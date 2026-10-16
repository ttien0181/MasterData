"""I1: pinned local subprocess, supervised batches, no HTTP model imports."""
import json
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

from masterone.config import get_settings
from masterone.mdm.generation_staging import digest
from masterone.mdm.pipeline.scoring_engine import file_hash

from .contracts import EncoderIntegrityError

REVISION = '614241f622f53c4eeff9890bdc4f31cfecc418b3'
BUNDLE = 'd9ffddf2ca5476f2c9ae023e921941c9db614a414e607c4a30bd82c7a88693d6'
LOCK = '291fea99bb0805521e318cfd69d2a2aca9fcb7d7d6341d62ba92b37a9c0c7141'
LINUX_LOCK = '763682254cdd498b21bee141022097b0fa0ffa7956abb0913d3dfc13b9838a0f'
PREPROCESSOR = {'version': 'e5_fields_v1', 'name': 'name_unicode', 'address': 'address',
                'prefix': 'query: ', 'name_tokens': 128, 'address_tokens': 512,
                'mask': ['MISSING', 'INVALID', 'PLACEHOLDER', 'SHARED'], 'normalize_vectors': True}
BUDGET = {'batch_size': 16, 'threads': 2, 'encoder_rss_bytes': 3*1024**3,
          'worker_rss_bytes': 8*1024**3, 'load_seconds': 300, 'batch_seconds': 120,
          'run_seconds': 1800, 'batch_retries': 1}


def runtime_spec():
    configured = get_settings().embedding_runtime_root
    if not configured:
        raise ValueError('ENCODER_RUNTIME_NOT_CONFIGURED')
    root = Path(configured).resolve()
    manifest = json.loads((root / 'model-manifest.json').read_text(encoding='utf-8'))
    lock, python = (LINUX_LOCK, '3.11') if sys.platform == 'linux' else (LOCK, '3.13.11')
    if sys.platform not in ('linux', 'win32'):
        raise ValueError('ENCODER_PLATFORM_NOT_APPROVED')
    if (manifest.get('bundle_digest') != BUNDLE or manifest.get('revision') != REVISION
            or file_hash(root / 'requirements-lock.txt') != lock):
        raise ValueError('ENCODER_APPROVED_IDENTITY_MISMATCH')
    bundle = {k: v for k, v in manifest.items() if k != 'bundle_digest'}
    if digest(bundle) != BUNDLE:
        raise ValueError('ENCODER_BUNDLE_MANIFEST_CORRUPT')
    identity = {'model_id': 'intfloat/multilingual-e5-small', 'revision': REVISION,
                'bundle_digest': BUNDLE, 'dimension': 384, 'dtype': 'float32', 'device': 'cpu',
                'runtime_lock_sha256': lock, 'worker_code_sha256': file_hash(Path(__file__).with_name('encoder_worker.py')),
                'python': python, 'platform': sys.platform}
    return root, identity


def runtime_python(root):
    return root / ('env/bin/python' if sys.platform == 'linux' else 'env/Scripts/python.exe')


def advanced_config(base, fallback):
    if fallback not in ('fail_operation', 'rule_only'):
        raise ValueError('UNPINNED_FALLBACK_MODE')
    _, identity = runtime_spec()
    return {**base, 'kind': 'RULE_WITH_EMBEDDING_EVIDENCE', 'config_schema_version': 'matching_profile_agents_v1',
            'encoder_identity': identity, 'preprocessing': PREPROCESSOR, 'resource_budget': BUDGET,
            'feature_schema_identity': digest({'version': 'matching_agents_evidence_v1',
                'rule': base['feature_schema_identity'], 'fields': ['name_cosine', 'address_cosine', 'field_status', 'quality_mask']}),
            'agent_code_identity': {p.name: file_hash(p) for p in sorted(Path(__file__).parent.glob('*.py'))},
            'assessment_auto_enabled': get_settings().pair_assessment_auto_enabled,
            'fallback_mode': fallback, 'validation_scope': 'TECHNICAL_EVIDENCE_TRIAL_ONLY_NO_QUALITY_CERTIFICATE'}


