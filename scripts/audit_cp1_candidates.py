"""Read-only GenVN 10k candidate audit. Raw-only adapter; truth joins after BUILD.

The source_mapping.yaml adapter mirrors GenVN files.iter_prepare, not P-119's
HTTP importer. It does not repair numeric cells, dates, identifiers or names.
Every case/mode runs in a fresh isolated process; datasets are never modified.
"""
import argparse
import csv
import importlib.util
import json
import subprocess
import sys
import unicodedata
from collections import defaultdict
from dataclasses import asdict
from hashlib import sha256
from itertools import combinations
from pathlib import Path
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from benchmark_sparse_tfidf import peak_rss
from run_merge_baseline import isolated_environment


def read_raw(dataset):
    import yaml
    from openpyxl import load_workbook
    catalog = yaml.safe_load((dataset / 'raw/source_mapping.yaml').read_text(encoding='utf8'))
    accepted, rejected, fingerprints, native_headers = [], defaultdict(int), {}, defaultdict(int)
    for mapping in catalog['files']:
        path = (dataset / mapping['path']).resolve()
        if not path.is_relative_to((dataset / 'raw').resolve()):
            raise ValueError('Raw path escape')
        fingerprints[mapping['path']] = sha256(path.read_bytes()).hexdigest()
        workbook = None
        if mapping['format'] == 'xlsx':
            workbook = load_workbook(path, read_only=True, data_only=True)
            rows = iter(workbook.active.values)
        else:
            data = path.read_bytes()
            if mapping['mixed_encoding']:
                lines = []
                for line in data.splitlines(keepends=True):
                    try:
                        lines.append(line.decode('utf-8-sig'))
                    except UnicodeDecodeError:
                        lines.append(line.decode('cp1258'))
            else:
                lines = data.decode(mapping['encoding']).splitlines(keepends=True)
            rows = iter(csv.reader(lines, delimiter=mapping['delimiter']))
        for _ in range(mapping['header_row']):
            next(rows)
        header = list(next(rows))
        from masterone.mdm.input_schema import resolve_source_schema, InputMappingError
        try:
            resolve_source_schema(header)
            native_headers['supported'] += 1
        except InputMappingError as error:
            native_headers['unsupported_' + error.report['code']] += 1
        for line, values in enumerate(rows, mapping['header_row'] + 2):
            if list(values) == header or (values and unicodedata.normalize('NFC', str(values[0])) == 'Tổng cộng' and not any(values[1:])):
                continue
            if len(values) != len(header):
                rejected['MALFORMED_ROW'] += 1
                continue
            physical = dict(zip(header, values))
            row = {field: '' if physical[col] is None else str(physical[col]) for field, col in mapping['columns'].items()}
            if not row['source_record_id'].strip():
                rejected['MISSING_SOURCE_RECORD_ID'] += 1
                continue
            key = f"{row['source_system']}:{row['source_record_id']}:{mapping['batch_id']}"
            accepted.append((key, row, {'source_file': mapping['path'], 'source_row_number': line}))
        if workbook:
            workbook.close()
    return accepted, dict(rejected), fingerprints, dict(native_headers)


def pair_hash(pairs):
    return sha256(json.dumps([[a, b, sorted(pairs[a, b])] for a, b in sorted(pairs)], ensure_ascii=False).encode()).hexdigest()


def evaluate(dataset, raw, pairs):
    import pyarrow.parquet as pq
    truth = pq.read_table(dataset / 'truth/records.parquet').to_pylist()
    by_id = {r['record_key']: r for r in truth}
    inputs = {key: row for key, row, _ in raw}
    if len(inputs) != len(raw) or set(inputs) - by_id.keys():
        raise ValueError('Duplicate or unknown raw identity')
    groups = defaultdict(list)
    for row in truth:
        if row['label_status'] == 'CERTAIN' and row['entity_id']:
            groups[row['entity_id']].append(row['record_key'])
    full_gold = {tuple(sorted(p)) for ids in groups.values() for p in combinations(ids, 2)}
    gold = {p for p in full_gold if all(k in inputs for k in p)}
    strata = defaultdict(set)
    for a, b in gold:
        left, right = inputs[a], inputs[b]
        issues = {i['issue_id'] for k in (a, b) for i in by_id[k]['applied_issues']}
        strata['cross_source' if left['source_system'] != right['source_system'] else 'same_source'].add((a, b))
        for label, ids in [('vietnamese_diacritics', {'ISS-NAME-01', 'ISS-NAME-05', 'ISS-NAME-06', 'ISS-NAME-07'}),
                           ('typo', {'ISS-NAME-08', 'ISS-NAME-09'}),
                           ('phone_format', {'ISS-PHONE-01', 'ISS-PHONE-02', 'ISS-PHONE-05', 'ISS-PHONE-06'})]:
            if ids & issues:
                strata[label].add((a, b))
        if any(not row.get(f, '').strip() for row in (left, right) for f in ('name', 'phone', 'email', 'dob')):
            strata['missing_data'].add((a, b))
        if left.get('dob') and right.get('dob') and left['dob'] != right['dob']:
            strata['dob_raw_conflict'].add((a, b))
    def metric(pool):
        return {'true_pairs': len(pool), 'retrieved': len(pool & pairs.keys()),
                'recall': len(pool & pairs.keys()) / len(pool) if pool else None}
    return {'scope': 'all CERTAIN entity pairs among accepted raw records; AMBIGUOUS_POLICY/JUNK excluded',
            'candidate_recall': metric(gold), 'full_truth_recall_including_input_losses': metric(full_gold),
            'truth_records': len(truth), 'truth_missing_input_records': len(by_id.keys() - inputs.keys()),
            'candidate_count': len(pairs), 'reduction_ratio': 1 - len(pairs) / (len(raw) * (len(raw)-1)/2),
            'strata': {k: metric(strata[k]) for k in ('vietnamese_diacritics', 'typo', 'phone_format', 'missing_data', 'dob_raw_conflict', 'cross_source', 'same_source')}}


