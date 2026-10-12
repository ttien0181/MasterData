from types import SimpleNamespace

from masterone.mdm.workflow_pair_advice import advice_payload, advice_route


def pair(states, **kwargs):
    result = {'decision': 'REVIEW', 'primary_group': 'C',
              'field_evidence': [{'field': f, 'state': s} for f, s in states.items()], **kwargs}
    return SimpleNamespace(decision='UNDECIDED', assessment=result)


def test_routes_only_informative_ambiguous_pairs():
    assert advice_route(pair({'name': 'HS', 'address': 'EX'})) == 'ELIGIBLE'
    assert advice_route(pair({'name': 'MI', 'address': 'EX'})) == 'NO_USEFUL_INFORMATION'
    assert advice_route(pair({'name': 'DF', 'address': 'DF'})) == 'RULE_CLEAR_DIFFERENT'
    assert advice_route(pair({'name': 'HS', 'phone': 'EX'}, primary_group='A')) == 'RULE_CLEAR'
    assert advice_route(pair({'name': 'HS', 'phone': 'EX'}, decision='BLOCKED')) == 'HARD_CONSTRAINT'
    assert advice_route(pair({'name': 'HS', 'phone': 'EX'}, soft_evaluation={
        'status': 'EVALUATED', 'recommendation': 'LIKELY_DIFFERENT_PERSON'})) == 'MODEL_CLEAR'


def test_projection_never_sends_contact_identifier_birthdate_or_gold():
    record = {'raw_fields': {'gold': 'private'}, 'normalized_fields': {
        'name': 'NAME', 'address': 'ADDRESS', 'phone': 'private-phone', 'email': 'private-email',
        'government_id': 'private-id', 'dob': 'private-birthdate', 'entity_id': 'gold'}}
    payload = advice_payload(pair({'name': 'HS', 'phone': 'EX'}).assessment, record, record, 'pair-1')
    assert payload['left'] == {'name': 'NAME', 'address': 'ADDRESS'}
    assert 'private' not in str(payload) and 'gold' not in str(payload)


def test_queued_advice_keeps_all_decisions_and_baselines(client, workflow_run, monkeypatch):
    from tests.test_mdm.test_workflow_contract import request
    from tests.test_mdm.test_workflow_stages import read, ready_decisions, run_one
    from masterone.mdm.workflow_models import WorkflowPair
    from masterone.config import get_settings
    state = ready_decisions(client, workflow_run)
    url = f'/api/v1/mdm/runs/{workflow_run}/workflow'
    assert client.post(url + '/reassess', json=request(state['workflow_revision'])).status_code == 202
    run_one(client)
    state = read(client, workflow_run)
    with client.session_factory.begin() as db:
        pairs = db.query(WorkflowPair).filter_by(run_id=workflow_run).all()
        before = {p.id: (p.decision, p.revision, p.baseline) for p in pairs}
        # Force one genuinely ambiguous review fixture, without granting merge eligibility.
        p = pairs[0]
        p.assessment = {**p.assessment, 'primary_group': 'C', 'decision': 'REVIEW',
                        'field_evidence': [{'field': 'name', 'state': 'HS'}, {'field': 'address', 'state': 'EX'}]}
        pair_id = p.id
    calls = []
    def generate(*args, **kwargs):
        calls.append(args[1])
        return {'output': {'pair_token': args[1]['pair_token'], 'recommendation': 'INSUFFICIENT_BASIS',
            'evidence': [{'field':'name','state':'HS'}], 'reason_codes':['SPARSE_SIGNALS'],
            'limitation_codes':['SOURCE_NOT_VERIFIED','HUMAN_REVIEW_REQUIRED']},
            'provider': 'test', 'model_version': 'test', 'request_id': 'test'}
    monkeypatch.setattr('masterone.mdm.workflow_pair_advice.generate', generate)
    response = client.post(url + '/pair-advice', json=request(state['workflow_revision'], pair_ids=[pair_id], limit=1))
    assert response.status_code == 202, response.text
    run_one(client)
    assert len(calls) == 1
    with client.session_factory() as db:
        for p in db.query(WorkflowPair).filter_by(run_id=workflow_run):
            assert (p.decision, p.revision, p.baseline) == before[p.id]
        assert db.get(WorkflowPair, pair_id).advice['recommendation'] == 'INSUFFICIENT_BASIS'
