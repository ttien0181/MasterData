import pytest
from masterone.mdm.data_profile import build_profile
from masterone.mdm.models import ResolutionFile, ResolutionRecord
from masterone.mdm.workflow_artifacts import seal, warm_profile
from masterone.mdm.workflow_models import WorkflowArtifact
from sqlalchemy import event


def test_profile_counts_present_raw_values_and_unique_flagged_records():
    rows = [
        {'source_system': 'CRM', 'raw_fields': {'name': 'An', 'email': 'bad', 'phone': '  '},
         'normalized_fields': {}, 'quality_flags': ['INVALID_EMAIL_FORMAT', 'MISSING_NAME']},
        {'source_system': 'ERP', 'raw_fields': {'name': None, 'email': '', 'phone': '090'},
         'normalized_fields': {}, 'quality_flags': []},
        {'source_system': 'CRM', 'raw_fields': {'name': 'Binh'},
         'normalized_fields': {}, 'quality_flags': None, 'extra_fields': {'custom_column': 'value'}},
    ]
    profile = build_profile(iter(rows))
    assert profile['record_count'] == 3
    assert profile['records_with_quality_flags'] == 1
    assert profile['field_completeness']['name'] == {'present': 2, 'missing': 1}
    assert profile['field_completeness']['email'] == {'present': 1, 'missing': 2}
    assert profile['field_completeness']['phone'] == {'present': 1, 'missing': 2}
    assert profile['field_completeness']['dob'] == {'present': 0, 'missing': 3}
    assert profile['source_counts'] == {'CRM': 2, 'ERP': 1}
    assert 'custom_column' in profile['schema']


@pytest.fixture
def profiled_run(client, workflow_run):
    with client.session_factory.begin() as db:
        file_id = db.query(ResolutionFile).filter_by(run_id=workflow_run).first().id
        db.query(ResolutionRecord).filter_by(run_id=workflow_run).delete()
        rows = []
        for i in range(125):
            raw = {'name': f'Person {i}', 'email': 'a@example.test' if i % 2 == 0 else ' ', 'phone': None}
            if i == 124:
                raw['late_column'] = 'yes'
            row = {'source_system': 'CRM', 'raw_fields': raw, 'normalized_fields': {},
                   'quality_flags': ['INVALID_EMAIL_FORMAT', 'NO_CONTACT_POINT'] if i < 25 else []}
            rows.append(row)
            db.add(ResolutionRecord(id=f'{i:036}', run_id=workflow_run, file_id=file_id,
                                    external_id=str(i), extra_fields={}, **row))
    payload = {'records': rows, 'profile': {'record_count': 125, 'unit': 'records',
               'quality_flags': {'INVALID_EMAIL_FORMAT': 25, 'NO_CONTACT_POINT': 25},
               'source_counts': {'CRM': 125}, 'field_quality': {}}}
    artifact_id = seal(client.session_factory, workflow_run, 'PREPARE', payload)
    return workflow_run, artifact_id


def test_data_pages_cover_all_records_and_keep_global_profile(client, profiled_run):
    run, artifact_id = profiled_run
    url = f'/api/v1/mdm/runs/{run}/workflow/data'
    first = client.get(url).json()
    assert len(first['samples']['items']) == 10
    assert first['samples']['total'] == 125
    assert first['samples']['offset'] == 0
    assert 'late_column' in first['schema']
    assert first['profile']['records_with_quality_flags'] == 25
    assert first['profile']['field_completeness']['email'] == {'present': 63, 'missing': 62}
    last = client.get(url + '?offset=120&limit=10').json()
    assert [r['external_id'] for r in last['samples']['items']] == ['120', '121', '122', '123', '124']
    assert last['samples']['next_cursor'] is None
    assert last['profile'] == first['profile']
    assert last['schema'] == first['schema']
    second = client.get(url + '?cursor=' + first['samples']['next_cursor']).json()
    assert second['samples']['items'][0]['external_id'] == '10'
    assert client.get(url + '?offset=-1').status_code == 422
    assert client.get(url + '?limit=101').status_code == 422
    with client.session_factory() as db:
        # Reading an old profile never rewrites a sealed artifact.
        assert 'field_completeness' not in db.get(WorkflowArtifact, artifact_id).payload['profile']


def test_data_profile_rejects_tampered_artifact(client, profiled_run):
    run, artifact_id = profiled_run
    with client.session_factory.begin() as db:
        db.query(WorkflowArtifact).filter_by(id=artifact_id).update({'content_hash': 'broken'})
    response = client.get(f'/api/v1/mdm/runs/{run}/workflow/data')
    assert response.status_code == 409
    assert response.json()['detail']['code'] == 'WORKFLOW_ARTIFACT_INVALID'


def test_prepare_seals_small_profile_and_pages_never_fetch_parent_payload(client, profiled_run):
    run, old_id = profiled_run
    with client.session_factory() as db:
        payload = dict(db.get(WorkflowArtifact, old_id).payload)
    payload['profile'] = build_profile(payload['records'])
    new_id = seal(client.session_factory, run, 'PREPARE', payload)
    with client.session_factory() as db:
        sidecar = db.query(WorkflowArtifact).filter_by(run_id=run, phase='PROFILE').one_or_none()
        assert sidecar is not None
        assert sidecar.payload['prepare_artifact_id'] == new_id
    statements = []
    engine = client.session_factory.kw['bind']

    def capture(_connection, _cursor, statement, parameters, _context, _many):
        statements.append((statement, parameters))

    event.listen(engine, 'before_cursor_execute', capture)
    try:
        response = client.get(f'/api/v1/mdm/runs/{run}/workflow/data?offset=120')
    finally:
        event.remove(engine, 'before_cursor_execute', capture)
    assert response.status_code == 200
    assert response.json()['profile']['records_with_quality_flags'] == 25
    assert not any('mdm_workflow_artifacts.payload' in sql and 'PREPARE' in parameters
                   for sql, parameters in statements)


