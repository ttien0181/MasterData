import importlib.util
from pathlib import Path


def report_module():
    path = Path(__file__).resolve().parents[3] / 'scripts/report_api_only_acceptance.py'
    spec = importlib.util.spec_from_file_location('api_acceptance_report', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_report_does_not_label_transport_failure_as_ai_success():
    module = report_module()
    run = {'status': 'PASS', 'variant': 'evidence', 'run_id': 'one',
           'fixture': {'dataset': 'root/TC3/10k/seed-7', 'record_count': 30},
           'http_stages': [{'stage': 'upload', 'seconds': 1}],
           'machine_quality_before_truth_replay': {'candidate_count': 12, 'candidate_recall': .5},
           'final_quality_after_scripted_truth_review': {'records_preserved': 30},
           'telemetry': {'embedding_by_position': {'evidence': {'http_attempts': 2}},
                         'embedding_by_model_field': [], 'operations': [],
                         'llm_by_purpose': [{'purpose': 'PAIR_ASSESSMENT', 'provider': 'gemini',
                                            'model': 'test', 'http_attempts': 1, 'successes': 0,
                                            'statuses': {'TIMEOUT': 1}}]}}
    row = module.summarize_run(run)
    assert row['workflow_status'] == 'PASS'
    assert row['ai_http_attempts'] == 3
    assert row['llm_successes'] == 0
    assert row['llm_failures'] == 1
    assert row['machine_candidate_recall'] == .5
    assert row['final_after_truth_review']['records_preserved'] == 30
    assert row['worker_seconds'] is None
