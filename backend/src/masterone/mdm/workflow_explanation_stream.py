"""Authenticated workflow SSE orchestration with aggregate-only provider input."""
import json

from masterone.llm.explanation import template_explanation
from masterone.llm.policy import PolicyDenied, PolicyEnforcer, assert_fresh
from masterone.llm.streaming import generate_explanation
from masterone.mdm.workflow_explanation import pair_current, prepare_pair


def event(kind, data):
    return f'event: {kind}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n'


def prepare_stream(db, run_id, pair_id, request, actor):
    # Run ownership, capability (route), and revision errors must precede HTTP 200.
    payload, settings, context, expected, factory = prepare_pair(db, run_id, pair_id, request, actor)

    async def stream():
        result = None
        request_id = None
        if not settings.llm_enabled or not settings.llm_explanation_enabled:
            result = template_explanation('LLM_DISABLED')
        else:
            source = generate_explanation(payload, settings, context=context, factory=factory)
            try:
                async for kind, data in source:
                    if kind == 'delta':
                        yield event('delta', {'text': data})
                    else:
                        request_id = data['request_id']
                        result = {'status': 'READY', 'explanation': data['output'],
                                  'model_version': data['model_version'], 'provider': data['provider'],
                                  'request_id': request_id,
                                  'usage': {'dispatch_attempts': data['attempts'],
                                            **{k: v for k, v in data['usage'].items() if v is not None}}}
            except PolicyDenied as exc:
                result = ({'status': 'STALE', 'explanation': None, 'model_version': None, 'usage': {}}
                          if exc.code in {'STALE', 'RUN_INACTIVE'}
                          else template_explanation(exc.code, attempts=exc.attempts))
            finally:
                # Closing this async generator propagates disconnect to the provider transport/accounting.
                await source.aclose()
        with factory() as current:
            fresh = pair_current(current, run_id, pair_id, expected)
            try:
                assert_fresh(current, run_id, context['fence'])
                if result['status'] == 'READY':
                    if not settings.llm_enabled or not settings.llm_explanation_enabled:
                        raise PolicyDenied('LLM_DISABLED')
                    PolicyEnforcer.check_control(current)
            except PolicyDenied:
                fresh = False
        if not fresh:
            if request_id:
                PolicyEnforcer(settings, factory).discard(request_id)
            result = {'status': 'STALE', 'explanation': None, 'model_version': None,
                      'usage': {}, 'error_code': 'STALE'}
        yield event('result', result)

    return stream()
