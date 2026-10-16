"""Field-only evidence over a supplied, pinned encoder; no model auto-download.

The callable returns vectors in input-text order; this adapter maps them back to
record IDs before joining pairs. Encoder process supervision belongs to runtime.
"""
import json
import math
from hashlib import sha256

import numpy as np

from .contracts import EncoderCheckpointError, EncoderIntegrityError, FieldSimilarity, cosine, eligible_text


def _digest(value):
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
        separators=(',', ':'), allow_nan=False).encode()).hexdigest()


class EmbeddingAgent:
    def __init__(self, encode, *, encoder_digest, preprocessing_digest, dimension,
                 batch_size=16, cache=None, runtime_digest='UNSPECIFIED_CALLABLE_TEST_RUNTIME',
                 cache_version='record_vectors_v1', on_batch_stored=None):
        if not encoder_digest or not preprocessing_digest or dimension <= 0 or batch_size <= 0:
            raise ValueError('INVALID_ENCODER_IDENTITY_OR_BUDGET')
        self.encode = encode
        self.encoder_digest, self.preprocessing_digest = encoder_digest, preprocessing_digest
        self.runtime_digest = runtime_digest
        self.dimension, self.batch_size = dimension, batch_size
        self.cache = cache if cache is not None else {}
        self.cache_version = cache_version
        self.on_batch_stored = on_batch_stored or (lambda: None)
        self.last_coverage = {}

    def evaluate(self, records, keys, *, owner_id=None, run_id=None):
        scopes = {(key.owner_id, key.run_id) for key in keys}
        if owner_id is not None or run_id is not None:
            if not owner_id or not run_id:
                raise ValueError('EMBEDDING_SCOPE_REQUIRED')
            scopes.add((owner_id, run_id))
        if len(scopes) > 1 or len(set(keys)) != len(keys):
            raise ValueError('EMBEDDING_SCOPE_OR_DUPLICATE_PAIR')
        index = {row['id']: row for row in records}
        if len(index) != len(records):
            raise ValueError('DUPLICATE_SNAPSHOT_ID')
        if any(key.left_id not in index or key.right_id not in index for key in keys):
            raise ValueError('EMBEDDING_ENDPOINT_MISSING')
        if not scopes:
            raise ValueError('EMBEDDING_SCOPE_REQUIRED_FOR_EMPTY_CANDIDATES')
        scope = next(iter(scopes))
        fields, identities = {}, {}
        # Local to this evaluation: one validation/normalization per content key.
        # Keep the original vectors for durable cache and retrieval consumers.
        prepared = {}
        def prepare(identity, vector):
            if identity not in prepared:
                valid = cosine(vector, vector, self.dimension)
                if valid.status != 'OK':
                    prepared[identity] = valid
                else:
                    scale = max(map(abs, vector))
                    scaled = [value / scale for value in vector]
                    norm = math.sqrt(math.fsum(value * value for value in scaled))
                    prepared[identity] = np.asarray(scaled, dtype=np.float64) / norm
            return prepared[identity]
        self.last_coverage = {}
        for field, text_field in [('name', 'name_unicode'), ('address', 'address')]:
            values, pending = {}, {}
            eligible = hits = 0
            for rid, row in index.items():
                normalized = row['normalized_fields']
                quality = normalized.get('_field_quality', {}).get(field, 'MISSING')
                text, unavailable = eligible_text(normalized.get(text_field), quality)
                if unavailable:
                    values[rid] = unavailable
                    continue
                eligible += 1
                identity = _digest([scope, rid, _digest(normalized), field, quality, _digest(text),
                                    self.encoder_digest, self.preprocessing_digest, self.runtime_digest, self.dimension])
                if self.cache_version == 'text_vectors_v2':
                    identity = _digest([self.cache_version, scope, field, quality, _digest(text),
                        self.encoder_digest, self.preprocessing_digest, self.runtime_digest, self.dimension])
                identities[(rid, field)] = identity
                if identity in self.cache:
                    vector = self.cache[identity]
                    valid = prepare(identity, vector)
                    if isinstance(valid, FieldSimilarity):
                        values[rid] = FieldSimilarity('ERROR', None, 'CACHE_VECTOR_INVALID')
                    else:
                        values[rid] = vector
                        hits += 1
                else:
                    pending.setdefault(text, []).append((rid, identity))
            texts = sorted(pending)
            for offset in range(0, len(texts), self.batch_size):
                batch = texts[offset:offset+self.batch_size]
                try:
                    vectors = list(self.encode(batch, field))
                    if len(vectors) != len(batch):
                        raise ValueError('ENCODER_BATCH_COVERAGE')
                    for text, vector in zip(batch, vectors, strict=True):
                        # Convert numpy scalar types at the encoder boundary, not during cosine validation.
                        vector = tuple(vector)
                        valid = prepare(pending[text][0][1], vector)
                        for rid, identity in pending[text]:
                            prepared[identity] = valid
                            if isinstance(valid, FieldSimilarity):
                                values[rid] = valid
                            else:
                                self.cache[identity] = vector
                                values[rid] = vector
                except Exception as error:
                    from masterone.mdm.job_queue import OwnershipLostError
                    if isinstance(error, (OwnershipLostError, EncoderIntegrityError)) or 'IDENTITY' in str(error) or 'MISMATCH' in str(error):
                        raise
                    if self.cache_version == 'text_vectors_v2':
                        # Fail promptly with the real API/limit error; do not spend the rest of a failed run.
                        raise
                    # No model substitution; process supervisor supplies detailed trace.
                    for text in batch:
                        for rid, _ in pending[text]:
                            values[rid] = FieldSimilarity('ERROR', None, 'ENCODER_BATCH_ERROR')
                else:
                    # Durability failures must propagate, including for the
                    # legacy local encoder, rather than becoming model errors.
                    try:
                        self.on_batch_stored()
                    except Exception as error:
                        from masterone.mdm.job_queue import OwnershipLostError
                        if isinstance(error, (OwnershipLostError, EncoderIntegrityError)):
                            raise
                        raise EncoderCheckpointError('ENCODER_CHECKPOINT_FAILED') from error
            fields[field] = values
            self.last_coverage[field] = {'eligible_records': eligible, 'unique_texts': len(texts),
                'cache_hits': hits, 'masked_records': len(records)-eligible,
                'error_records': sum(isinstance(v, FieldSimilarity) and v.status == 'ERROR'
                                     for v in values.values())}
            available = eligible - self.last_coverage[field]['error_records']
            self.last_coverage[field]['available_records'] = available
            self.last_coverage[field]['eligible_success_ratio'] = available / eligible if eligible else None
        # Transient snapshot vectors for retrieval; never serialize these into pair/browser payloads.
        self.last_vectors = fields
        output = []
        for key in keys:
            similarities = {}
            for field, values in fields.items():
                left, right = values[key.left_id], values[key.right_id]
                unavailable = [v for v in (left, right) if isinstance(v, FieldSimilarity)]
                if unavailable:
                    similarities[field] = next((v for v in unavailable if v.status == 'ERROR'), unavailable[0])
                else:
                    a = prepared[identities[key.left_id, field]]
                    b = prepared[identities[key.right_id, field]]
                    value = float(np.dot(a, b))
                    similarities[field] = FieldSimilarity('OK', max(-1., min(1., value)))
            output.append({'key': key, 'fields': similarities,
                'encoder_digest': self.encoder_digest, 'preprocessing_digest': self.preprocessing_digest,
                'runtime_digest': self.runtime_digest,
                'vector_identities': {field: [identities.get((rid, field)) for rid in
                                             (key.left_id, key.right_id)] for field in fields},
                'trace_id': key.digest})
        return output
