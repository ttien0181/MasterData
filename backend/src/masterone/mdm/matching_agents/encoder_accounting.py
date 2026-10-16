"""Durable batch intents distinguish unknown interrupted work from zero usage."""
from uuid import uuid4


def encode_accounted(runtime, texts, field, persist):
    batch_id = str(uuid4())
    base = {'batch_id': batch_id, 'field': field, 'texts': len(texts), 'external_requests': 0}
    persist({**base, 'event': 'INTENT'})
    before_attempts = runtime.batch_attempts
    before_tokens = runtime.usage.get(field, {}).get('input_tokens', 0)
    before_unknown = runtime.usage.get(field, {}).get('unknown_usage_requests', 0)
    if getattr(runtime, 'external', False):
        # Every retry has its own durable HTTP intent and receipt, including error usage.
        runtime.receipt = lambda row: persist({**row, 'batch_id': batch_id})
    try:
        vectors = runtime.encode(texts, field)
    except Exception:
        try:
            persist({**base, 'event': 'FAILED', 'input_tokens': None,
                     'batch_attempts': runtime.batch_attempts - before_attempts,
                     'external_requests': runtime.batch_attempts - before_attempts if getattr(runtime, 'external', False) else 0})
        except Exception:
            # Lease loss can prevent a final receipt; the durable intent remains unknown.
            pass
        raise
    after_tokens = runtime.usage.get(field, {}).get('input_tokens')
    unknown = runtime.usage.get(field, {}).get('unknown_usage_requests', 0) > before_unknown
    persist({**base, 'event': 'SUCCESS', 'batch_attempts': runtime.batch_attempts - before_attempts,
             'external_requests': runtime.batch_attempts - before_attempts if getattr(runtime, 'external', False) else 0,
             'input_tokens': after_tokens - before_tokens if after_tokens is not None and not unknown else None})
    return vectors
