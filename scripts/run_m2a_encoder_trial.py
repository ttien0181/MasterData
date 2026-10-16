"""Approved E5 CPU trial on TC1 source-only values, not a quality benchmark.

Controller runs in the existing environment, encoder in the isolated D1 venv.
No DB connection, label access, backend dependency change or model fallback.
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'artifacts/advanced-m2a-20261014/e5-trial-01'


def write(path, value):
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    # Windows readers can briefly deny rename while holding the destination.
    # Bound retries; persistent permission problems must still fail the trial.
    for attempt in range(20):
        try:
            temporary.replace(path)
            return
        except PermissionError:
            if attempt == 19:
                raise
            time.sleep(.01)


def file_hash(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def prepare():
    import yaml
    from qa_dataset_adapter import FIELDS, decoded_rows
    sys.path.insert(0, str(ROOT / 'backend/src'))
    from masterone.mdm.pipeline.standardization import standardize
    dataset = ROOT / 'artifacts/cluster-improvements-20261013/datasets/TC1_easy_match/10k/seed-7'
    catalog_path = dataset / 'raw/source_mapping.yaml'
    catalog = yaml.safe_load(catalog_path.read_text(encoding='utf-8'))
    sources = [{'path': str(catalog_path.relative_to(ROOT)), 'sha256': file_hash(catalog_path)}]
    rows, identities = [], set()
    for spec in catalog['files']:
        source = (dataset / spec['path']).resolve()
        if not source.is_relative_to((dataset / 'raw').resolve()):
            raise ValueError('SOURCE_PATH_ESCAPE')
        sources.append({'path': str(source.relative_to(ROOT)), 'sha256': file_hash(source)})
        iterator = iter(decoded_rows(source, spec))
        for _ in range(spec['header_row']):
            next(iterator)
        header = next(iterator)
        for values in iterator:
            original = dict(zip(header, values, strict=True))
            raw = {field: original[spec['columns'][field]] for field in FIELDS}
            identity = (raw['source_system'], raw['source_record_id'])
            if identity in identities:
                raise ValueError('DUPLICATE_SOURCE_ID')
            identities.add(identity)
            normalized, _ = standardize(raw, raw['entity_type'])
            rows.append({'record_key': list(identity), 'name': normalized.get('name_unicode'),
                         'address': normalized.get('address'), 'quality': normalized['_field_quality']})
    if len(rows) != 10022:
        raise ValueError('APPROVED_TC1_COUNT_CHANGED')
    write(OUT / 'input.json', rows)
    write(OUT / 'input-manifest.json', {'records': len(rows), 'sources': sources,
          'normalizer_sha256': file_hash(ROOT / 'backend/src/masterone/mdm/pipeline/standardization.py'),
          'input_sha256': file_hash(OUT / 'input.json'), 'labels_accessed': False,
          'scope': 'TC1 performance only; not a DEV split; not workflow execution'})


def encoder():
    import importlib.metadata

    import numpy as np
    import torch
    from sentence_transformers import SentenceTransformer
    torch.set_num_threads(2)
    torch.set_num_interop_threads(1)
    torch.manual_seed(7)
    torch.use_deterministic_algorithms(True)
    manifest = json.loads((OUT / 'model-manifest.json').read_text(encoding='utf-8'))
    if manifest['revision'] != '614241f622f53c4eeff9890bdc4f31cfecc418b3':
        raise ValueError('MODEL_REVISION_MISMATCH')
    for entry in manifest['files']:
        if file_hash(OUT / 'model' / entry['path']) != entry['sha256']:
            raise ValueError('MODEL_CONTENT_MISMATCH')
    def progress(phase, **extra):
        write(OUT / 'progress.json', {'phase': phase, 'monotonic': time.monotonic(), **extra})
    progress('load')
    started = time.perf_counter()
    model = SentenceTransformer(str(OUT / 'model'), device='cpu', local_files_only=True,
                                trust_remote_code=False)
    model.float().eval()
    load_seconds = time.perf_counter() - started
    rows = json.loads((OUT / 'input.json').read_text(encoding='utf-8'))
    results = {}
    for field, limit in [('name', 128), ('address', 512)]:
        eligible = [r[field] for r in rows if r['quality'].get(field) == 'VALID'
                    and isinstance(r[field], str) and r[field].strip()]
        texts = sorted(set(eligible))
        model.max_seq_length = limit
        progress('batch', field=field, offset=0)
        warm = ['query: ' + text for text in texts[:16]]
        for _ in range(2):
            model.encode(warm, batch_size=16, normalize_embeddings=True, show_progress_bar=False)
            progress('batch', field=field, offset=0)
        micro = []
        for _ in range(3):
            before = time.perf_counter()
            model.encode(warm, batch_size=16, normalize_embeddings=True, show_progress_bar=False)
            micro.append(time.perf_counter() - before)
            progress('batch', field=field, offset=0)
        durations, truncation, retry_count, vectors = [], 0, 0, []
        stage = time.perf_counter()
        for offset in range(0, len(texts), 16):
            batch = ['query: ' + text for text in texts[offset:offset + 16]]
            progress('batch', field=field, offset=offset)
            truncation += sum(len(ids) > limit for ids in model.tokenizer(batch, truncation=False)['input_ids'])
            before = time.perf_counter()
            for attempt in range(2):
                try:
                    value = model.encode(batch, batch_size=16, normalize_embeddings=True,
                                         convert_to_numpy=True, show_progress_bar=False)
                    if value.shape != (len(batch), 384) or not np.isfinite(value).all():
                        raise ValueError('INVALID_ENCODER_VECTOR')
                    if np.any(np.linalg.norm(value, axis=1) == 0):
                        raise ValueError('ZERO_ENCODER_VECTOR')
                    break
                except Exception:
                    if attempt:
                        raise
                    retry_count += 1
            durations.append(time.perf_counter() - before)
            vectors.append(value)
        elapsed = time.perf_counter() - stage
        matrix = np.concatenate(vectors) if vectors else np.empty((0, 384), dtype=np.float32)
        np.save(OUT / f'{field}-vectors.npy', matrix)
        # Cache prototype: content keys preserve field identity; per-record mapping retained.
        index = {text: number for number, text in enumerate(texts)}
        write(OUT / f'{field}-mapping.json', [{'record_key': row['record_key'],
              'vector_index': index.get(row[field]) if row['quality'].get(field) == 'VALID' else None,
              'quality': row['quality'].get(field)} for row in rows])
        repeat = model.encode(warm, batch_size=16, normalize_embeddings=True, show_progress_bar=False)
        delta = float(np.max(np.abs(repeat - matrix[:len(warm)]))) if len(warm) else None
        results[field] = {'eligible_record_fields': len(eligible), 'masked_record_fields': len(rows)-len(eligible),
            'unique_texts_encoded': len(texts), 'dedup_hits': len(eligible)-len(texts),
            'truncated_unique_texts': truncation, 'max_tokens': limit, 'batch_size': 16,
            'stage_seconds': elapsed, 'unique_texts_per_second': len(texts)/elapsed if elapsed else None,
            'batch_p50_seconds': float(np.percentile(durations, 50)) if durations else None,
            'batch_p95_seconds': float(np.percentile(durations, 95)) if durations else None,
            'microbenchmark_seconds_3_repetitions': micro, 'repeat_max_abs_delta': delta,
            'retry_count': retry_count, 'vector_file_sha256': file_hash(OUT / f'{field}-vectors.npy')}
    write(OUT / 'inference-report.json', {'status': 'PERFORMANCE_TRIAL_ONLY', 'model': manifest['model_id'],
        'revision': manifest['revision'], 'bundle_digest': manifest['bundle_digest'], 'device': 'cpu',
        'dtype': 'float32', 'threads': 2, 'dimension': 384, 'cold_load_seconds': load_seconds,
        'fields': results, 'runtime': {name: importlib.metadata.version(name) for name in
        ['torch', 'sentence-transformers', 'transformers', 'numpy', 'tokenizers']},
        'quality_metrics': None, 'pipeline_benchmark': 'NOT_RUN_PENDING_BACKEND_LOCK_APPROVAL'})
    progress('done')


def read_progress(path):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (PermissionError, FileNotFoundError):
        # Retain last known deadline while Windows temporarily denies a reader.
        return None


def supervise():
    import psutil
    prepare()
    env = os.environ.copy()
    env.update({'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1', 'HF_HUB_DISABLE_TELEMETRY': '1',
                'TOKENIZERS_PARALLELISM': 'false', 'OMP_NUM_THREADS': '2', 'MKL_NUM_THREADS': '2'})
    executable = OUT / 'env/Scripts/python.exe'
    command = [str(executable), '-B', '-X', 'utf8', str(Path(__file__).resolve()), '--encoder']
    write(OUT / 'inference-command.json', command)
    started, peak_encoder, peak_tree, violation = time.monotonic(), 0, 0, None
    with (OUT / 'inference.log').open('w', encoding='utf-8') as log:
        child = subprocess.Popen(command, env=env, stdout=log, stderr=subprocess.STDOUT)
        process = psutil.Process(child.pid)
        phase, checkpoint = 'load', started
        try:
            while child.poll() is None:
                try:
                    processes = [process] + process.children(recursive=True)
                    encoder_rss = sum(p.memory_info().rss for p in processes)
                    aggregate = encoder_rss + psutil.Process().memory_info().rss
                    peak_encoder, peak_tree = max(peak_encoder, encoder_rss), max(peak_tree, aggregate)
                    now = time.monotonic()
                    state = read_progress(OUT / 'progress.json')
                    if state and state['monotonic'] >= started:
                        phase = state['phase']
                        checkpoint = started if phase == 'load' else state['monotonic']
                    if encoder_rss > 3 * 1024**3 or aggregate > 8 * 1024**3:
                        violation = 'RSS_BUDGET_EXCEEDED'
                    elif now-started > 1800 or now-checkpoint > (300 if phase == 'load' else 120):
                        violation = 'TIMEOUT_BUDGET_EXCEEDED'
                    if violation:
                        break
                except psutil.NoSuchProcess:
                    pass
                time.sleep(0.05)
        finally:
            # A controller exception must not leave an unmonitored encoder alive.
            if child.poll() is None:
                try:
                    for descendant in process.children(recursive=True):
                        try:
                            descendant.kill()
                        except psutil.NoSuchProcess:
                            pass
                finally:
                    child.kill()
            code = child.wait()
    write(OUT / 'supervisor-report.json', {'exit_code': code, 'budget_violation': violation,
        'peak_encoder_process_tree_rss_bytes': peak_encoder, 'peak_trial_process_tree_rss_bytes': peak_tree,
        'whole_trial_seconds': time.monotonic()-started, 'sampling_interval_seconds': .05,
        'scope': 'trial controller+encoder; not full production workflow worker'})
    return code


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--encoder', action='store_true')
    args = parser.parse_args()
    if args.encoder:
        encoder()
    else:
        raise SystemExit(supervise())
