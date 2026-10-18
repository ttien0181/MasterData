"""Real provider SSE transport; only the structured summary is exposed incrementally."""
import asyncio
import json
import time

import httpx

from masterone.llm import gateway as g
from masterone.llm.policy import (PROVIDERS, PROVIDER_SKIP_CODES, PolicyDenied, PolicyEnforcer,
                                 allowed_payload, provider_model, validate_output)


def client(settings):
    return httpx.AsyncClient(timeout=settings.llm_request_timeout_seconds, follow_redirects=False)


def summary_prefix(text):
    """Decode only complete JSON string characters, including paired UTF-16 escapes."""
    decoder = json.JSONDecoder()
    index = 0
    def whitespace(i):
        while i < len(text) and text[i].isspace():
            i += 1
        return i
    index = whitespace(index)
    if index >= len(text) or text[index] != '{':
        return ''
    index += 1
    while True:
        try:
            index = whitespace(index)
            key, index = decoder.raw_decode(text, index)
            index = whitespace(index)
            if text[index] != ':':
                return ''
            index = whitespace(index + 1)
            if key != 'summary_vi':
                _, index = decoder.raw_decode(text, index)
                index = whitespace(index)
                if text[index] != ',':
                    return ''
                index += 1
                continue
            if text[index] != '"':
                return ''
            index += 1
            start = index
            safe = index
            while index < len(text):
                char = text[index]
                if char == '"':
                    break
                if char == '\\':
                    if index + 1 >= len(text):
                        break
                    if text[index + 1] == 'u':
                        end = index + 6
                        if end > len(text):
                            break
                        point = int(text[index + 2:end], 16)
                        if 0xD800 <= point <= 0xDBFF:
                            if end + 6 > len(text) or text[end:end + 2] != '\\u':
                                break
                            low = int(text[end + 2:end + 6], 16)
                            if not 0xDC00 <= low <= 0xDFFF:
                                return ''
                            end += 6
                        elif 0xDC00 <= point <= 0xDFFF:
                            return ''
                        index = end
                    else:
                        index += 2
                else:
                    if ord(char) < 32 or 0xD800 <= ord(char) <= 0xDFFF:
                        return ''
                    index += 1
                safe = index
            return json.loads('"' + text[start:safe] + '"')
        except (ValueError, IndexError, TypeError):
            return ''


async def _packets(response):
    """Bound wire bytes, decode UTF-8 across network chunks, support SSE data lines."""
    import codecs
    decoder = codecs.getincrementaldecoder('utf-8')('strict')
    pending, lines, size = '', [], 0
    async for chunk in response.aiter_bytes():
        size += len(chunk)
        if size > g.MAX_RESPONSE_BYTES:
            raise g._InvalidError('RESPONSE_TOO_LARGE')
        pending += decoder.decode(chunk)
        while '\n' in pending:
            line, pending = pending.split('\n', 1)
            line = line.rstrip('\r')
            if not line:
                if lines:
                    yield '\n'.join(lines)
                    lines = []
            elif line.startswith('data:'):
                lines.append(line[5:].lstrip(' '))
    pending += decoder.decode(b'', final=True)
    if pending.strip() or lines:
        raise g._InvalidError('TRUNCATED_SSE')


