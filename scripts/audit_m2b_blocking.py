"""Read-only M2-B audit probes of existing production blocking; no retrieval redesign."""
import json
import sys
from dataclasses import asdict
from hashlib import sha256
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend/src'))

from masterone.mdm.pipeline.candidate_engine import (  # noqa: E402
    CandidateConfig,
    CandidateEngine,
    CandidateWorkloadExceededError,
)
from masterone.mdm.pipeline.tfidf_blocking import _match_windows, _partition_key  # noqa: E402


def row(number, source='CRM', shared=False):
    return {'id': f'audit-{number:05}', 'source_system': source, 'normalized_fields': {
        'phones': [{'value': 'synthetic-audit-contact', 'quality': 'VALID', 'shared': shared}]}}


def main():
    out = ROOT / 'artifacts/advanced-m2b-20261014/audit-01'
    out.mkdir(parents=True, exist_ok=False)
    config = CandidateConfig(include_grams=False, include_tfidf=False)
    oversized = CandidateEngine(config).generate([row(i) for i in range(21)])
    assert len(oversized.pairs) == 0
    assert oversized.metrics['skipped_pair_observations'] == 210
    small = CandidateEngine(config)
    small.add_records([row(0), row(1)])
    small.add_records([row(2, 'ERP')])
    union = small.finalize()
    assert len(union.pairs) == 3
    assert ('audit-00000', 'audit-00002') in union.pairs
    hubs = CandidateEngine(config).generate([row(0, shared=True), row(1, shared=True)])
    assert len(hubs.pairs) == 1  # blocking VALID contact is not scoring anchor eligibility
    duplicate_rejected = False
    try:
        CandidateEngine(config).generate([row(0), row(0)])
    except ValueError as error:
        duplicate_rejected = str(error) == 'Duplicate snapshot record ID'
    assert duplicate_rejected
    exceeded = False
    try:
        CandidateEngine(CandidateConfig(include_grams=False, include_tfidf=False, max_unique_pairs=1)).generate([row(i) for i in range(3)])
    except CandidateWorkloadExceededError:
        exceeded = True
    assert exceeded
    left = {'id': 'left', 'normalized_fields': {'name': 'synthetic name', 'dob': '1980-01-01'}}
    right = {'id': 'right', 'normalized_fields': {'name': 'synthetic name', 'dob': '1981-01-01'}}
    assert _partition_key(left) != _partition_key(right)
    window_rows = [{'id': f'w-{i:05}', 'normalized_fields': {'name': 'synthetic'}} for i in range(6001)]
    windows = _match_windows(window_rows)
    assert not any({'w-00000', 'w-06000'} <= {r['id'] for r in w} for w in windows)
    paths = [ROOT / 'MASTERONE_ADVANCED_SPEC_20261014.md', ROOT / 'docker-compose.yml']
    paths += [ROOT / f'backend/src/masterone/mdm/{rel}' for rel in (
        'pipeline/candidate_engine.py', 'pipeline/candidate_planner.py', 'pipeline/tfidf_blocking.py',
        'pipeline/stage_runner.py', 'pipeline/scoring_engine.py', 'pipeline/matching.py',
        'pipeline/policy_engine.py', 'pipeline/decision_semantics.py', 'pipeline/pair_assessment.py',
        'resolution_service.py', 'workflow_jobs.py', 'workflow_assessment.py', 'workflow_models.py',
        'workflow_review.py', 'workflow_review_rules.py', 'matching_agents/workflow_agents.py')]
    pins = {str(p.relative_to(ROOT)): sha256(p.read_bytes()).hexdigest() for p in paths}
    receipt = {
        'scope': 'CURRENT_CODE_AUDIT_SYNTHETIC_PROBES_NOT_E0_E3',
        'default_candidate_config': asdict(CandidateConfig()),
        'oversized_valid_contact_21': {'unique_pairs': 0, 'potential_observations_skipped': 210, 'metrics': oversized.metrics},
        'cross_chunk_same_and_cross_source_union': {'unique_pairs': len(union.pairs), 'all_provenance': {','.join(k): sorted(v) for k, v in union.pairs.items()}},
        'explicit_shared_flag_valid_contact_enters_blocking': True,
        'duplicate_snapshot_id_rejected': duplicate_rejected,
        'global_budget_raises_no_partial_success': exceeded,
        'legacy_dob_partition_can_separate_same_name': True,
        'legacy_overlapping_windows_not_global': {'records': 6001, 'window_sizes': list(map(len, windows)), 'first_last_co_window': False},
        'source_hashes': pins,
        'product_code_changed': False,
        'production_database_accessed': False,
    }
    (out / 'probes.json').write_text(json.dumps(receipt, indent=2) + '\n', encoding='utf-8')
    (out / 'manifest.json').write_text(json.dumps({'files': [{'path': 'probes.json', 'sha256': sha256((out / 'probes.json').read_bytes()).hexdigest()}]}, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'output': str(out), 'probe_groups': 7, 'assertions': 'PASS', 'scope': receipt['scope']}))


if __name__ == '__main__':
    main()