@pytest.mark.parametrize('corruption', ['hash', 'reference'])
def test_corrupt_matching_profile_is_not_silently_replaced(client, profiled_run, corruption):
    run, parent_id = profiled_run
    with client.session_factory() as db:
        parent = db.get(WorkflowArtifact, parent_id)
        payload = {'prepare_artifact_id': parent_id, 'prepare_content_hash': parent.content_hash,
                   'profile': build_profile(parent.payload['records'])}
    if corruption == 'reference':
        payload['prepare_content_hash'] = 'wrong'
    sidecar_id = seal(client.session_factory, run, 'PROFILE', payload)
    if corruption == 'hash':
        with client.session_factory.begin() as db:
            db.query(WorkflowArtifact).filter_by(id=sidecar_id).update({'content_hash': 'broken'})
    assert client.get(f'/api/v1/mdm/runs/{run}/workflow/data').status_code == 409


def test_profile_and_prepare_seal_roll_back_together(client, profiled_run, monkeypatch):
    from masterone.mdm import workflow_artifacts
    run, parent_id = profiled_run
    with client.session_factory() as db:
        payload = dict(db.get(WorkflowArtifact, parent_id).payload)
    payload['profile'] = build_profile(payload['records'])
    original_digest = workflow_artifacts.digest

    def fail_profile(data):
        if 'prepare_artifact_id' in data:
            raise RuntimeError('summary seal failed')
        return original_digest(data)

    monkeypatch.setattr(workflow_artifacts, 'digest', fail_profile)
    with pytest.raises(RuntimeError, match='summary seal failed'):
        seal(client.session_factory, run, 'PREPARE', payload)
    with client.session_factory() as db:
        assert db.query(WorkflowArtifact).filter_by(run_id=run, phase='PREPARE').count() == 1
        assert db.query(WorkflowArtifact).filter_by(run_id=run, phase='PROFILE').count() == 0


def test_legacy_profile_warming_is_idempotent_and_preserves_parent(client, profiled_run):
    run, parent_id = profiled_run
    with client.session_factory() as db:
        original = db.get(WorkflowArtifact, parent_id)
        original_hash, original_payload = original.content_hash, original.payload
    warm_profile(client.session_factory, run)
    warm_profile(client.session_factory, run)
    with client.session_factory() as db:
        parent = db.get(WorkflowArtifact, parent_id)
        assert (parent.content_hash, parent.payload) == (original_hash, original_payload)
        assert db.query(WorkflowArtifact).filter_by(run_id=run, phase='PROFILE').count() == 1
    response = client.get(f'/api/v1/mdm/runs/{run}/workflow/data')
    assert response.status_code == 200
    assert response.json()['profile']['records_with_quality_flags'] == 25


def test_legacy_warming_verifies_full_parent_before_first_summary(client, profiled_run):
    run, parent_id = profiled_run
    with client.session_factory.begin() as db:
        parent = db.get(WorkflowArtifact, parent_id)
        altered = {**parent.payload, 'records': []}
        db.query(WorkflowArtifact).filter_by(id=parent_id).update({'payload': altered})
    with pytest.raises(ValueError, match='WORKFLOW_ARTIFACT_INVALID'):
        warm_profile(client.session_factory, run)
    with client.session_factory() as db:
        assert db.query(WorkflowArtifact).filter_by(run_id=run, phase='PROFILE').count() == 0


def test_analysis_is_available_before_prepare_and_covers_the_entire_upload(client, profiled_run):
    run, _ = profiled_run
    with client.session_factory.begin() as db:
        db.query(WorkflowArtifact).filter_by(run_id=run).delete()
    response = client.get(f'/api/v1/mdm/runs/{run}/workflow/data?include_records=false')
    assert response.status_code == 200
    data = response.json()
    assert data['samples'] == {'items': [], 'total': 125, 'offset': 0, 'next_cursor': None}
    assert data['profile']['record_count'] == 125
    assert data['profile']['source_counts'] == {'CRM': 125}
    assert data['profile']['records_with_quality_flags'] == 25
    assert data['profile']['field_completeness']['email'] == {'present': 63, 'missing': 62}
    assert 'late_column' in data['schema']


def test_record_pagination_never_loads_or_scans_profile(client, profiled_run, monkeypatch):
    from masterone.mdm import data_profile, workflow_artifacts
    run, _ = profiled_run

    def forbidden(*args, **kwargs):
        pytest.fail('record pagination must not load artifacts or scan the complete input')

    monkeypatch.setattr(data_profile, 'build_profile', forbidden)
    monkeypatch.setattr(workflow_artifacts, 'load', forbidden)
    response = client.get(f'/api/v1/mdm/runs/{run}/workflow/data?offset=10&include_profile=false')
    assert response.status_code == 200
    data = response.json()
    assert data['profile'] is None
    assert len(data['samples']['items']) == 10
    assert data['samples']['total'] == 125
    assert data['samples']['items'][0]['external_id'] == '10'
