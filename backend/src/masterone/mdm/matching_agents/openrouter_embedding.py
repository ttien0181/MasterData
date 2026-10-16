"""Sequential OpenRouter embeddings with durable per-attempt usage and admission caps.

No texts, credentials, vectors or provider error bodies enter usage receipts.
Unknown usage retains the conservative UTF-8 token reservation across retries.
"""
import math
import time
from pathlib import Path
from uuid import uuid4

import httpx

from masterone.config import get_settings
from masterone.mdm.generation_staging import digest
from masterone.mdm.pipeline.scoring_engine import file_hash

from .contracts import EncoderIntegrityError, cosine

PREPROCESSOR = {'version': 'openrouter_fields_v1', 'name': 'name_unicode', 'address': 'address',
                'prefix': '', 'truncate': False, 'cache_version': 'text_vectors_v2',
                'mask': ['MISSING', 'INVALID', 'PLACEHOLDER', 'SHARED']}


def runtime_spec():
    settings = get_settings()
    endpoint = settings.embedding_openrouter_base_url.rstrip('/')
    if endpoint != 'https://openrouter.ai/api/v1':
        raise EncoderIntegrityError('EMBEDDING_ENDPOINT_IDENTITY_MISMATCH')
    identity = {'provider': 'openrouter', 'model_id': settings.embedding_model,
                'response_model_ids': [settings.embedding_model, 'text-embedding-3-small'],
                'revision': 'openrouter_embeddings_v1', 'dimension': settings.embedding_dimensions,
                'endpoint': endpoint, 'encoding_format': 'float', 'cache_version': 'text_vectors_v2'}
    identity['bundle_digest'] = digest(identity)
    budget = {'batch_size': settings.embedding_batch_size, 'concurrency': 1,
              'batch_seconds': settings.embedding_timeout_seconds, 'batch_retries': settings.embedding_max_retries,
              'run_seconds': 1800, 'worker_rss_bytes': settings.embedding_worker_rss_bytes,
              'max_requests': settings.embedding_max_requests_per_run,
              'max_tokens': settings.embedding_max_tokens_per_run, 'max_cost_usd': settings.embedding_budget_usd,
              'usd_per_million_tokens': settings.embedding_usd_per_million_tokens,
              'max_text_bytes': 8192, 'max_batch_bytes': 32768}
    return identity, budget


def advanced_config(base, fallback):
    if fallback not in ('fail_operation', 'rule_only'):
        raise ValueError('UNPINNED_FALLBACK_MODE')
    identity, budget = runtime_spec()
    return {**base, 'kind': 'RULE_WITH_EMBEDDING_EVIDENCE',
            'config_schema_version': 'matching_profile_openrouter_v1', 'encoder_identity': identity,
            'preprocessing': PREPROCESSOR, 'resource_budget': budget,
            'feature_schema_identity': digest({'version': 'openrouter_evidence_v1',
                'rule': base['feature_schema_identity'], 'fields': ['name_cosine', 'address_cosine', 'field_status', 'quality_mask']}),
            'agent_code_identity': {p.name: file_hash(p) for p in sorted(Path(__file__).parent.glob('*.py'))},
            'assessment_auto_enabled': get_settings().pair_assessment_auto_enabled,
            'fallback_mode': fallback, 'validation_scope': 'OPENROUTER_MIGRATION_UNCALIBRATED_NO_QUALITY_CERTIFICATE'}


