"""Full read-only TC1 audit, separate from matcher execution and synthetic gold."""
import argparse
import csv
import hashlib
import json
import os
import sys
import unicodedata
from collections import Counter, defaultdict
from itertools import combinations
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to((ROOT / 'artifacts/advanced-m0-20261014').resolve()):
        parser.error('Audit output must stay in owned M0 directory')
    output.mkdir(parents=True, exist_ok=False)
    from run_merge_baseline import isolated_environment
    env = isolated_environment(output / 'state')
    env['MASTERONE_ENV_FILE'] = str(output / 'absent.env')
    os.environ.update(env)
    sys.path.insert(0, str(ROOT / 'backend/src'))
    import pyarrow.parquet as pq
    from masterone.mdm.pipeline.standardization import fold_text, standardize
    dataset = ROOT / 'artifacts/cluster-improvements-20261013/datasets/TC1_easy_match/10k/seed-7'
    with args.input.open(encoding='utf8', newline='') as handle:
        rows = list(csv.DictReader(handle))
    counts, missing, quality, flags, unicode_counts = Counter(), defaultdict(Counter), defaultdict(Counter), Counter(), Counter()
    entity_types, invalid_id_types, placeholder_indicators = Counter(), Counter(), Counter()
    identities, exact, buckets = Counter(), Counter(), {f: defaultdict(list) for f in ('name', 'phone', 'email', 'address', 'government_id')}
    for row in rows:
        source = row['source_system']
        counts[source] += 1
        entity_types[row['entity_type']] += 1
        identities[(source, row['source_record_id'])] += 1
        exact[tuple(row.values())] += 1
        for field, value in row.items():
            missing[source][field] += not value.strip()
            if value.strip().casefold() in {'unknown', 'n/a', 'null', 'none', '-', '0000000000'}:
                placeholder_indicators[field] += 1
        nf, qf = standardize({k: row[k] for k in ('name', 'phone', 'email', 'address', 'dob', 'government_id', 'government_id_type')}, row['entity_type'])
        flags.update(qf)
        if nf.get('_field_quality', {}).get('government_id') == 'INVALID':
            invalid_id_types[str((row['entity_type'], row['government_id_type']))] += 1
        for field, value in nf.get('_field_quality', {}).items():
            quality[field][value] += 1
        name = row['name']
        unicode_counts['non_nfc'] += unicodedata.normalize('NFC', name) != name
        unicode_counts['accented'] += fold_text(name) != ' '.join(name.split()).casefold()
        unicode_counts['replacement_character'] += '\ufffd' in name
        for field, groups in buckets.items():
            value = nf.get(field)
            if value and nf.get('_field_quality', {}).get(field) == 'VALID':
                groups[value].append((source, nf))
    collisions = {}
    for field, groups in buckets.items():
        within, cross, conflicting = Counter(), Counter(), 0
        other = 'name' if field != 'name' else 'phone'
        for group in groups.values():
            sources = Counter(source for source, _ in group)
            for source, n in sources.items():
                within[source] += n * (n - 1) // 2
            for a, b in combinations(sorted(sources), 2):
                cross[a + '|' + b] += sources[a] * sources[b]
            conflicting += len(group) > 1 and len({nf.get(other) for _, nf in group if nf.get(other)}) > 1
        collisions[field] = {'repeated_value_buckets': sum(len(g) > 1 for g in groups.values()),
            'within_source_equal_value_pairs': dict(within), 'cross_source_equal_value_pairs': dict(cross),
            'anchor_buckets_with_other_field_variation': conflicting,
            'interpretation': 'value collision only; never an identity label'}
    truth_rows = pq.read_table(dataset / 'truth/records.parquet').to_pylist()
    truth = {r['record_key']: r for r in truth_rows}
    entity_sizes = Counter(r['entity_id'] for r in truth_rows)
    source_keys = {f"{r['source_system']}:{r['source_record_id']}:2026-Q1" for r in rows}
    pairs = pq.read_table(dataset / 'truth/answer_keys/evaluation_pairs.parquet').to_pylist()
    labels, strata, canonical = Counter(), Counter(), defaultdict(set)
    missing_endpoints = self_pairs = conflicting_entity_labels = 0
    for pair in pairs:
        a, b, label = pair['record_key_a'], pair['record_key_b'], pair['label']
        labels[label] += 1
        strata[str((label, pair['difficulty'], pair['relation_type']))] += 1
        canonical[tuple(sorted((a, b)))].add(label)
        self_pairs += a == b
        missing_endpoints += a not in source_keys or b not in source_keys
        if a in truth and b in truth and label in {'MATCH', 'NON_MATCH'}:
            conflicting_entity_labels += (truth[a]['entity_id'] == truth[b]['entity_id']) != (label == 'MATCH')
    report = {'dataset': 'TC1_easy_match/10k/seed-7', 'records': len(rows), 'sources': dict(counts),
        'schema': list(rows[0]), 'dtype': 'raw strings; IDs/phone leading zeros preserved',
        'missing_by_source': dict(missing), 'field_quality_full_10022': dict(quality), 'quality_flags': dict(flags),
        'entity_types': dict(entity_types), 'invalid_government_id_by_entity_and_type': dict(invalid_id_types),
        'literal_placeholder_indicators': dict(placeholder_indicators),
        'name_unicode': dict(unicode_counts), 'duplicate_source_keys': sum(n - 1 for n in identities.values()),
        'exact_duplicate_rows': sum(n - 1 for n in exact.values()), 'collisions': collisions,
        'BILL': 'NOT_PRESENT; CRM/ERP/CSV only', 'synthetic_truth_records': len(truth_rows),
        'synthetic_entities': len({r['entity_id'] for r in truth_rows}),
        'truth_key_duplicates': len(truth_rows) - len(truth), 'truth_source_key_symmetric_difference': len(set(truth) ^ source_keys),
        'pair_labels': dict(labels), 'pair_strata': dict(strata), 'unique_labeled_pairs': len(canonical),
        'positive_pair_universe_from_synthetic_entities': sum(n * (n - 1) // 2 for n in entity_sizes.values()),
        'duplicate_label_rows': len(pairs) - len(canonical), 'self_pairs': self_pairs, 'missing_endpoints': missing_endpoints,
        'conflicting_pair_labels': sum(len(v) > 1 for v in canonical.values()),
        'labels_disagreeing_with_generated_entity_truth': conflicting_entity_labels,
        'ground_truth_independence': 'synthetic PRIOR; no independent steward labels; generator-code provenance not independently adjudicated',
        'algorithm_label_contamination': 'UNVERIFIED, not asserted absent; source truth never enters matcher',
        'split_use': {'train': 'NONE', 'calibration': 'NONE', 'threshold_tuning': 'NONE', 'test': 'synthetic B1 diagnostic only; all easy, sampled negatives'},
        'source_projection_sha256': hashlib.sha256(args.input.read_bytes()).hexdigest()}
    (output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf8')
    print(json.dumps({k: report[k] for k in ('records', 'sources', 'pair_labels', 'missing_endpoints', 'conflicting_pair_labels')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
