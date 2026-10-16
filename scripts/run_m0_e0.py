"""Reproducible diagnostic E0 using the production API and leased workflow jobs.

The approved small local dataset and smallest Drive TC1 bundle are supported. No business algorithms,
review decisions, thresholds, application schemas or live configuration change.
All application writes belong to a newly created SQLite sandbox. Gold is read
only after source-only predictions and membership have been frozen.
"""
import argparse
import csv
import functools
import hashlib
import importlib.metadata
import io
import json
import os
import platform
import subprocess
import sys
import time
from collections import Counter
from dataclasses import asdict
from datetime import UTC, datetime
from itertools import combinations
from pathlib import Path

from artifact_retention import finish_run

ROOT = Path(__file__).resolve().parents[1]


def file_hash(path):
    h = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + '\n', encoding='utf8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--dataset', choices=('small', 'tc1'), default='small')
    parser.add_argument('--keep-state', action='store_true', help='Retain sandbox SQLite for debugging/resume')
    args = parser.parse_args()
    output = args.output.resolve()
    allowed_root = (ROOT / 'artifacts/advanced-m0-20261014').resolve()
    if not output.is_relative_to(allowed_root):
        parser.error('Output must be inside artifacts/advanced-m0-20261014')
    output.mkdir(parents=True, exist_ok=False)
    from run_merge_baseline import isolated_environment
    env = isolated_environment(output / 'state')
    env.update({
        'MASTERONE_ENV_FILE': str(output / 'absent.env'),
        'SESSION_WORKFLOW_ENABLED': 'true', 'PAIR_ASSESSMENT_AUTO_ENABLED': 'true',
        'WORKFLOW_PARQUET_ENABLED': 'false', 'PAIR_AUTOMATION_ENABLED': 'false',
        'PAIR_REVIEW_MODEL_PATH': '', 'LLM_PAIR_ASSESSMENT_ENABLED': 'false',
        'WORKFLOW_ARTIFACT_DIR': str(output / 'state/workflow-artifacts'),
        'PYTHONDONTWRITEBYTECODE': '1',
    })
    os.environ.update(env)
    sys.path[:0] = [str(ROOT / 'backend/src'), str(ROOT / 'backend'), str(ROOT / 'benchmark')]
    from fastapi.testclient import TestClient
    from m0_evaluation import evaluate_pairs, ratio
    from masterone.auth.models import User
    from masterone.config import get_settings
    from masterone.db.base import Base
    from masterone.db.session import get_session
    from masterone.main import app
    from masterone.mdm.engine_adapter import engine_context
    from masterone.mdm.export_stream import rss_peak
    from masterone.mdm.job_queue import claim_next, ownership
    from masterone.mdm.models import ResolutionEngineSelection, ResolutionRecord, ResolutionRun, ResolutionTask
    from masterone.mdm.pipeline.candidate_engine import CandidateConfig, CandidateEngine
    from masterone.mdm.pipeline.golden_v2 import record_payload
    from masterone.mdm.pipeline.hard_constraints import check_all
    from masterone.mdm.pipeline.scoring_engine import ScoringConfig, ScoringEngine
    from masterone.mdm.pipeline.stage_runner import StageRunner
    from masterone.mdm.workflow_assessment_reads import effective_result
    from masterone.mdm.workflow_jobs import execute_workflow
    from masterone.mdm.workflow_models import WorkflowArtifact, WorkflowAssessment, WorkflowPair
    from masterone.mdm.workflow_review import eligibility
    from masterone.mdm.workflow_review_rules import review_required
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from standalone.contracts.labels import PairLabel
    from standalone.evaluation.clusters import cluster_metrics

    if args.dataset == 'small':
        sources = [ROOT / 'benchmark/datasets/masterone_test/records.csv',
                   ROOT / 'benchmark/datasets/masterone_test/pairs.csv']
    else:
        import yaml
        dataset = ROOT / 'artifacts/cluster-improvements-20261013/datasets/TC1_easy_match/10k/seed-7'
        manifest_path = dataset / 'manifest.yaml'
        remote_manifest = ROOT / 'artifacts/advanced-m0-20261014/drive-metadata/manifests/TC1_easy_match/10k/seed-7/manifest.yaml'
        if file_hash(manifest_path) != file_hash(remote_manifest):
            raise ValueError('Local manifest differs from downloaded Drive manifest')
        declared = yaml.safe_load(manifest_path.read_text(encoding='utf8'))
        sources = [manifest_path, remote_manifest]
        for rel, digest in declared['file_hashes'].items():
            path = (dataset / rel).resolve()
            if not path.is_relative_to(dataset.resolve()) or file_hash(path) != digest:
                raise ValueError(f'Dataset manifest verification failed: {rel}')
            sources.append(path)
    pins = {str(p.relative_to(ROOT)): file_hash(p) for p in sources}
    code_paths = sorted((ROOT / 'backend/src/masterone').rglob('*.py')) + sorted(
        (ROOT / 'backend/src/masterone/mdm/config').glob('*.yaml'))
    code_paths += [ROOT / 'backend/src/masterone/mdm/input_contract_v1.json',
                   ROOT / 'scripts/run_m0_e0.py', ROOT / 'scripts/m0_evaluation.py',
                   ROOT / 'docs/advanced-matching/evaluation-protocol.md']
    code_pins = {p.relative_to(ROOT).as_posix(): file_hash(p) for p in code_paths}
    settings = get_settings()
    if not settings.active_mdm_database_url.startswith('sqlite:///'):
        raise RuntimeError('E0 requires an owned SQLite sandbox')
    if settings.r2_enabled or settings.llm_enabled or settings.pair_automation_enabled:
        raise RuntimeError('External storage/model or automation enabled')
    config = {'flags': {k: getattr(settings, k) for k in type(settings).model_fields if k.endswith('_enabled')},
              'candidate': asdict(CandidateConfig()), 'scoring': asdict(ScoringConfig()),
              'database': 'owned SQLite file; fresh Base.metadata.create_all; no live migration',
              'worker_execution': 'production claim/ownership/execute_workflow in harness process',
              'gold_access_order': 'after prediction/membership hashes',
              'rule_advisory_positive': 'MERGEABLE and manual_allowed; no auto-merge assertion',
              'final_routing_positive': 'MERGEABLE, triage_status != REVIEW and auto_allowed',
              'schema_projection': ['source_record_id', 'source_system', 'name', 'phone', 'email', 'address'],
              'split': 'existing TEST-only; no training/calibration/tuning', 'review_actions': 0}
    config['dataset'] = args.dataset
    raw_identity_keys = {}
    if args.dataset == 'tc1':
        from qa_dataset_adapter import FIELDS, decoded_rows
        config['schema_projection'] = list(FIELDS)
        config['split'] = 'publisher synthetic B1 evaluation pairs; diagnostic only; no new split'
    write_json(output / 'config-before-labels.json', config)
    # Project source-only values, preserving every record and the declared source ID.
    raw = []
    if args.dataset == 'small':
        with sources[0].open(encoding='utf-8-sig', newline='') as handle:
            raw = list(csv.DictReader(handle))
        if len(raw) != 4213 or len({r['id'] for r in raw}) != len(raw):
            raise ValueError('Approved dataset size or raw-ID uniqueness changed')
        raw = [{'source_record_id': r['id'], 'source_system': r['source_system'],
                'name': r['full_name'], 'phone': r['phone_number'],
                'email': r['email_address'], 'address': r['raw_address']} for r in raw]
    else:
        catalog = yaml.safe_load((dataset / 'raw/source_mapping.yaml').read_text(encoding='utf8'))
        for spec in catalog['files']:
            path = (dataset / spec['path']).resolve()
            if not path.is_relative_to((dataset / 'raw').resolve()):
                raise ValueError('Source mapping escapes raw directory')
            rows = iter(decoded_rows(path, spec))
            for _ in range(spec['header_row']):
                next(rows)
            header = next(rows)
            for values in rows:
                if len(values) != len(header):
                    raise ValueError('Malformed raw row; no silent exclusion')
                original = dict(zip(header, values, strict=True))
                row = {field: original[spec['columns'][field]] for field in FIELDS}
                identity = (row['source_system'], row['source_record_id'])
                if identity in raw_identity_keys:
                    raise ValueError('Ambiguous source ID across batches')
                raw_identity_keys[identity] = f"{identity[0]}:{identity[1]}:{spec['batch_id']}"
                raw.append(row)
        if len(raw) != declared['actual_records']:
            raise ValueError('Raw count differs from manifest')
    stream = io.StringIO(newline='')
    writer = csv.DictWriter(stream, fieldnames=config['schema_projection'], lineterminator='\n')
    writer.writeheader()
    for r in raw:
        writer.writerow(r)
    projected = stream.getvalue().encode('utf8')
    (output / 'source-only-input.csv').write_bytes(projected)
    del raw, projected

    engine = create_engine(settings.active_mdm_database_url, connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    def session_override():
        with factory() as session:
            yield session
    app.dependency_overrides[get_session] = session_override
    app.state.session_factory = factory
    # No TestClient lifespan: it would apply startup migrations, unnecessary here.
    client = TestClient(app)
    client.headers.update({'Origin': 'http://localhost:3000'})
    credentials = {'email': 'm0-sandbox@example.com', 'password': 'm0-owned-sandbox-test-password'}
    def checked(response, codes=(200, 202)):
        if response.status_code not in codes:
            raise RuntimeError(f'HTTP {response.status_code}: {response.text[:1200]}')
        return response.json()
    checked(client.post('/api/v1/auth/register', json=credentials), (201,))
    with factory.begin() as db:
        db.query(User).filter_by(email_normalized=credentials['email']).one().role = 'STEWARD'
    login = checked(client.post('/api/v1/auth/login', json=credentials))
    client.headers['X-CSRF-Token'] = login['csrf_token']

    stages, nested = [], []
    def stage(name, function):
        started = time.perf_counter()
        print('START', name, flush=True)
        result = function()
        stages.append({'stage': name, 'wall_seconds': time.perf_counter() - started,
                       'process_peak_rss_bytes': rss_peak()})
        print('DONE', name, round(stages[-1]['wall_seconds'], 3), flush=True)
        return result
    def instrument(owner, name, label):
        original = getattr(owner, name)
        @functools.wraps(original)
        def measured(*pos, **kw):
            started = time.perf_counter()
            try:
                return original(*pos, **kw)
            finally:
                nested.append({'stage': label, 'wall_seconds': time.perf_counter() - started,
                               'process_peak_rss_bytes': rss_peak()})
        setattr(owner, name, measured)
    original_init = StageRunner.__init__
    def local_init(self, run_id, attempt_id, output_dir=None, db_url=None):
        return original_init(self, run_id, attempt_id,
                             output_dir=output / 'stages' / attempt_id, db_url=db_url)
    StageRunner.__init__ = local_init
    instrument(CandidateEngine, 'generate', 'candidate-generation')
    instrument(ScoringEngine, 'write_scores', 'rule-scoring')
    instrument(StageRunner, 'run_prepare', 'prepare-core')
    import masterone.mdm.workflow_assessment as assessment_module
    instrument(assessment_module, 'compute_assessment', 'rule-assessment')
    def worker():
        claim = claim_next(factory, 'm0-e0-owned-worker', lease_seconds=3600, timeout_seconds=3600)
        if claim is None:
            raise RuntimeError('Expected queued owned workflow task')
        with factory() as db:
            oid = db.get(ResolutionTask, claim.task_id).operation_id
        with ownership(claim):
            execute_workflow(oid, factory, lambda *_: None)

    total_start = time.perf_counter()
    with (output / 'source-only-input.csv').open('rb') as h:
        upload = stage('import-api', lambda: checked(client.post('/api/v1/mdm/runs',
            data={'workflow_version': '2'}, files={'files': ('source-only.csv', h, 'text/csv')}), (201,)))
    rid = upload['run_id']
    root = f'/api/v1/mdm/runs/{rid}/workflow'
    def state():
        return checked(client.get(root))
    serial = [0]
    def mutation(**kw):
        serial[0] += 1
        w = state()
        return {'request_id': f'm0-{serial[0]}', 'expected_workflow_revision': w['workflow_revision'],
                'expected_assessment_id': w.get('assessment_id'), **kw}
    stage('prepare-worker', worker)
    stage('enqueue-decide-api', lambda: checked(client.post(root + '/start-decide', json=mutation())))
    stage('decide-and-assessment-worker', worker)
    freeze_start = time.perf_counter()
    with factory() as db:
        record_rows = db.query(ResolutionRecord).filter_by(run_id=rid).order_by(ResolutionRecord.id).all()
        records = {r.id: record_payload(r) for r in record_rows}
        internal_by_external = {(r.external_id if args.dataset == 'small' else
            raw_identity_keys[(r.source_system, r.external_id)]): r.id for r in record_rows}
        if len(internal_by_external) != len(records):
            raise ValueError('Label join on raw ID would be ambiguous')
        allowed_source_extras = {'source_updated_at', 'is_active'} if args.dataset == 'tc1' else set()
        if any(r.benchmark_entity_id is not None or set(r.extra_fields or {}) - allowed_source_extras
               for r in record_rows):
            raise ValueError('Gold/annotation present in matcher payload')
        config['importer_raw_source_extra_keys'] = sorted(allowed_source_extras)
        pairs = []
        for row in db.query(WorkflowPair).filter_by(run_id=rid).order_by(WorkflowPair.id):
            p = effective_result(row)
            pairs.append({'pair_id': row.id, 'left_id': p['left_id'], 'right_id': p['right_id'],
                'decision': p['decision'], 'triage_status': p.get('triage_status'),
                'primary_group': p.get('primary_group'), 'subgroup': p.get('subgroup'),
                'reason': p.get('reason'), 'score': p['score'], 'rule_conclusion': p.get('rule_conclusion'),
                'constraint_codes': p.get('constraint_codes', []), 'eligibility': eligibility(row),
                'original_decision': row.baseline['decision'],
                'original_group': row.baseline.get('primary_group'), 'overlay': row.decision,
                'review_required': review_required(row.decision, p)})
        selection = db.get(ResolutionEngineSelection, rid)
        active = db.query(WorkflowAssessment).filter_by(run_id=rid, active=True).one()
        config['selected_engine_profile'] = selection.configuration
        config['effective_assessment_configuration'] = active.configuration
        payload = db.query(WorkflowArtifact).filter_by(run_id=rid, phase='DECIDE').one().payload
        candidate_metrics = payload['candidate_metrics']
        scoring_metrics = payload['scoring_metrics']
        actual_records = db.get(ResolutionRun, rid).record_count
    if any(p['overlay'] != 'UNDECIDED' for p in pairs):
        raise ValueError('Unexpected review decision in pre-HITL baseline')
    with (output / 'predictions-pre-hitl.jsonl').open('w', encoding='utf8') as h:
        for p in pairs:
            h.write(json.dumps(p, ensure_ascii=False) + '\n')
    write_json(output / 'source-id-map.json', [
        {'record_id': r.id, 'source_record_id': r.external_id, 'source_system': r.source_system}
        for r in record_rows])
    write_json(output / 'config.json', config)
    prediction_hash = file_hash(output / 'predictions-pre-hitl.jsonl')
    snapshot_wall = time.perf_counter() - freeze_start
    # No review actions: publish only the region the unchanged workflow permits.
    w = state()
    stage('enqueue-preview-api', lambda: checked(client.post(root + '/cluster-preview', json=mutation(
        expected_decision_revision=w['decision_revision'], allow_partial=True))))
    stage('cluster-preview-worker', worker)
    w = state()
    stage('enqueue-publication-api', lambda: checked(client.post(root + '/finalize-decisions', json=mutation(
        expected_decision_revision=w['decision_revision'], allow_partial=True))))
    stage('cluster-publication-worker', worker)
    def pages(endpoint):
        result, cursor = [], None
        while True:
            page = checked(client.get(root + endpoint, params={'limit': 100, **({'cursor': cursor} if cursor else {})}))
            result.extend(page['items'])
            cursor = page['next_cursor']
            if cursor is None:
                return result
    groups = stage('cluster-reads-api', lambda: pages('/clusters'))
    goldens = stage('golden-draft-reads-api', lambda: pages('/golden'))
    memberships = {record: group['cluster_id'] for group in groups for record in group['member_record_ids']}
    if len(memberships) != sum(len(g['member_record_ids']) for g in groups):
        raise ValueError('Duplicate published membership')
    write_json(output / 'memberships-pre-hitl.json', groups)
    membership_hash = file_hash(output / 'memberships-pre-hitl.json')
    total_wall = time.perf_counter() - total_start
    serving_peak = rss_peak()
    # Open independent label file only after immutable prediction and membership snapshots.
    eval_start = time.perf_counter()
    labels, label_pairs, label_strata = {}, {}, {}
    seen = set()
    pair_by_endpoints = {tuple(sorted((p['left_id'], p['right_id']))): p for p in pairs}
    if args.dataset == 'small':
        with sources[1].open(encoding='utf-8-sig', newline='') as handle:
            evaluation_rows = list(csv.DictReader(handle))
    else:
        import pyarrow.parquet as pq
        evaluation_rows = []
        for row in pq.read_table(dataset / 'truth/answer_keys/evaluation_pairs.parquet').to_pylist():
            evaluation_rows.append({'id_a': row['record_key_a'], 'id_b': row['record_key_b'],
                'is_match': {'MATCH': '1', 'NON_MATCH': '0', 'UNKNOWN': 'UNKNOWN'}[row['label']],
                'case_type': row['relation_type'], 'difficulty': row['difficulty'],
                'hard_negative_type': ''})
    for row in evaluation_rows:
        endpoints = tuple(sorted((internal_by_external[row['id_a']], internal_by_external[row['id_b']])))
        if endpoints[0] == endpoints[1] or endpoints in seen:
            raise ValueError('Self/duplicate label pair')
        seen.add(endpoints)
        pid = pair_by_endpoints[endpoints]['pair_id'] if endpoints in pair_by_endpoints else 'missing:' + hashlib.sha256(
            json.dumps(endpoints).encode()).hexdigest()
        if row['is_match'] not in {'0', '1', 'UNKNOWN'}:
            raise ValueError('Unrecognized label; no implicit negative')
        labels[pid] = {'1': PairLabel.POSITIVE, '0': PairLabel.NEGATIVE, 'UNKNOWN': PairLabel.UNKNOWN}[row['is_match']]
        label_pairs[pid] = endpoints
        label_strata[pid] = {k: row[k] for k in ('case_type', 'difficulty', 'hard_negative_type')}
    candidate_ids = {p['pair_id'] for p in pairs}
    advisory, routing = {}, {}
    for p in pairs:
        blocked = p['decision'] in {'NOT_MERGEABLE', 'NON_MATCH', 'BLOCKED'}
        base = 'BLOCKED' if blocked else ('INSUFFICIENT_EVIDENCE' if p['decision'] == 'INSUFFICIENT_EVIDENCE' else 'REVIEW')
        advisory[p['pair_id']] = 'MATCH' if p['decision'] == 'MERGEABLE' and p['eligibility']['manual_allowed'] else base
        routing[p['pair_id']] = 'MATCH' if p['decision'] == 'MERGEABLE' and p.get('triage_status') != 'REVIEW' and p['eligibility']['auto_allowed'] else base
    known_covered = {pid: label for pid, label in labels.items() if set(label_pairs[pid]) <= set(memberships)}
    cluster_decisions = {pid: 'MATCH' if memberships[a] == memberships[b] else 'NON_MATCH'
                         for pid, (a, b) in label_pairs.items() if a in memberships and b in memberships}
    constraints = Counter()
    forbidden_advisory = forbidden_auto = cluster_checks = cluster_violations = 0
    with engine_context(config['effective_assessment_configuration']['profile']):
        for p in pairs:
            violation = check_all(records[p['left_id']], records[p['right_id']], frozenset())
            if violation:
                constraints[violation.constraint] += 1
                forbidden_advisory += advisory[p['pair_id']] == 'MATCH'
                forbidden_auto += routing[p['pair_id']] == 'MATCH'
        for group in groups:
            for a, b in combinations(group['member_record_ids'], 2):
                cluster_checks += 1
                cluster_violations += check_all(records[a], records[b], frozenset()) is not None
    full_cluster = cluster_metrics(memberships, None, expected_record_ids=frozenset(memberships),
                                   entity_gold_complete_verified=False)
    with factory() as db:
        artifacts = list(db.query(WorkflowArtifact).filter_by(run_id=rid))
        db_artifact_bytes = sum(len(json.dumps(a.payload, ensure_ascii=False).encode()) for a in artifacts)
        phase_counts = dict(Counter(a.phase for a in artifacts))
    for p in sources:
        if file_hash(p) != pins[str(p.relative_to(ROOT))]:
            raise ValueError('Source hash changed during E0')
    if any(file_hash(ROOT / rel) != value for rel, value in code_pins.items()):
        raise ValueError('Pinned source/config code changed during E0')
    known_n = sum(v is not PairLabel.UNKNOWN for v in labels.values())
    summary = {
        'experiment': 'E0', 'status': 'DIAGNOSTIC_ONLY_M0', 'executed_at_utc': datetime.now(UTC).isoformat(),
        'head': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
        'run_id': rid, 'input_records': actual_records, 'candidate_pairs': len(pairs),
        'unique_candidate_pairs': len(pair_by_endpoints), 'candidate_metrics': candidate_metrics, 'scoring_metrics': scoring_metrics,
        'legal_pair_universe': actual_records * (actual_records - 1) // 2,
        'reduction_ratio': 1 - len(pair_by_endpoints) / (actual_records * (actual_records - 1) // 2),
        'groups': dict(Counter(p['primary_group'] for p in pairs)),
        'original_decide_groups': dict(Counter(p['original_group'] for p in pairs)),
        'matching_decisions': dict(Counter(p['decision'] for p in pairs)),
        'original_decide_decisions': dict(Counter(p['original_decision'] for p in pairs)),
        'review_required_pairs': sum(p['review_required'] for p in pairs),
        'candidate_review_rate': ratio(sum(routing[p['pair_id']] == 'REVIEW' for p in pairs), len(pairs), 'no candidates'),
        'candidate_abstain_rate': ratio(sum(routing[p['pair_id']] in {'REVIEW', 'INSUFFICIENT_EVIDENCE'} for p in pairs), len(pairs), 'no candidates'),
        'human_review_actions': 0, 'automated_accepts': 0, 'overlay_decisions': dict(Counter(p['overlay'] for p in pairs)),
        'rule_advisory': evaluate_pairs(labels, advisory, candidate_ids),
        'final_routing': evaluate_pairs(labels, routing, candidate_ids),
        'labels': {'positive': sum(v is PairLabel.POSITIVE for v in labels.values()),
                   'negative': sum(v is PairLabel.NEGATIVE for v in labels.values()),
                   'unknown': sum(v is PairLabel.UNKNOWN for v in labels.values()),
                   'scope': ('sampled TEST pairs; reused; not independent human verified' if args.dataset == 'small'
                             else 'TC1 synthetic B1: all declared positives and sampled negatives; no independent human ground truth')},
        'cluster_result': state().get('cluster_result'), 'workflow_state': state()['workflow_state'],
        'published_groups': len(groups), 'published_multi_record_groups': sum(g['member_count'] > 1 for g in groups),
        'golden_drafts_read': len(goldens), 'golden_statuses': dict(Counter(g['status'] for g in goldens)),
        'published_membership_records': len(memberships),
        'sampled_cluster_endpoint_coverage': ratio(len(known_covered), known_n, 'no known labels'),
        'sampled_cluster_covered_only': evaluate_pairs(known_covered, cluster_decisions, set(cluster_decisions)),
        'full_entity_cluster_metrics': {k: asdict(v) for k, v in full_cluster.items()},
        'hard_constraints': {'candidate_checks': len(pairs), 'blocked_candidate_violations': dict(constraints),
                             'forbidden_rule_advisory_matches': forbidden_advisory,
                             'forbidden_final_routing_matches': forbidden_auto,
                             'published_co_cluster_checks': cluster_checks, 'published_violations': cluster_violations,
                             'limitations': 'No human Deny; source ID/DOB present in TC1 but no independently verified metadata'},
        'stages': stages, 'nested_compute_stages': nested, 'prediction_snapshot_seconds': snapshot_wall,
        'pipeline_wall_seconds': total_wall, 'pipeline_process_peak_rss_bytes': serving_peak,
        'evaluation_seconds': time.perf_counter() - eval_start,
        'workflow_db_artifact_json_bytes': db_artifact_bytes, 'workflow_artifact_phase_counts': phase_counts,
        'environment': {'python': sys.version, 'platform': platform.platform(),
                        'packages': {n: importlib.metadata.version(n) for n in ('fastapi', 'sqlalchemy', 'duckdb', 'pyarrow', 'splink', 'scikit-learn')}},
        'input_hashes': pins, 'code_hashes': code_pins, 'predictions_sha256': prediction_hash,
        'memberships_sha256': membership_hash,
        'post_hitl': {'status': 'NOT_RUN', 'reason': 'No authorized human decisions; no simulation'},
    }
    write_json(output / 'report.json', summary)
    write_json(output / 'labeled-pair-diagnostics.json', [
        {'pair_id': pid, 'label': label.value, 'candidate': pid in candidate_ids,
         'rule_advisory': advisory.get(pid, 'MISSING'), 'final_routing': routing.get(pid, 'MISSING'),
         **label_strata[pid]} for pid, label in labels.items()])
    (output / 'report.md').write_text(
        '# E0 diagnostic M0\n\n' + '\n'.join(f'- {k}: {summary[k]}' for k in (
            'head', 'input_records', 'candidate_pairs', 'groups', 'matching_decisions',
            'published_groups', 'published_membership_records', 'golden_drafts_read',
            'workflow_state', 'pipeline_wall_seconds', 'pipeline_process_peak_rss_bytes')) +
        '\n\nFull counts/metrics/scope/config: report.json and config.json.\n', encoding='utf8')
    engine.dispose()
    app.dependency_overrides.clear()
    finish_run(output, keep_state=args.keep_state)
    inventory = [{'path': p.relative_to(output).as_posix(), 'bytes': p.stat().st_size, 'sha256': file_hash(p)}
                 for p in sorted(output.rglob('*')) if p.is_file()]
    write_json(output / 'manifest.json', {'source_hashes': pins, 'code_hashes': code_pins,
        'files': inventory, 'total_artifact_file_bytes_excluding_manifest': sum(x['bytes'] for x in inventory),
        'source_files_unchanged': True, 'truth_columns_in_matcher_payload': False})
    print(json.dumps({k: summary[k] for k in ('status', 'input_records', 'candidate_pairs',
        'groups', 'matching_decisions', 'cluster_result', 'pipeline_wall_seconds')}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
