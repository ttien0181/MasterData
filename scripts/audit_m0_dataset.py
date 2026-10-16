"""Read-only M0 profiles; no labels, splits or source files are rewritten.

Full primitive checks cover each CSV. Production normalization is full on the
small local sources, and a declared first-1000-row-per-file sample on large
cached bundles. Contact/name collisions are observations, not identity labels.
"""
import argparse
import csv
import hashlib
import json
import os
import re
import sys
import time
import unicodedata
from collections import Counter, defaultdict
from datetime import UTC, datetime
from itertools import combinations
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'backend/src'), str(ROOT / 'scripts')]


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda: f.read(1024 * 1024), b''):
            h.update(b)
    return h.hexdigest()


def write(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    out = args.output.resolve()
    if not out.is_relative_to((ROOT / 'artifacts/advanced-m0-20261014').resolve()):
        parser.error('Output must stay inside owned M0 artifact directory')
    out.mkdir(parents=True, exist_ok=False)
    # Normalization uses no DB/model, but isolate config before importing it.
    from run_merge_baseline import isolated_environment
    env = isolated_environment(out / 'state')
    env['MASTERONE_ENV_FILE'] = str(out / 'absent.env')
    os.environ.update(env)
    from masterone.mdm.input_schema import resolve_source_schema
    from masterone.mdm.pipeline.standardization import fold_text, standardize
    profiles, source_manifest = [], {}
    small_rows = {}
    started = time.perf_counter()
    roots = [ROOT / 'masterone_test', ROOT / 'benchmark/datasets/masterone_test',
             ROOT / 'benchmark/datasets/data_reference',
             ROOT / 'artifacts/cluster-improvements-20261013/datasets']
    paths = sorted({p for root in roots if root.exists() for p in root.rglob('*.csv')})
    for path in paths:
        relative = path.relative_to(ROOT).as_posix()
        source_manifest[relative] = {'sha256': sha(path), 'bytes': path.stat().st_size}
        n, duplicate, malformed = 0, 0, 0
        missing, types = Counter(), defaultdict(Counter)
        seen, identities = set(), Counter()
        quality, flags, names = defaultdict(Counter), Counter(), Counter()
        sources, cases, difficulties = Counter(), Counter(), Counter()
        rows = []
        is_large = 'cluster-improvements-20261013' in relative
        limit = 1000 if is_large else None
        is_input = False
        with path.open(encoding='utf-8-sig', newline='') as f:
            sample = f.read(4096)
            f.seek(0)
            try:
                dialect = csv.Sniffer().sniff(sample, delimiters=',;\t|')
            except csv.Error:
                dialect = csv.excel
            reader = csv.DictReader(f, dialect=dialect, strict=True)
            columns = reader.fieldnames or []
            mapping = {}
            try:
                mapping = resolve_source_schema(columns).fields
                is_input = True
            except Exception as e:
                schema_error = type(e).__name__
            for row in reader:
                n += 1
                malformed += None in row or any(v is None for v in row.values())
                encoded = json.dumps([row.get(k) for k in columns], ensure_ascii=False).encode()
                key = hashlib.sha256(encoded).digest()
                duplicate += key in seen
                seen.add(key)
                missing.update(k for k in columns if not (row.get(k) or '').strip())
                # Lexical dtype observations: do not coerce IDs/phones or strip leading zero.
                for k in columns:
                    value = (row.get(k) or '').strip()
                    if not value:
                        types[k]['empty'] += 1
                    elif value.isdigit():
                        types[k]['digit_string'] += 1
                    elif re.fullmatch(r'-?\d+\.\d+', value):
                        types[k]['decimal_string'] += 1
                    else:
                        types[k]['text'] += 1
                source = row.get('source_system', row.get('source', ''))
                if source:
                    sources[source] += 1
                rid = row.get(mapping.get('source_record_id'), '') if is_input else row.get(
                    'source_record_id', row.get('record_id', row.get('id', '')))
                if rid:
                    identities[hashlib.sha256(json.dumps([source, rid]).encode()).digest()] += 1
                cases[row.get('case_type', row.get('noise_type', ''))] += 1
                difficulties[row.get('difficulty', '')] += 1
                if is_input and (limit is None or n <= limit):
                    fields = {k: (row.get(c) or '') for k, c in mapping.items()
                              if k in {'name', 'phone', 'email', 'address', 'dob', 'government_id', 'government_id_type'}}
                    nf, qf = standardize(fields, row.get('entity_type') or 'PERSON')
                    for k, v in nf.get('_field_quality', {}).items():
                        quality[k][v] += 1
                    flags.update(qf)
                    name = fields.get('name', '')
                    names['non_nfc'] += unicodedata.normalize('NFC', name) != name
                    names['accented'] += fold_text(name) != ' '.join(name.split()).casefold()
                    names['replacement_character'] += '\ufffd' in name
                    names['mojibake_suspect'] += bool(re.search(r'Ã|Â|áº|á»', name))
                    names['single_token'] += len(name.split()) == 1
                    if not is_large:
                        rows.append({'source': source, 'id': rid, 'nf': nf,
                                     'entity': row.get('entity_id'), 'raw': row})
        profile = {'path': relative, 'rows': n, 'columns': columns, 'lexical_types': dict(types),
                   'missing': dict(missing), 'malformed_rows': malformed, 'exact_duplicate_rows': duplicate,
                   'duplicate_source_keys': sum(v - 1 for v in identities.values() if v > 1),
                   'sources': dict(sources), 'case_counts': dict(cases), 'difficulty_counts': dict(difficulties),
                   'production_schema_mapping': mapping if is_input else None,
                   'production_schema_error': None if is_input else schema_error,
                   'normalization_scope': 'full file' if is_input and limit is None else
                       ('first 1000 rows per file; nonrandom diagnostic sample' if is_input else 'not customer input'),
                   'normalization_rows': sum(quality.get('name', {}).values()),
                   'field_quality': dict(quality), 'quality_flags': dict(flags), 'name_unicode': dict(names)}
        profiles.append(profile)
        if rows:
            small_rows[relative] = rows
        print('PROFILE', relative, n, flush=True)

    def observations(rows):
        result = {}
        for field in ('name', 'phone', 'email', 'address'):
            buckets = defaultdict(list)
            for row in rows:
                value = row['nf'].get(field, '')
                if value and row['nf'].get('_field_quality', {}).get(field) == 'VALID':
                    buckets[value].append(row)
            within, cross, conflicting = Counter(), Counter(), 0
            for bucket in buckets.values():
                counts = Counter(r['source'] for r in bucket)
                for src, count in counts.items():
                    within[src] += count * (count - 1) // 2
                for a, b in combinations(sorted(counts), 2):
                    cross[a + '|' + b] += counts[a] * counts[b]
                other = {'name': 'phone', 'phone': 'email', 'email': 'phone', 'address': 'name'}[field]
                conflicting += len(bucket) > 1 and len({r['nf'].get(other) for r in bucket if r['nf'].get(other)}) > 1
            result[field] = {'repeated_value_buckets': sum(len(v) > 1 for v in buckets.values()),
                             'within_source_equal_value_pairs': dict(within),
                             'cross_source_equal_value_pairs': dict(cross),
                             'repeated_anchor_buckets_with_multiple_other_field_values': conflicting,
                             'meaning': 'collision/value variation only, not duplicate-person or NON_MATCH labels'}
        core = Counter(hashlib.sha256(json.dumps([r['nf'].get(k, '') for k in
            ('name', 'phone', 'email', 'address')], ensure_ascii=False).encode()).digest() for r in rows)
        result['identical_normalized_core_rows_excluding_id'] = sum(v - 1 for v in core.values() if v > 1)
        return result
    collisions = {}
    for name in ('masterone_test/records.csv', 'masterone_test/crm.csv', 'masterone_test/erp.csv', 'masterone_test/bill.csv'):
        if name in small_rows:
            collisions[name] = observations(small_rows[name])
    combined = [row for name in ('masterone_test/crm.csv', 'masterone_test/erp.csv', 'masterone_test/bill.csv') for row in small_rows.get(name, [])]
    collisions['CRM_ERP_BILL_21000_separate_universe'] = observations(combined)

    records_path = ROOT / 'benchmark/datasets/masterone_test/records.csv'
    pairs_path = ROOT / 'benchmark/datasets/masterone_test/pairs.csv'
    byid = {r['id']: r for r in small_rows['benchmark/datasets/masterone_test/records.csv']}
    label_counts, source_pairs, strata, unicode_cases = Counter(), defaultdict(Counter), defaultdict(Counter), Counter()
    canonical_labels, bad_endpoints, self_pairs, inconsistent = {}, 0, 0, 0
    splits, entity_splits = Counter(), defaultdict(set)
    for row in csv.DictReader(pairs_path.open(encoding='utf-8-sig', newline='')):
        a, b = row['id_a'], row['id_b']
        key = tuple(sorted((a, b)))
        label = row['is_match']
        self_pairs += a == b
        inconsistent += key in canonical_labels and canonical_labels[key] != label
        canonical_labels[key] = label
        label_counts[label] += 1
        splits[row['split']] += 1
        if a not in byid or b not in byid:
            bad_endpoints += 1
            continue
        left, right = byid[a], byid[b]
        source_pairs['|'.join(sorted([left['source'], right['source']]))][label] += 1
        for r in (left, right):
            entity_splits[r['entity']].add(row['split'])
        for field in ('case_type', 'difficulty', 'hard_negative_type'):
            strata[field + ':' + row[field]][label] += 1
        ln, rn = left['raw']['full_name'], right['raw']['full_name']
        unicode_cases['same_folded_name_different_raw'] += fold_text(ln) == fold_text(rn) and ln != rn
    lf_hashes = {p.name: hashlib.sha256(p.read_bytes().replace(b'\r\n', b'\n')).hexdigest() for p in (records_path, pairs_path)}
    bundles = []
    for path in sorted((ROOT / 'artifacts/cluster-improvements-20261013/datasets').rglob('manifest.json')):
        manifest = json.loads(path.read_text(encoding='utf-8-sig'))
        source_manifest[path.relative_to(ROOT).as_posix()] = {'sha256': sha(path), 'bytes': path.stat().st_size}
        good, absent, changed = 0, [], []
        for rel, expected in manifest['file_hashes'].items():
            p = path.parent / rel
            if not p.is_file():
                absent.append(rel)
                continue
            actual = sha(p)
            source_manifest[p.relative_to(ROOT).as_posix()] = {'sha256': actual, 'bytes': p.stat().st_size}
            if actual == expected:
                good += 1
            else:
                changed.append(rel)
        bundles.append({'manifest': path.relative_to(ROOT).as_posix(), 'case': manifest['case'],
                        'generated_records': manifest['generated_records'], 'realism_level': manifest['realism_level'],
                        'matched_files': good, 'missing_files': absent, 'hash_mismatches': changed,
                        'generator_source_sha256': manifest.get('generator_source_sha256'),
                        'scope': 'synthetic diagnostic; labeling independence from matcher not independently adjudicated'})
    for path in (ROOT / 'benchmark/datasets/manifests').glob('*.json'):
        source_manifest[path.relative_to(ROOT).as_posix()] = {'sha256': sha(path), 'bytes': path.stat().st_size}
    import yaml
    for path in sorted((ROOT / 'artifacts/cluster-improvements-20261013/datasets').rglob('manifest.yaml')):
        manifest = yaml.safe_load(path.read_text(encoding='utf8'))
        source_manifest[path.relative_to(ROOT).as_posix()] = {'sha256': sha(path), 'bytes': path.stat().st_size}
        good, absent, changed = 0, [], []
        for rel, expected in manifest['file_hashes'].items():
            p = path.parent / rel
            if not p.is_file():
                absent.append(rel)
                continue
            actual = sha(p)
            source_manifest[p.relative_to(ROOT).as_posix()] = {'sha256': actual, 'bytes': p.stat().st_size}
            if actual == expected:
                good += 1
            else:
                changed.append(rel)
        bundles.append({'manifest': path.relative_to(ROOT).as_posix(),
                        'case': path.parent.parent.parent.name, 'generated_records': manifest['actual_records'],
                        'realism_level': manifest.get('parameter_provenance'), 'matched_files': good,
                        'missing_files': absent, 'hash_mismatches': changed,
                        'scope': 'synthetic PRIOR, not steward-labeled production gold'})
    report = {'executed_at_utc': datetime.now(UTC).isoformat(), 'wall_seconds': time.perf_counter() - started,
              'drive_access': 'Public web open failed Internal Error; plugin still installed=false on M0 check',
              'profiles': profiles, 'collisions': collisions,
              'labels': {'counts': dict(label_counts), 'splits': dict(splits), 'source_pair_counts': dict(source_pairs),
                         'strata': dict(strata), 'self_pairs': self_pairs, 'missing_endpoints': bad_endpoints,
                         'conflicting_labels': inconsistent, 'unique_canonical_pairs': len(canonical_labels),
                         'entity_split_overlap': sum(len(v) > 1 for v in entity_splits.values()),
                         'unicode_cases': dict(unicode_cases), 'lf_normalized_sha256': lf_hashes,
                         'independently_verified_entity_gold': False,
                         'label_algorithm_contamination': 'UNVERIFIED: generator/adjudication provenance missing',
                         'train': 'NOT_AVAILABLE', 'calibration': 'NOT_AVAILABLE', 'threshold_tuning': 'NOT_AVAILABLE',
                         'test': 'existing sampled TEST-only reused diagnostic, not untouched holdout'},
              'cached_bundles': bundles, 'source_manifest_file': 'source-manifest.json'}
    # Fail if any source changed during this read-only audit.
    for rel, entry in source_manifest.items():
        if sha(ROOT / rel) != entry['sha256']:
            raise ValueError('Source changed during audit: ' + rel)
    write(out / 'dataset-audit.json', report)
    write(out / 'source-manifest.json', {'files': source_manifest, 'sources_unchanged': True,
        'id_and_phone_dtype': 'opaque strings; leading zeros retained', 'new_labels_or_splits_created': False})
    print('REPORT', out, flush=True)


if __name__ == '__main__':
    main()