async def generate_explanation(payload, settings, *, context, factory=None):
    """Yield ('delta', text) and one ('result', validated gateway response)."""
    purpose = 'EXPLANATION'
    payload = allowed_payload(purpose, payload)
    gate = PolicyEnforcer(settings, factory)
    request_id, cached = gate.reserve(purpose, payload, context, g.PROMPT_VERSION[purpose], g.SCHEMA_VERSION[purpose])
    if cached is not None:
        yield 'result', {**cached, 'usage': {}, 'request_id': request_id, 'cached': True, 'attempts': 0}
        return
    prompt = g._prompt(purpose, payload)
    last = None
    attempts = 0
    for provider in PROVIDERS:
        model = provider_model(settings, provider)
        for retry in range(settings.gemini_max_retries + 1):
            try:
                attempt_id = gate.attempt(request_id, purpose, provider, payload, context,
                                          g.PROMPT_VERSION[purpose], g.SCHEMA_VERSION[purpose])
            except PolicyDenied as exc:
                if exc.code not in PROVIDER_SKIP_CODES:
                    raise PolicyDenied(exc.code, attempts=attempts) from None
                last = PolicyDenied(exc.code, attempts=attempts)
                break
            attempts += 1
            started = time.monotonic()
            usage, finished, emitted = None, False, False
            status, error, skip, retry_call = 'SCHEMA_INVALID', 'SCHEMA_INVALID', False, False
            output_text, shown, finish_reason, model_version = '', '', None, model
            try:
                request = (g._gemini_request(settings, model, prompt, g.EXPLANATION_SCHEMA) if provider == 'gemini'
                           else g._openrouter_request(settings, purpose, model, prompt, g.EXPLANATION_SCHEMA))
                body = json.loads(request.data)
                url = request.full_url
                if provider == 'gemini':
                    url = url.replace(':generateContent', ':streamGenerateContent?alt=sse')
                else:
                    body.update(stream=True, stream_options={'include_usage': True})
                async with asyncio.timeout(settings.llm_request_timeout_seconds):
                    async with client(settings) as session:
                        async with session.stream('POST', url, headers=dict(request.header_items()), json=body) as response:
                            code = response.status_code
                            if code == 429 or (provider == 'openrouter' and code == 402):
                                status, error, skip = 'PROVIDER_QUOTA', 'HTTP_' + str(code), True
                                if provider == 'gemini':
                                    gate.block_gemini(settings.gemini_quota_cooldown_seconds)
                            elif code in (401, 403):
                                status, error, skip = 'PROVIDER_UNAVAILABLE', 'HTTP_' + str(code), True
                            elif code in g.RETRYABLE_HTTP:
                                retry_call = retry < settings.gemini_max_retries
                                status, error = ('HTTP_RETRYABLE' if retry_call else 'HTTP_ERROR'), 'HTTP_' + str(code)
                            elif code != 200:
                                status, error = 'HTTP_ERROR', 'PROVIDER_HTTP_ERROR'
                            else:
                                async for packet in _packets(response):
                                    if packet == '[DONE]' and provider == 'openrouter':
                                        continue
                                    chunk = json.loads(packet)
                                    if not isinstance(chunk, dict) or 'error' in chunk:
                                        raise g._InvalidError('INVALID_PACKET')
                                    if (provider == 'gemini' and 'usageMetadata' in chunk) or (provider == 'openrouter' and chunk.get('usage')):
                                        usage = g.provider_usage(provider, chunk)
                                    if provider == 'gemini':
                                        model_version = chunk.get('modelVersion', model_version)
                                        candidates = chunk.get('candidates', [])
                                        for candidate in candidates:
                                            if candidate.get('index', 0) != 0:
                                                continue
                                            finish_reason = candidate.get('finishReason', finish_reason)
                                            for part in candidate.get('content', {}).get('parts', []):
                                                if not part.get('thought') and isinstance(part.get('text'), str):
                                                    output_text += part['text']
                                    else:
                                        model_version = chunk.get('model', model_version)
                                        for choice in chunk.get('choices', []):
                                            if choice.get('index', 0) == 0:
                                                finish_reason = choice.get('finish_reason') or finish_reason
                                                output_text += choice.get('delta', {}).get('content') or ''
                                    if len(output_text.encode()) > g.MAX_RESPONSE_BYTES:
                                        raise g._InvalidError('OUTPUT_TOO_LARGE')
                                    prefix = summary_prefix(output_text)
                                    if prefix:
                                        # Provisional text obeys the same content/length rules as final output.
                                        validate_output(purpose, {'summary_vi': prefix, 'attention_fields': [],
                                                                  'limitations': []}, payload)
                                    if prefix and prefix.startswith(shown) and len(prefix) > len(shown):
                                        delta, shown = prefix[len(shown):], prefix
                                        emitted = True
                                        yield 'delta', delta
                                if finish_reason != ('STOP' if provider == 'gemini' else 'stop'):
                                    raise g._InvalidError('FINISH_REASON')
                                output = json.loads(output_text)
                                validate_output(purpose, output, payload)
                                if output['summary_vi'] != shown:
                                    raise g._InvalidError('SUMMARY_CHANGED')
                                result = {'output': output, 'provider': provider, 'model_version': model_version}
                                try:
                                    gate.finish(request_id, attempt_id, 'SUCCESS', context, response=result, usage=usage,
                                                latency_ms=g._elapsed(started))
                                finally:
                                    finished = True
                                yield 'result', {**result, 'usage': usage or {}, 'request_id': request_id,
                                                 'cached': False, 'attempts': attempts}
                                return
            except (GeneratorExit, asyncio.CancelledError):
                status, error = 'CANCELLED', 'CANCELLED'
                raise
            except (httpx.TimeoutException, TimeoutError):
                status, error = 'TIMEOUT', 'PROVIDER_TIMEOUT'
            except httpx.HTTPError:
                status, error = 'HTTP_ERROR', 'PROVIDER_HTTP_ERROR'
            except (ValueError, KeyError, IndexError, TypeError, AttributeError, UnicodeError, g._InvalidError):
                status, error = 'SCHEMA_INVALID', 'SCHEMA_INVALID'
            except PolicyDenied as exc:
                if finished:
                    raise PolicyDenied(exc.code, attempts=attempts) from None
                status, error = 'SCHEMA_INVALID', exc.code
            finally:
                if not finished:
                    try:
                        gate.finish(request_id, attempt_id, status, context, usage=usage,
                                    latency_ms=g._elapsed(started), error_code=error)
                    except PolicyDenied as exc:
                        if status != 'CANCELLED':
                            raise PolicyDenied(exc.code, attempts=attempts) from None
            if emitted or not (skip or retry_call):
                raise PolicyDenied(error, attempts=attempts)
            if skip:
                break
            await asyncio.sleep(min(0.5 * 2 ** retry, 2))
    raise last or PolicyDenied('PROVIDER_UNAVAILABLE', attempts=attempts)