def worker(args):
    import pyarrow as pa
    import pyarrow.parquet as pq
    from masterone.mdm.pipeline.candidate_engine import CandidateEngine, CandidateConfig
    from masterone.mdm.pipeline.candidate_planner import WorkloadPlanError
    from masterone.mdm.pipeline.standardization import standardize
    from masterone.mdm.pipeline.stage_runner import StageRunner
    started = perf_counter()
    raw, rejected, hashes, headers = read_raw(args.dataset)
    records = [{'id': key, 'normalized_fields': standardize(row, row.get('entity_type', 'PERSON'))[0]} for key, row, _ in raw]
    config = CandidateConfig(sparse_tfidf=args.mode == 'sparse')
    report = {'case': args.dataset.parts[-3], 'mode': args.mode, 'input_records': len(raw), 'rejected_rows': rejected,
              'native_schema_headers': headers, 'raw_hashes': hashes, 'config': asdict(config), 'python': sys.version}
    build_start = perf_counter()
    try:
        if args.mode == 'baseline':
            for name in ('tfidf_blocking', 'blocking'):
                spec = importlib.util.spec_from_file_location('masterone.mdm.pipeline.' + name, args.output / (name + '.py'))
                module = importlib.util.module_from_spec(spec)
                sys.modules[spec.name] = module
                spec.loader.exec_module(module)
            pairs = module.generate_candidates(records)
        else:
            result = CandidateEngine(config).generate(records)
            pairs = result.pairs
            report['engine_metrics'] = result.metrics
        report.update(status='completed', build_seconds=perf_counter()-build_start,
                      pair_provenance_sha256=pair_hash(pairs), metrics=evaluate(args.dataset, raw, pairs),
                      build_peak_rss_bytes=peak_rss())
        if args.mode == 'default':
            snapshot = args.output / 'snapshot.parquet'
            pq.write_table(pa.Table.from_pylist([{'id': r['id'], 'normalized_fields': json.dumps(r['normalized_fields'])} for r in records]), snapshot)
            report['equivalence'] = []
            for batch_size in (257, 4096):
                directory = args.output / str(batch_size)
                directory.mkdir()
                runner = StageRunner('audit', 'cp1', output_dir=directory)
                try:
                    large = runner.build_candidates(str(snapshot), str(directory / 'pairs.parquet'), config, batch_size=batch_size)
                    digest = pair_hash(large.pairs)
                    report['equivalence'].append({'batch_size': batch_size, 'pass': digest == report['pair_provenance_sha256'], 'hash': digest})
                finally:
                    runner.con.close()
    except WorkloadPlanError as error:
        report.update(status='guarded', plan=error.plan, build_seconds=perf_counter()-build_start)
    report.update(total_seconds=perf_counter()-started, process_peak_rss_bytes=peak_rss())
    (args.output / 'metrics.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf8')
    print(json.dumps({k: report.get(k) for k in ('case', 'mode', 'status', 'metrics', 'build_seconds', 'process_peak_rss_bytes', 'equivalence')}, ensure_ascii=False), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--dataset', type=Path)
    parser.add_argument('--mode', choices=('default', 'sparse', 'baseline'))
    args = parser.parse_args()
    if args.dataset:
        worker(args)
        return
    args.output.mkdir(parents=True, exist_ok=False)
    results = []
    for dataset in sorted(args.root.glob('TC*/10k/seed-7')):
        for mode in ('default', 'sparse', 'baseline'):
            directory = args.output / dataset.parts[-3] / mode
            directory.mkdir(parents=True)
            if mode == 'baseline':
                for name in ('blocking', 'tfidf_blocking'):
                    source = subprocess.check_output(['git', 'show', '937bfa9:backend/src/masterone/mdm/pipeline/' + name + '.py'], cwd=ROOT)
                    (directory / (name + '.py')).write_bytes(source)
            command = [sys.executable, '-X', 'utf8', __file__, '--dataset', str(dataset), '--mode', mode, '--output', str(directory)]
            try:
                proc = subprocess.run(command, env=isolated_environment(directory), cwd=ROOT, capture_output=True, text=True, encoding='utf8', timeout=300)
                (directory / 'stdout.log').write_text(proc.stdout, encoding='utf8')
                (directory / 'stderr.log').write_text(proc.stderr, encoding='utf8')
                print(dataset.parts[-3], mode, 'exit', proc.returncode, flush=True)
                results.append({'case': dataset.parts[-3], 'mode': mode, 'exit': proc.returncode})
            except subprocess.TimeoutExpired:
                results.append({'case': dataset.parts[-3], 'mode': mode, 'status': 'TIMEOUT_300s'})
                print(dataset.parts[-3], mode, 'TIMEOUT', flush=True)
    (args.output / 'run.json').write_text(json.dumps(results, indent=2), encoding='utf8')


if __name__ == '__main__':
    main()