class EncoderRuntime:
    def __init__(self, identity, budget, progress=None):
        self.identity, self.budget = identity, budget
        self.progress = progress or (lambda *_: None)
        self.child = None
        self.responses = queue.Queue(maxsize=2)
        self.started = time.monotonic()
        self.peak_rss = 0
        self.calls = 0
        self.retry_count = 0
        self.batch_attempts = 0
        self.usage = {}

    def __enter__(self):
        root, actual = runtime_spec()
        if actual != self.identity or self.budget != BUDGET:
            raise ValueError('ENCODER_PROFILE_RUNTIME_MISMATCH')
        env = os.environ.copy()
        for key in ('PYTHONPATH', 'PYTHONHOME'):
            env.pop(key, None)
        env.update({'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1', 'HF_HUB_DISABLE_TELEMETRY': '1',
                    'TOKENIZERS_PARALLELISM': 'false', 'OMP_NUM_THREADS': '2', 'MKL_NUM_THREADS': '2'})
        self.responses = queue.Queue(maxsize=2)
        self.child = subprocess.Popen([str(runtime_python(root)), '-B', '-X', 'utf8',
            str(Path(__file__).with_name('encoder_worker.py')), str(root)], env=env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding='utf-8', bufsize=1)
        child, responses = self.child, self.responses
        def reader():
            try:
                for line in child.stdout:
                    responses.put(json.loads(line))
            except Exception:
                responses.put({'error': 'ENCODER_INVALID_RESPONSE'})
            finally:
                responses.put({'error': 'ENCODER_EXITED'})
        threading.Thread(target=reader, daemon=True, name='encoder-response').start()
        try:
            ready = self._receive(self.budget['load_seconds'])
            if ready.get('identity') != self.identity or ready.get('status') != 'READY':
                raise ValueError('ENCODER_READY_IDENTITY_MISMATCH')
            return self
        except BaseException:
            self.close()
            raise

    def _receive(self, seconds):
        import psutil
        deadline = time.monotonic() + seconds
        process = psutil.Process(self.child.pid)
        while True:
            now = time.monotonic()
            if now > deadline or now-self.started > self.budget['run_seconds']:
                self.close()
                raise ValueError('ENCODER_TIMEOUT')
            try:
                rss = sum(p.memory_info().rss for p in [process] + process.children(recursive=True))
                self.peak_rss = max(self.peak_rss, rss)
                if rss > self.budget['encoder_rss_bytes']:
                    self.close()
                    raise ValueError('ENCODER_RSS_BUDGET')
            except psutil.NoSuchProcess:
                pass
            try:
                reply = self.responses.get(timeout=.05)
                if reply.get('error'):
                    if reply.get('integrity'):
                        raise EncoderIntegrityError(reply['error'])
                    raise ValueError(reply['error'])
                return reply
            except queue.Empty:
                self.progress('embedding', self.calls, self.calls + 1)

    def encode(self, texts, field):
        if field not in ('name', 'address') or not texts or len(texts) > self.budget['batch_size']:
            raise ValueError('ENCODER_BATCH_CONTRACT')
        if any(not isinstance(text, str) or not text.strip() for text in texts):
            raise ValueError('ENCODER_MISSING_TEXT')
        self.calls += 1
        request = {'request_id': self.calls, 'field': field, 'texts': texts}
        for attempt in range(self.budget['batch_retries'] + 1):
            try:
                self.batch_attempts += 1
                self.child.stdin.write(json.dumps(request, ensure_ascii=False, allow_nan=False) + '\n')
                self.child.stdin.flush()
                reply = self._receive(self.budget['batch_seconds'])
                break
            except (ValueError, BrokenPipeError, OSError) as error:
                retryable = isinstance(error, (BrokenPipeError, OSError)) or str(error) in ('ENCODER_TIMEOUT', 'ENCODER_EXITED')
                if not retryable or attempt == self.budget['batch_retries']:
                    raise
                self.retry_count += 1
                self.close()
                self.__enter__()  # Revalidate the same frozen pins; never select another model.
        if reply.get('request_id') != self.calls or reply.get('field') != field:
            raise ValueError('ENCODER_BATCH_IDENTITY_MISMATCH')
        self.record_usage(field, texts, reply)
        return reply['vectors']

    def record_usage(self, field, texts, reply):
        tokens = reply.get('input_tokens')
        if type(tokens) is not int or tokens < 0:
            raise ValueError('ENCODER_USAGE_MISSING')
        row = self.usage.setdefault(field, {'batches': 0, 'texts': 0, 'input_tokens': 0})
        row['batches'] += 1
        row['texts'] += len(texts)
        row['input_tokens'] += tokens

    def close(self):
        if self.child is not None:
            if self.child.poll() is None:
                import psutil
                try:
                    for child in psutil.Process(self.child.pid).children(recursive=True):
                        child.kill()
                except psutil.NoSuchProcess:
                    pass
                self.child.kill()
            self.child.wait(timeout=5)
            for pipe in (self.child.stdin, self.child.stdout):
                pipe.close()

    def __exit__(self, *_):
        self.close()
