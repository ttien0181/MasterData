import asyncio
import json

import httpx
import pytest

from masterone.llm import streaming
from masterone.mdm.models import LLMRequest, LLMReservation
from tests.test_llm.conftest import run_context

PAYLOAD = {'pair_count': 1, 'field_signal_counts': {'name': {'exact': 1}}, 'score_is_probability': False}
OUTPUT = {'summary_vi': 'Tên tương tự.', 'attention_fields': ['name'], 'limitations': ['Cần đối chiếu.']}


class Chunks(httpx.AsyncByteStream):
    def __init__(self, chunks):
        self.chunks = chunks
        self.reads = 0
        self.closed = False

    async def __aiter__(self):
        for chunk in self.chunks:
            self.reads += 1
            yield chunk

    async def aclose(self):
        self.closed = True


def install(monkeypatch, chunks):
    wire = Chunks(chunks)
    original = httpx.AsyncClient
    def handle(request):
        assert request.url.path.endswith(':streamGenerateContent')
        assert request.url.params['alt'] == 'sse'
        return httpx.Response(200, stream=wire)
    monkeypatch.setattr(streaming, 'client', lambda settings: original(transport=httpx.MockTransport(handle)))
    return wire


def packet(text='', finish=None, usage=None):
    body = {'candidates': [{'content': {'parts': [{'text': text}]}}]}
    if finish:
        body['candidates'][0]['finishReason'] = finish
    if usage:
        body['usageMetadata'] = usage
    return ('data: ' + json.dumps(body, ensure_ascii=False) + '\n\n').encode()


@pytest.mark.asyncio
async def test_provider_chunks_are_delivered_before_response_is_complete_and_cached(llm_factory, llm_settings, monkeypatch):
    raw = json.dumps(OUTPUT, ensure_ascii=False)
    split = raw.index(' tương')
    wire = install(monkeypatch, [packet(raw[:split]), packet(raw[split:], 'STOP', {'promptTokenCount': 12, 'candidatesTokenCount': 7})])
    events = streaming.generate_explanation(PAYLOAD, llm_settings, context=run_context(llm_factory), factory=llm_factory)
    assert await anext(events) == ('delta', 'Tên')
    assert wire.reads == 1
    remaining = [event async for event in events]
    assert ''.join(['Tên'] + [data for kind, data in remaining if kind == 'delta']) == 'Tên tương tự.'
    assert remaining[-1][1]['output'] == OUTPUT
    assert wire.closed
    with llm_factory() as db:
        assert db.query(LLMRequest).one().output_tokens == 7
        assert db.query(LLMReservation).one().state == 'SUCCESS'
    cached = [e async for e in streaming.generate_explanation(PAYLOAD, llm_settings, context=run_context(llm_factory), factory=llm_factory)]
    assert len(cached) == 1 and cached[0][1]['cached']
    from masterone.llm.gateway import generate
    normal = generate('EXPLANATION', PAYLOAD, llm_settings, context=run_context(llm_factory), factory=llm_factory)
    assert normal['cached'] and normal['output'] == OUTPUT and normal['attempts'] == 0


@pytest.mark.asyncio
async def test_close_releases_transport_and_accounts_unknown_usage(llm_factory, llm_settings, monkeypatch):
    wire = install(monkeypatch, [packet('{"summary_vi":"Tên'), packet(' tương tự."}', 'STOP')])
    events = streaming.generate_explanation(PAYLOAD, llm_settings, context=run_context(llm_factory), factory=llm_factory)
    await anext(events)
    await events.aclose()
    assert wire.closed
    with llm_factory() as db:
        row = db.query(LLMRequest).one()
        assert row.status == 'CANCELLED'
        assert row.input_tokens is None and row.output_tokens is None
        assert db.query(LLMReservation).one().state == 'CANCELLED'


def test_summary_prefix_waits_for_complete_escapes_and_ignores_nested_keys():
    assert streaming.summary_prefix('{"limitations":["summary_vi"],"summary_vi":"A\\u1') == 'A'
    assert streaming.summary_prefix('{"summary_vi":"A\\u1ebf\\nB\\') == 'Aế\nB'
    assert streaming.summary_prefix('{"summary_vi":"\\ud83d') == ''
    assert streaming.summary_prefix('{"summary_vi":"\\ud83d\\ude00"}') == '😀'


@pytest.mark.asyncio
@pytest.mark.parametrize('tail', ['MAX_TOKENS', None])
async def test_incomplete_finish_never_caches_output(llm_factory, llm_settings, monkeypatch, tail):
    install(monkeypatch, [packet(json.dumps(OUTPUT), tail)])
    from masterone.llm.policy import PolicyDenied
    with pytest.raises(PolicyDenied, match='SCHEMA_INVALID'):
        _ = [e async for e in streaming.generate_explanation(PAYLOAD, llm_settings, context=run_context(llm_factory), factory=llm_factory)]
    with llm_factory() as db:
        assert db.query(LLMReservation).one().response is None


@pytest.mark.asyncio
@pytest.mark.parametrize('output', [
    {**OUTPUT, 'summary_vi': 'Please merge these records.'},
    {**OUTPUT, 'summary_vi': 'Contact private@example.test'},
    {**OUTPUT, 'summary_vi': 'X' * 1201},
    {**OUTPUT, 'attention_fields': ['source_values']},
])
async def test_invalid_content_settles_reservation_without_cache(llm_factory, llm_settings, monkeypatch, output):
    install(monkeypatch, [packet(json.dumps(output), 'STOP')])
    from masterone.llm.policy import PolicyDenied
    with pytest.raises(PolicyDenied, match='SCHEMA_INVALID'):
        _ = [e async for e in streaming.generate_explanation(PAYLOAD, llm_settings, context=run_context(llm_factory), factory=llm_factory)]
    with llm_factory() as db:
        assert db.query(LLMRequest).one().status == 'SCHEMA_INVALID'
        row = db.query(LLMReservation).one()
        assert row.state == 'SCHEMA_INVALID' and row.response is None


