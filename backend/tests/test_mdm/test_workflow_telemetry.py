from masterone.mdm.workflow_telemetry import StageTimer, summarize_embedding


def test_embedding_breakdown_keeps_model_field_and_stage_without_double_counting():
    from masterone.mdm.workflow_telemetry import embedding_breakdown
    receipts = [
        {'request_id': '1', 'event': 'HTTP_INTENT', 'position': 'evidence',
         'provider': 'openrouter', 'model': 'model-a', 'field': 'name'},
        {'request_id': '1', 'event': 'HTTP_RESPONSE', 'input_tokens': 5, 'latency_seconds': 2},
        {'request_id': '2', 'event': 'HTTP_INTENT', 'position': 'evidence',
         'provider': 'openrouter', 'model': 'model-a', 'field': 'address'},
        {'request_id': '3', 'event': 'HTTP_INTENT', 'position': 'retrieval',
         'provider': 'openrouter', 'model': 'model-b', 'field': 'name'},
    ]
    rows = embedding_breakdown(receipts + receipts)
    assert len(rows) == 3
    assert sum(row['http_attempts'] for row in rows) == 3
    assert sum(row['unresolved_intents'] for row in rows) == 2
    name = next(row for row in rows if row['model'] == 'model-a' and row['field'] == 'name')
    assert name['input_tokens'] == 5 and name['latency_seconds'] == 2
    assert all(row['model'] and row['provider'] and row['position'] for row in rows)


def test_timer_aggregates_repeated_stages_without_double_counting():
    tick = [10.]
    timer = StageTimer(clock=lambda: tick[0])
    tick[0] = 11.
    timer.progress('embedding')
    tick[0] = 13.
    timer.progress('embedding')
    tick[0] = 14.
    timer.progress('rules')
    tick[0] = 17.
    result = timer.snapshot()
    assert result['total_seconds'] == 7.
    assert result['stages'] == {'worker-setup': 1., 'embedding': 3., 'rules': 3.}


def test_embedding_retries_count_http_attempts_once_and_keep_unknown_usage():
    receipts = [
        {'batch_id': 'b', 'event': 'INTENT', 'external_requests': 0},
        {'batch_id': 'b', 'request_id': 'one', 'event': 'HTTP_INTENT', 'position': 'retrieval'},
        {'batch_id': 'b', 'request_id': 'one', 'event': 'HTTP_FAILED', 'position': 'retrieval',
         'input_tokens': None, 'api_cost_usd': None, 'error_code': 'EMBEDDING_HTTP_503', 'latency_seconds': 1},
        {'batch_id': 'b', 'request_id': 'two', 'event': 'HTTP_INTENT', 'position': 'retrieval'},
        {'batch_id': 'b', 'request_id': 'two', 'event': 'HTTP_RESPONSE', 'position': 'retrieval',
         'input_tokens': 12, 'api_cost_usd': .001, 'estimated_cost_usd': .0001, 'latency_seconds': 2},
        {'batch_id': 'b', 'event': 'SUCCESS', 'external_requests': 2, 'input_tokens': None},
        {'batch_id': 'c', 'request_id': 'interrupted', 'event': 'HTTP_INTENT', 'position': 'retrieval'},
    ]
    result = summarize_embedding(receipts)
    row = result['retrieval']
    assert row['http_attempts'] == 3
    assert row['responses'] == 2 and row['failed_responses'] == 1
    assert row['unresolved_intents'] == 1
    assert row['known_input_tokens'] == 12 and row['unknown_usage_attempts'] == 2
    assert row['input_tokens'] is None and row['api_cost_usd'] is None
    assert row['known_api_cost_usd'] == .001
    assert row['latency_seconds'] == 3
    assert result['evidence']['http_attempts'] == 0
    assert summarize_embedding(receipts + receipts) == result


def test_telemetry_endpoint_is_owned_and_run_scoped(client, workflow_run, monkeypatch):
    from masterone.config import get_settings
    from masterone.mdm.generation_staging import digest
    from masterone.mdm.models import ResolutionRun
    from masterone.mdm.workflow_models import WorkflowArtifact

    from tests.test_mdm.test_workflow_stages import run_one
    monkeypatch.setattr(get_settings(), 'llm_enabled', False)
    run_one(client)
    with client.session_factory.begin() as db:
        owner = db.get(ResolutionRun, workflow_run).owner_user_id
        other = ResolutionRun(owner_user_id=owner)
        db.add(other)
        db.flush()
        for run, identifier in [(workflow_run, 'ours'), (other.id, 'theirs')]:
            payload = {'event': 'HTTP_INTENT', 'request_id': identifier, 'position': 'retrieval'}
            db.add(WorkflowArtifact(run_id=run, phase='AGENT_ENCODER_USAGE', content_hash=digest(payload), payload=payload))
    response = client.get(f'/api/v1/mdm/runs/{workflow_run}/workflow/telemetry')
    assert response.status_code == 200, response.text
    result = response.json()
    assert result['embedding_by_position']['retrieval']['http_attempts'] == 1
    assert result['operations'][0]['telemetry']['attempts'][0]['total_seconds'] >= 0
    assert 'raw_fields' not in response.text and 'theirs' not in response.text
    assert client.get('/api/v1/mdm/runs/missing/workflow/telemetry').status_code == 404


def test_automatic_retry_preserves_failed_timing_without_inventing_queue_wait(client, workflow_run):
    import pytest
    from masterone.mdm.job_queue import claim_next, failed, ownership
    from masterone.mdm.models import ResolutionTask
    from masterone.mdm.workflow_jobs import execute_workflow

    from tests.test_mdm.test_workflow_stages import run_one
    claim = claim_next(client.session_factory, 'telemetry-retry-test', lease_seconds=300)
    with client.session_factory() as db:
        op_id = db.get(ResolutionTask, claim.task_id).operation_id
    def interrupted(*_):
        raise ValueError('TEST_INTERRUPTED')
    with ownership(claim):
        with pytest.raises(ValueError, match='TEST_INTERRUPTED'):
            execute_workflow(op_id, client.session_factory, interrupted)
    failed(client.session_factory, claim, 'TEST_INTERRUPTED')
    run_one(client)
    report = client.get(f'/api/v1/mdm/runs/{workflow_run}/workflow/telemetry').json()
    attempts = report['operations'][0]['telemetry']['attempts']
    assert {row['status'] for row in attempts} == {'FAILED', 'COMPLETED'}
    assert next(row for row in attempts if row['status'] == 'COMPLETED')['queue_wait_seconds'] is None


def test_telemetry_hides_another_owners_existing_run(client, workflow_run):
    credentials = {'email': 'telemetry-other@example.com', 'password': 'separate-local-test-password'}
    assert client.post('/api/v1/auth/register', json=credentials).status_code == 201
    assert client.post('/api/v1/auth/login', json=credentials).status_code == 200
    assert client.get(f'/api/v1/mdm/runs/{workflow_run}/workflow/telemetry').status_code == 404
