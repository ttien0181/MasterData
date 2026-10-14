"""Isolated, reproducible handoff probes against checksum-verified user data.

No developer DB, remote object store or AI provider is used. The API probe uses
the actual routes and leased workflow worker in-process; it is not a 100k-run
load test. Offline truth only evaluates outputs and never supplies decisions.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import sys
import time
from collections import Counter
from copy import deepcopy
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from run_merge_baseline import isolated_environment  # noqa: E402


def timed(call):
    start = time.perf_counter()
    value = call()
    return value, time.perf_counter() - start


def require(response, status=200):
    if response.status_code != status:
        raise RuntimeError(f'Unexpected HTTP {response.status_code}: {response.text[:500]}')
    return response.json()


def offline(components):
    from masterone.mdm.engine_adapter import engine_context
    from masterone.mdm.workflow_finalize import compute_publication
    profile = dict(decision_semantics_v2_enabled=True, cluster_safety_v2_enabled=True,
                   generation_staging_enabled=False)
    # Fixed subset of each stratum; source assessments precede offline truth.
    selected, counts = [], Counter()
    for component in components:
        if counts[component['stratum']] < 8:
            selected.append(component)
            counts[component['stratum']] += 1
    records = {r['id']: r for c in selected for r in c['records']}
    pairs = {p['id']: p for c in selected for p in c['pairs']}
    snapshot = dict(records=list(records.values()), pairs=list(pairs.values()), overlays={},
                    review_reasons={}, negative_pairs=[], profile=profile, policy_version='person-v3',
                    allow_partial=True)
    with engine_context(profile):
        initial, full_seconds = timed(lambda: compute_publication(snapshot, 'offline-handoff', 1))
        changed = deepcopy(snapshot)
        changed_pair = next(p for p in pairs.values() if p['decision'] in ('MERGEABLE', 'REVIEW'))
        # Explicit simulated steward rejection, chosen without truth labels.
        changed['overlays'][changed_pair['id']] = 'REJECTED'
        cached, cached_seconds = timed(lambda: compute_publication(
            changed, 'offline-handoff', 2, previous_plan={'snapshot': snapshot, 'computed': initial}))
        full, changed_full_seconds = timed(lambda: compute_publication(changed, 'offline-handoff', 2))
    for key in ('clusters', 'publication', 'conflicts', 'pairs', 'unresolved_record_ids'):
        if cached[key] != full[key]:
            raise AssertionError('Cached/full disagreement: ' + key)
    rejected = {changed_pair['left_id'], changed_pair['right_id']}
    if any(rejected <= set(c['member_record_ids']) for c in cached['clusters']):
        raise AssertionError('Rejected edge merged indirectly')
    owners = {rid: owner for c in selected for rid, owner in c['owners'].items()}
    cross_entity = sum(len({owners[rid] for rid in c['member_record_ids'] if owners.get(rid)}) > 1
                       for c in cached['clusters'])
    return {'selected_components': len(selected), 'selected_records': len(records), 'pairs': len(pairs),
            'strata': dict(counts), 'full_seconds': full_seconds, 'changed_full_seconds': changed_full_seconds,
            'cached_seconds': cached_seconds, 'region_cache': cached['region_cache'],
            'cached_full_equal': True, 'rejected_edge_kept_apart': True,
            'offline_cross_entity_groups': cross_entity,
            'assessment_frequency_scope': 'original complete scenario, not entire 100k bundle',
            'simulated_review': 'one rejection from actionable raw assessment; no truth-based acceptance'}


def api_probe(components):
    from masterone.config import get_settings
    from masterone.mdm.models import ResolutionRecord
    from masterone.mdm.workflow_models import WorkflowCluster, WorkflowGeneration, WorkflowPair
    from pytest import MonkeyPatch
    from tests.test_mdm.conftest import client as client_fixture
    from tests.test_mdm.test_workflow_stages import ready_decisions, run_one
    patch = MonkeyPatch()
    settings = get_settings()
    patch.setattr(settings, 'session_workflow_enabled', True)
    patch.setattr(settings, 'pair_assessment_auto_enabled', True)
    patch.setattr(settings, 'mdm_engine_v2_enabled', True)
    fixture = client_fixture.__wrapped__(patch)
    client = next(fixture)
    try:
        selected, strata = [], Counter()
        for c in components:
            if len(c['records']) <= 20 and strata[c['stratum']] < 1:
                selected.append(c)
                strata[c['stratum']] += 1
            if len(selected) >= 12:
                break
        records = {r['id']: r for c in selected for r in c['records']}
        stream = io.StringIO()
        fields = ['source_record_id', 'source_system', 'entity_type', 'name', 'dob', 'government_id',
                  'government_id_type', 'government_id_namespace', 'government_id_verified',
                  'phone', 'email', 'address']
        writer = csv.DictWriter(stream, fields, extrasaction='ignore')
        writer.writeheader()
        for r in records.values():
            writer.writerow({**r['raw_fields'], 'source_record_id': r['external_id'],
                             'source_system': r['source_system'], 'entity_type': r['entity_type']})
        uploaded = require(client.post('/api/v1/mdm/runs', data={'workflow_version': '2'},
            files={'files': ('supplied-handoff.csv', stream.getvalue().encode('utf8'), 'text/csv')}), 201)
        rid = uploaded['run_id']
        root = f'/api/v1/mdm/runs/{rid}/workflow'
        _, prepare_decide_seconds = timed(lambda: ready_decisions(client, rid))

        def state():
            return require(client.get(root))

        def mutation(**extra):
            w = state()
            return dict(request_id=str(uuid4()), expected_workflow_revision=w['workflow_revision'],
                        expected_decision_revision=w['decision_revision'],
                        expected_assessment_id=w['assessment_id'], **extra)

        pending = require(client.get(root + '/pairs', params={'resolution': 'REQUIRED', 'limit': 100}))
        rejected, accepted = 0, 0
        while pending['items']:
            for pair in pending['items']:
                # Simulated steward fixture: clear A matches may be accepted;
                # every ambiguous/soft/hard pair stays apart. Never use truth.
                clear = pair['baseline']['decision'] == 'MERGEABLE' and pair['baseline'].get('primary_group') == 'A'
                body = mutation(decision='merge' if clear else 'no_merge', expected_pair_revision=pair['revision'])
                body.pop('expected_decision_revision')
                require(client.post(root + f"/pairs/{pair['pair_id']}/decision", json=body))
                accepted += int(clear)
                rejected += int(not clear)
            pending = require(client.get(root + '/pairs', params={'resolution': 'REQUIRED', 'limit': 100}))
        queued = require(client.post(root + '/cluster-preview', json=mutation(allow_partial=True)), 202)
        _, preview_seconds = timed(lambda: run_one(client))
        receipt = require(client.get(root + '/operations/' + queued['operation_id']))
        assert receipt['status'] == 'COMPLETED', receipt
        page, page_seconds = timed(lambda: require(client.get(root + '/cluster-preview/' +
                                                              queued['operation_id'] + '/groups')))
        expected = {c['id']: c['member_record_ids'] for c in page['items']}
        while page['next_cursor']:
            page = require(client.get(root + '/cluster-preview/' + queued['operation_id'] + '/groups',
                                      params={'cursor': page['next_cursor']}))
            expected.update({c['id']: c['member_record_ids'] for c in page['items']})
        cluster_op = require(client.post(root + '/finalize-decisions', json=mutation(allow_partial=True,
            preview_operation_id=queued['operation_id'], preview_token=receipt['result']['preview_token'])), 202)
        _, publish_seconds = timed(lambda: run_one(client))
        published = state()
        assert published['cluster_revision'] == 1, published
        with client.session_factory() as db:
            actual = {c.id: c.payload['member_record_ids'] for c in db.query(WorkflowCluster).join(
                WorkflowGeneration).filter(WorkflowGeneration.run_id == rid, WorkflowGeneration.revision == 1)}
            owners_by_key = {key: c['owners'][record['id']] for c in selected
                             for key, record in zip(c['source_keys'], c['records'])}
            owners = {row.id: owners_by_key[row.source_system + ':' + row.external_id]
                      for row in db.query(ResolutionRecord).filter_by(run_id=rid)}
            cross_entity = sum(len({owners[member] for member in ids if owners.get(member)}) > 1
                               for ids in actual.values())
            membership = {member: cid for cid, ids in actual.items() for member in ids}
            for pair in db.query(WorkflowPair).filter_by(run_id=rid, decision='REJECTED'):
                a, b = pair.baseline['left_id'], pair.baseline['right_id']
                if a in membership and membership.get(a) == membership.get(b):
                    raise AssertionError('Review veto violated in actual API publication')
        assert expected == actual, 'Preview membership differs from publication'
        group_id = next(iter(actual))
        detail, detail_seconds = timed(lambda: require(client.get(root + '/clusters/' + group_id)))
        assert detail['cluster_id'] == group_id
        return {'records_uploaded': len(records), 'simulated_no_merge_decisions': rejected,
                'simulated_clear_A_accept_decisions': accepted,
                'groups': len(actual), 'multi_record_groups': sum(len(ids) > 1 for ids in actual.values()),
                'offline_cross_entity_groups': cross_entity, 'review_vetoes_preserved': True,
                'covered_records': sum(map(len, actual.values())),
                'prepare_decide_seconds': prepare_decide_seconds,
                'preview_seconds': preview_seconds, 'first_page_seconds': page_seconds,
                'publication_seconds': publish_seconds, 'group_detail_seconds': detail_seconds,
                'preview_publication_exact_membership': True,
                'worker_performance': require(client.get(root + '/operations/' +
                                                        cluster_op['operation_id']))['result']['performance'],
                'mode': 'actual HTTP routes + claimed workflow worker, in-process, isolated SQLite',
                'simulated_review': 'accept only raw MERGEABLE Group A; other required pairs kept apart; no truth-based acceptance'}
    finally:
        fixture.close()
        patch.undo()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--datasets', type=Path, default=Path('C:/Users/nguye/Downloads/benchmark_datasets'))
    parser.add_argument('--recovered', type=Path, default=ROOT / 'artifacts/cluster-calibration-20261013/recovered')
    parser.add_argument('--output', type=Path, default=ROOT / 'artifacts/handoff-performance-20261013')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    os.environ.update(isolated_environment(args.output / 'state'))
    sys.path[:0] = [str(ROOT / 'backend/src'), str(ROOT / 'backend')]
    from calibrate_cluster_benchmark import prepare, sha256
    from masterone.mdm.engine_adapter import engine_context
    from masterone.mdm.pipeline.pair_assessment import assessment_profile
    from masterone.mdm.workflow_cluster_preview import publication_identity
    from recover_cluster_benchmark import FOLDERS, contained_path, listing
    initial_identity = publication_identity()
    public = listing('1ZSRF1p6OY9hCglV95jebNcOZusQ_D1Ws')
    cases, all_components = [], []
    for case in ('TC6_CLUSTER_BRIDGE', 'TC8_JUNK_PLACEHOLDER'):
        assert public[case][0] == FOLDERS[case], 'Public folder identity changed'
        bundle = args.datasets / case / '100k/seed-7'
        overlay = args.recovered / case / '100k/seed-7'
        manifest = json.loads((bundle / 'manifest.json').read_text(encoding='utf8'))
        for relative, expected in manifest['file_hashes'].items():
            source = contained_path(bundle, relative)
            if not source.is_file():
                source = contained_path(overlay, relative)
            if sha256(source) != expected:
                raise ValueError('Manifest hash mismatch: ' + case + '/' + relative)
        with engine_context(assessment_profile()):
            (components, _, meta), seconds = timed(lambda: prepare(bundle, overlay))
        all_components.extend(components)
        cases.append({**meta, 'prepare_seconds': seconds, 'manifest_sha256': sha256(bundle / 'manifest.json'),
                      'manifest_files_verified': len(manifest['file_hashes']), 'integrity_status': 'PASS'})
        print(json.dumps({'case': case, 'decoded': meta['integrity']['decoded_records'], 'seconds': seconds}), flush=True)
    result = {'cases': cases, 'offline': offline(all_components), 'api': api_probe(all_components),
              'engine_identity': initial_identity,
              'limits': ['No entire 100k/1M run or multi-process/concurrent browser load measured',
                         'In-memory snapshot reconstruction remains proportional to full run size',
                         'Optional annotations use a separate requested job; no live provider latency measured',
                         'Timings are one local sample, not a production latency SLA']}
    if initial_identity != publication_identity():
        raise RuntimeError('Code changed during benchmark; repeat against final source')
    (args.output / 'report.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf8')
    print(json.dumps({'offline': result['offline'], 'api': result['api'], 'output': str(args.output)}, indent=2))


if __name__ == '__main__':
    main()