@pytest.mark.asyncio
async def test_timeout_after_delta_does_not_retry_or_fallback(llm_factory, llm_settings, monkeypatch):
    original = httpx.AsyncClient
    calls = []
    class TimeoutWire(Chunks):
        async def __aiter__(self):
            yield packet('{"summary_vi":"Safe')
            raise httpx.ReadTimeout('sensitive provider details')
    wire = TimeoutWire([])
    def handle(request):
        calls.append(request)
        return httpx.Response(200, stream=wire)
    monkeypatch.setattr(streaming, 'client', lambda settings: original(transport=httpx.MockTransport(handle)))
    source = streaming.generate_explanation(PAYLOAD, llm_settings.model_copy(update={'gemini_max_retries': 2}),
                                            context=run_context(llm_factory), factory=llm_factory)
    assert await anext(source) == ('delta', 'Safe')
    from masterone.llm.policy import PolicyDenied
    with pytest.raises(PolicyDenied, match='PROVIDER_TIMEOUT'):
        await anext(source)
    assert len(calls) == 1 and wire.closed
    with llm_factory() as db:
        assert db.query(LLMRequest).one().status == 'TIMEOUT'


@pytest.mark.asyncio
async def test_quota_falls_back_to_real_openrouter_stream(llm_factory, llm_settings, monkeypatch):
    original = httpx.AsyncClient
    calls = []
    raw = json.dumps(OUTPUT)
    chunks = [({'choices': [{'index': 0, 'delta': {'content': raw[:20]}, 'finish_reason': None}]}),
              ({'choices': [{'index': 0, 'delta': {'content': raw[20:]}, 'finish_reason': 'stop'}],
                'usage': {'prompt_tokens': 10, 'completion_tokens': 8}})]
    wire = Chunks([('data: ' + json.dumps(c) + '\n\n').encode() for c in chunks])
    def handle(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(429)
        body = json.loads(request.content)
        assert body['stream'] and body['stream_options']['include_usage']
        assert 'source_values' not in request.content.decode()
        return httpx.Response(200, stream=wire)
    monkeypatch.setattr(streaming, 'client', lambda settings: original(transport=httpx.MockTransport(handle)))
    events = [e async for e in streaming.generate_explanation(PAYLOAD, llm_settings,
              context=run_context(llm_factory), factory=llm_factory)]
    assert events[-1][1]['provider'] == 'openrouter' and events[-1][1]['attempts'] == 2
    with llm_factory() as db:
        assert sorted(row.status for row in db.query(LLMRequest)) == ['PROVIDER_QUOTA', 'SUCCESS']


@pytest.mark.asyncio
async def test_malformed_packet_after_delta_is_terminal_schema_failure(llm_factory, llm_settings, monkeypatch):
    wire = install(monkeypatch, [packet('{"summary_vi":"Safe'), b'data: {"candidates":[null]}\n\n'])
    source = streaming.generate_explanation(PAYLOAD, llm_settings, context=run_context(llm_factory), factory=llm_factory)
    assert await anext(source) == ('delta', 'Safe')
    from masterone.llm.policy import PolicyDenied
    with pytest.raises(PolicyDenied, match='SCHEMA_INVALID'):
        await anext(source)
    assert wire.closed
    with llm_factory() as db:
        assert db.query(LLMRequest).one().status == 'SCHEMA_INVALID'
        assert db.query(LLMReservation).one().response is None


@pytest.mark.asyncio
async def test_duplicate_summary_cannot_replace_emitted_text(llm_factory, llm_settings, monkeypatch):
    raw = '{"summary_vi":"First", "summary_vi":"Different", "attention_fields":[], "limitations":[]}'
    install(monkeypatch, [packet(raw, 'STOP')])
    source = streaming.generate_explanation(PAYLOAD, llm_settings, context=run_context(llm_factory), factory=llm_factory)
    assert await anext(source) == ('delta', 'First')
    from masterone.llm.policy import PolicyDenied
    with pytest.raises(PolicyDenied, match='SCHEMA_INVALID'):
        await anext(source)
    with llm_factory() as db:
        assert db.query(LLMReservation).one().response is None


@pytest.mark.asyncio
async def test_task_cancellation_settles_once_and_closes_response(llm_factory, llm_settings, monkeypatch):
    original = httpx.AsyncClient
    waiting = asyncio.Event()
    class WaitingWire(Chunks):
        async def __aiter__(self):
            yield packet('{"summary_vi":"Safe')
            waiting.set()
            await asyncio.Event().wait()
    wire = WaitingWire([])
    monkeypatch.setattr(streaming, 'client', lambda settings: original(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, stream=wire))))
    source = streaming.generate_explanation(PAYLOAD, llm_settings, context=run_context(llm_factory), factory=llm_factory)
    await anext(source)
    task = asyncio.create_task(anext(source))
    await waiting.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert wire.closed
    with llm_factory() as db:
        assert db.query(LLMRequest).one().status == 'CANCELLED'
        row = db.query(LLMReservation).one()
        assert row.calls == 1 and row.state == 'CANCELLED' and row.response is None
