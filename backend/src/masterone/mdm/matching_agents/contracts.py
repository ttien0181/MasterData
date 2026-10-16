"""Agent evidence has a canonical scoped identity and explicit availability."""
import json
import math
from dataclasses import dataclass
from hashlib import sha256
from typing import Literal

Status = Literal['OK', 'NOT_AVAILABLE', 'OUT_OF_SCOPE', 'ERROR']


class EncoderIntegrityError(ValueError):
    """Pinned model/runtime identity failed; never eligible for degraded fallback."""


class EncoderCheckpointError(ValueError):
    """Validated vectors could not be persisted; never a provider fallback."""


@dataclass(frozen=True, order=True)
class PairKey:
    owner_id: str
    run_id: str
    left_id: str
    right_id: str

    def __post_init__(self):
        if any(not isinstance(value, str) or not value for value in
               (self.owner_id, self.run_id, self.left_id, self.right_id)):
            raise ValueError('PAIR_IDENTITY_EMPTY')
        if self.left_id >= self.right_id:
            raise ValueError('PAIR_NOT_CANONICAL')

    @classmethod
    def canonical(cls, owner_id, run_id, a, b):
        return cls(owner_id, run_id, *sorted((a, b)))

    @property
    def digest(self):
        payload = ['masterone_pair_key_v1', self.owner_id, self.run_id, self.left_id, self.right_id]
        return sha256(json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


@dataclass(frozen=True)
class FieldSimilarity:
    status: Status
    cosine: float | None
    reason: str | None = None

    def __post_init__(self):
        if self.status not in ('OK', 'NOT_AVAILABLE', 'OUT_OF_SCOPE', 'ERROR'):
            raise ValueError('INVALID_AGENT_STATUS')
        if self.status == 'OK':
            if (isinstance(self.cosine, bool) or not isinstance(self.cosine, (int, float))
                    or not math.isfinite(self.cosine) or not -1 <= self.cosine <= 1):
                raise ValueError('INVALID_COSINE')
        elif self.cosine is not None or not self.reason:
            raise ValueError('UNAVAILABLE_SCORE_REQUIRES_NULL_AND_REASON')


def cosine(left, right, dimension):
    if dimension <= 0 or len(left) != dimension or len(right) != dimension:
        return FieldSimilarity('ERROR', None, 'DIMENSION_MISMATCH')
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
           for v in (*left, *right)):
        return FieldSimilarity('ERROR', None, 'NONFINITE_OR_INVALID_VECTOR')
    # Scale before dot/norm to avoid overflow for finite large values.
    scale_left, scale_right = max(map(abs, left)), max(map(abs, right))
    if scale_left == 0 or scale_right == 0:
        return FieldSimilarity('ERROR', None, 'ZERO_VECTOR')
    a, b = [v / scale_left for v in left], [v / scale_right for v in right]
    value = math.fsum(x*y for x, y in zip(a, b, strict=True)) / (
        math.sqrt(math.fsum(x*x for x in a)) * math.sqrt(math.fsum(y*y for y in b)))
    return FieldSimilarity('OK', max(-1., min(1., value)))


def eligible_text(value, quality):
    """No new normalizer or placeholder policy: honor production quality masks."""
    if quality != 'VALID':
        return None, FieldSimilarity('NOT_AVAILABLE', None, 'QUALITY_' + str(quality))
    if value is None or not isinstance(value, str) or not value.strip():
        return None, FieldSimilarity('NOT_AVAILABLE', None, 'MISSING_TEXT')
    return value, None