class OpenRouterEmbeddingRuntime:
    external = True

    def __init__(self, identity, budget, progress=None, *, transport=None):
        self.identity, self.budget = identity, budget
        self.progress = progress or (lambda *_: None)
        self.transport = transport
        self.client = None
        self.started = time.monotonic()
        self.peak_rss = self.calls = self.retry_count = self.batch_attempts = 0
        self.usage = {}
        self.receipt = lambda _: None
        self.reserved_requests = self.reserved_tokens = 0
        self.reserved_cost = 0.

    def restore_budget(self, receipts):
        intents = {row['request_id']: row for row in receipts if row.get('event') == 'HTTP_INTENT'}
        self.reserved_requests = len(intents)
        self.reserved_tokens = sum(row['reserved_tokens'] for row in intents.values())
        self.reserved_cost = sum(row['reserved_cost_usd'] for row in intents.values())
        responses = {row['request_id']: row for row in receipts
                     if row.get('event') in ('HTTP_RESPONSE', 'HTTP_FAILED') and row.get('request_id') in intents}
        for request_id, row in responses.items():
            intent = intents[request_id]
            self.reserved_tokens += max(0, (row.get('input_tokens') or 0) - intent['reserved_tokens'])
            self.reserved_cost += max(0., (row.get('api_cost_usd') or row.get('estimated_cost_usd') or 0.)
                                     - intent['reserved_cost_usd'])

    def __enter__(self):
        identity, budget = runtime_spec()
        if self.identity != identity or self.budget != budget:
            raise EncoderIntegrityError('EMBEDDING_PROFILE_RUNTIME_IDENTITY_MISMATCH')
        key = get_settings().embedding_openrouter_api_key
        if not key:
            raise ValueError('EMBEDDING_API_KEY_MISSING')
        self.client = httpx.Client(transport=self.transport, timeout=self.budget['batch_seconds'],
            follow_redirects=False, headers={'Authorization': 'Bearer ' + key},
            limits=httpx.Limits(max_connections=1, max_keepalive_connections=1))
        return self

    def __exit__(self, *_):
        if self.client is not None:
            self.client.close()

    def _check_time(self):
        if time.monotonic() - self.started >= self.budget['run_seconds']:
            raise ValueError('EMBEDDING_RUN_TIMEOUT')

    def _reserve(self, tokens):
        self._check_time()
        cost = tokens * self.budget['usd_per_million_tokens'] / 1000000
        if (self.reserved_requests + 1 > self.budget['max_requests']
                or self.reserved_tokens + tokens > self.budget['max_tokens']
                or self.reserved_cost + cost > self.budget['max_cost_usd']):
            raise ValueError('EMBEDDING_RUN_BUDGET_EXCEEDED')
        self.reserved_requests += 1
        self.reserved_tokens += tokens
        self.reserved_cost += cost
        return cost

    def encode(self, texts, field):
        if field not in ('name', 'address') or not texts or len(texts) > self.budget['batch_size']:
            raise ValueError('EMBEDDING_BATCH_CONTRACT')
        if any(not isinstance(text, str) or not text.strip() for text in texts):
            raise ValueError('EMBEDDING_MISSING_TEXT')
        lengths = [len(text.encode('utf-8')) for text in texts]
        if max(lengths) > self.budget['max_text_bytes'] or sum(lengths) > self.budget['max_batch_bytes']:
            raise ValueError('EMBEDDING_INPUT_LIMIT_EXCEEDED')
        # UTF-8 bytes plus per-item overhead bound tokens without a local tokenizer/model.
        reserved_tokens = sum(lengths) + 8 * len(texts)
        self.calls += 1
        for attempt in range(self.budget['batch_retries'] + 1):
            cost = self._reserve(reserved_tokens)
            request_id = str(uuid4())
            base = {'request_id': request_id, 'provider': 'openrouter', 'model': self.identity['model_id'],
                    'field': field, 'external_requests': 1, 'reserved_tokens': reserved_tokens,
                    'reserved_cost_usd': cost}
            self.receipt({**base, 'event': 'HTTP_INTENT'})
            self.batch_attempts += 1
            started = time.monotonic()
            status, payload, retryable, error = None, {}, False, None
            try:
                remaining = self.budget['run_seconds'] - (time.monotonic() - self.started)
                response = self.client.post(self.identity['endpoint'] + '/embeddings',
                    json={'model': self.identity['model_id'], 'input': texts,
                          'dimensions': self.identity['dimension'], 'encoding_format': 'float',
                          'provider': {'allow_fallbacks': False}},
                    timeout=min(self.budget['batch_seconds'], max(.01, remaining)))
                status = response.status_code
                try:
                    payload = response.json()
                    if not isinstance(payload, dict):
                        payload = {}
                except ValueError:
                    payload = {}
                if status != 200 or 'error' in payload:
                    retryable = status in (408, 429, 500, 502, 503, 504, 524, 529)
                    error = 'EMBEDDING_HTTP_' + str(status)
            except httpx.TransportError:
                retryable, error = True, 'EMBEDDING_TRANSPORT_ERROR'
            usage = payload.get('usage') or {}
            tokens = usage.get('prompt_tokens', usage.get('total_tokens')) if isinstance(usage, dict) else None
            if type(tokens) is not int or tokens < 0:
                tokens = None
            actual_cost = usage.get('cost') if isinstance(usage, dict) else None
            if (isinstance(actual_cost, bool) or not isinstance(actual_cost, (int, float))
                    or not math.isfinite(actual_cost) or actual_cost < 0):
                actual_cost = None
            row = self.usage.setdefault(field, {'batches': 0, 'texts': 0, 'input_tokens': 0,
                'external_requests': 0, 'reported_cost_usd': 0., 'unknown_usage_requests': 0,
                'unknown_cost_requests': 0, 'estimated_cost_usd': 0.})
            row['external_requests'] += 1
            row['input_tokens'] += tokens or 0
            row['reported_cost_usd'] += actual_cost or 0
            row['unknown_usage_requests'] += tokens is None
            row['unknown_cost_requests'] += actual_cost is None
            estimated = tokens * self.budget['usd_per_million_tokens'] / 1000000 if tokens is not None else None
            row['estimated_cost_usd'] += estimated or 0
            # Retain reservations for unknown responses; account for any provider excess.
            self.reserved_tokens += max(0, (tokens or 0) - reserved_tokens)
            self.reserved_cost += max(0., (actual_cost or estimated or 0.) - cost)
            self.receipt({**base, 'event': 'HTTP_FAILED' if error else 'HTTP_RESPONSE',
                'http_status': status, 'input_tokens': tokens, 'api_cost_usd': actual_cost,
                'estimated_cost_usd': estimated, 'latency_seconds': time.monotonic() - started,
                'error_code': error})
            self.progress('embedding', self.calls, self.calls + 1)
            self._check_time()
            if self.reserved_cost > self.budget['max_cost_usd'] or self.reserved_tokens > self.budget['max_tokens']:
                raise ValueError('EMBEDDING_RUN_BUDGET_EXCEEDED')
            if error:
                if retryable and attempt < self.budget['batch_retries']:
                    self.retry_count += 1
                    time.sleep(min(2 ** attempt, 4))
                    continue
                raise ValueError(error)
            # OpenRouter currently returns the upstream ID without its routing prefix.
            # Both exact aliases are pinned; no other model is accepted.
            if payload.get('model') not in self.identity['response_model_ids']:
                raise EncoderIntegrityError('EMBEDDING_RESPONSE_MODEL_IDENTITY_MISMATCH')
            data = payload.get('data')
            if (not isinstance(data, list) or len(data) != len(texts)
                    or any(not isinstance(item, dict) or type(item.get('index')) is not int for item in data)
                    or sorted(item['index'] for item in data) != list(range(len(texts)))):
                raise EncoderIntegrityError('EMBEDDING_RESPONSE_COVERAGE_MISMATCH')
            vectors = [item.get('embedding') for item in sorted(data, key=lambda item: item['index'])]
            if any(not isinstance(vector, list) or cosine(vector, vector, self.identity['dimension']).status != 'OK'
                   for vector in vectors):
                raise EncoderIntegrityError('EMBEDDING_RESPONSE_VECTOR_MISMATCH')
            row['batches'] += 1
            row['texts'] += len(texts)
            return vectors
