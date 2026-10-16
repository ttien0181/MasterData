"""Single gate for every outbound LLM call.

Nothing here sends a request. A caller reserves one logical request, attempts one dispatch per provider
and finishes it. Approval, retention, prices, budgets, freshness and Gemini cooldown are checked here,
so a transport cannot bypass them. Raw prompts and provider bodies are never stored.

Freshness uses a cheap fence per call. The full run fingerprint is computed once per stage start and once
at publication, so the cost does not grow with the number of batches.
"""

import json
import re
from contextlib import contextmanager
from datetime import UTC, timedelta
from functools import lru_cache
from hashlib import sha256
from uuid import uuid4

from sqlalchemy import func, select, text

from masterone.db.base import now_utc
from masterone.db.session import create_database
from masterone.mdm.models import (
    LLMPolicyControl,
    LLMRequest,
    LLMReservation,
    ResolutionPublication,
    ResolutionRun,
    ResolutionTask,
)

PURPOSES = ("ADDRESS_COMPARE", "EXPLANATION", "PAIR_ASSESSMENT")
PROVIDERS = ("gemini", "openrouter")
# Provider-specific denials move on to the next provider. Every other denial stops the request.
PROVIDER_SKIP_CODES = frozenset({
    "PROVIDER_DENIED", "MODEL_DENIED", "RETENTION_UNVERIFIED", "PRICE_UNVERIFIED",
    "PROVIDER_UNAVAILABLE", "PROVIDER_COOLDOWN",
})
# Attempt outcomes that leave the logical request dispatchable by the next provider.
# The provider did not process them, so resending elsewhere cannot double-charge.
RETRYABLE_OUTCOMES = frozenset({"HTTP_RETRYABLE", "PROVIDER_QUOTA", "PROVIDER_UNAVAILABLE"})
# Outcomes that count toward the per-provider circuit. Quota is handled by cooldown, not the circuit.
CIRCUIT_OUTCOMES = ("TIMEOUT", "HTTP_ERROR", "SCHEMA_INVALID")
ADVICE_WORDS = ("merge", "gộp", "từ chối", "reject", "approve", "phê duyệt")

_UNSAFE = re.compile(
    r"@|https?://|www\.|\b\d[\d\s().+-]{7,}\d\b|"
    r"ignore|instruction|system|prompt|exfiltrat|upload|secret|api.?key|"
    r"cccd|cmnd|invoice|hóa đơn|hoá đơn|email|điện thoại|phone|họ tên|full.?name|"
    r"```|<\s*/?\s*(script|system)|\[\s*INST",
    re.IGNORECASE,
)
# Output check: the model may name fields (phone, email) but must not emit contact values, IDs or injected text.
_OUTPUT_UNSAFE = re.compile(
    r"@|https?://|www\.|\d[\d\s().+-]{7,}\d|"
    r"ignore (?:all|previous|the above)|system prompt|exfiltrat|```|<\s*/?\s*(?:script|system)|\[\s*INST",
    re.IGNORECASE,
)
_NEGATION = re.compile(r"\b(?:không|khong|chưa|chua|not|never|no|without)\b", re.IGNORECASE)
_CLAUSE_SPLIT = re.compile(r"[.;:!?\n,]+")
_ADDRESS_CONTEXT = re.compile(
    r"\d|phường|phuong|ward|đường|duong|street|tỉnh|tinh|province|quận|quan|huyện|huyen|xã|xa",
    re.IGNORECASE,
)
_CODE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,47}")
_MODEL = re.compile(r"[A-Za-z0-9_./-]{1,100}")
_FIELDS = frozenset({"name", "phone", "email", "address"})
_STATES = frozenset({"EX", "NX", "NA", "HS", "MS", "WS", "CL", "DF", "CF", "MI", "IV", "SH", "NC"})
_BANDS = frozenset({"exact", "similar", "different", "ambiguous", "missing"})
_MAX_COUNT = 10_000_000


class PolicyDenied(RuntimeError):  # noqa: N818 - stable domain exception, fixed codes only
    def __init__(self, code, *, attempts=0):
        super().__init__(code)
        self.code = code
        self.attempts = attempts


def digest(value):
    raw = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    return sha256(raw.encode()).hexdigest()


def _approved(csv_value):
    return {item.strip() for item in csv_value.split(",") if item.strip()}


def provider_model(settings, provider):
    return settings.gemini_model if provider == "gemini" else settings.openrouter_model


def provider_price(settings, provider):
    return settings.gemini_token_price_microusd if provider == "gemini" else settings.openrouter_token_price_microusd


def provider_retention(settings, provider):
    if provider == "gemini":
        return settings.gemini_retention_verified, settings.gemini_retention_contract
    return settings.openrouter_retention_verified, settings.openrouter_retention_contract


def provider_key(settings, provider):
    return settings.gemini_api_key if provider == "gemini" else settings.openrouter_api_key


def enforce(settings, provider, purpose):
    """Static approval with fixed codes. Touches neither the database nor the network."""
    if not settings.llm_enabled:
        raise PolicyDenied("LLM_DISABLED")
    if purpose not in PURPOSES:
        raise PolicyDenied("PURPOSE_DENIED")
    if purpose == "ADDRESS_COMPARE" and not settings.semantic_address_enabled:
        raise PolicyDenied("PURPOSE_DISABLED")
    if purpose == "EXPLANATION" and not settings.llm_explanation_enabled:
        raise PolicyDenied("PURPOSE_DISABLED")
    if purpose == 'PAIR_ASSESSMENT' and not settings.llm_pair_assessment_enabled:
        raise PolicyDenied('PURPOSE_DISABLED')
    if provider not in PROVIDERS or provider not in _approved(settings.llm_approved_providers):
        raise PolicyDenied("PROVIDER_DENIED")
    model = provider_model(settings, provider)
    if model not in _approved(settings.llm_approved_models) or not _MODEL.fullmatch(model):
        raise PolicyDenied("MODEL_DENIED")
    verified, contract = provider_retention(settings, provider)
    if not verified or not contract.strip():
        raise PolicyDenied("RETENTION_UNVERIFIED")
    if provider_price(settings, provider) <= 0:
        raise PolicyDenied("PRICE_UNVERIFIED")
    if not provider_key(settings, provider):
        raise PolicyDenied("PROVIDER_UNAVAILABLE")


def require_some_provider(settings, purpose):
    """Logical identity needs at least one approved provider; global denials propagate."""
    last = None
    for provider in PROVIDERS:
        try:
            enforce(settings, provider, purpose)
            return
        except PolicyDenied as exc:
            if exc.code not in PROVIDER_SKIP_CODES:
                raise
            last = exc
    raise last or PolicyDenied("PROVIDER_UNAVAILABLE")


def validate_startup(settings):
    """Fail fast when LLM is enabled without any provider key. Missing approvals only deny calls."""
    if settings.llm_enabled and not (settings.gemini_api_key or settings.openrouter_api_key):
        raise RuntimeError("LLM_ENABLED=true requires GEMINI_API_KEY or OPENROUTER_API_KEY")


def allowed_payload(purpose, payload):
    """Reject unknown keys. Source values never enter a prompt; only counts, bands and codes do."""
    if purpose == 'PAIR_ASSESSMENT':
        if not isinstance(payload, dict) or set(payload) != {'pair_token', 'left', 'right', 'signals', 'unverified_identifier_relation'}:
            raise PolicyDenied('PAYLOAD_DENIED')
        if not isinstance(payload['pair_token'], str) or not re.fullmatch(r'pair-[a-zA-Z0-9-]{1,40}', payload['pair_token']):
            raise PolicyDenied('PAYLOAD_DENIED')
        for side in ('left', 'right'):
            if not isinstance(payload[side], dict) or set(payload[side]) != {'name', 'address'}:
                raise PolicyDenied('PAYLOAD_DENIED')
            for value in payload[side].values():
                if not isinstance(value, str) or len(value) > 300 or _OUTPUT_UNSAFE.search(value) or re.search(r'\b\d{4}-\d{2}-\d{2}\b', value):
                    raise PolicyDenied('PAYLOAD_DENIED')
        if not isinstance(payload['signals'], dict) or set(payload['signals']) - (_FIELDS | {'dob', 'government_id'}):
            raise PolicyDenied('PAYLOAD_DENIED')
        if any(value not in _STATES for value in payload['signals'].values()):
            raise PolicyDenied('PAYLOAD_DENIED')
        if payload['unverified_identifier_relation'] not in {'SAME_VALUE', 'DIFFERENT_VALUE', 'NOT_COMPARABLE', 'VERIFIED_METADATA'}:
            raise PolicyDenied('PAYLOAD_DENIED')
        return payload
    if purpose == "ADDRESS_COMPARE":
        if not isinstance(payload, list) or not 1 <= len(payload) <= 10:
            raise PolicyDenied("PAYLOAD_DENIED")
        for row in payload:
            if not isinstance(row, dict) or set(row) != {"pair_id", "left_address", "right_address"}:
                raise PolicyDenied("PAYLOAD_DENIED")
            if not isinstance(row["pair_id"], str) or not re.fullmatch(r"[\w-]{1,64}", row["pair_id"]):
                raise PolicyDenied("PAYLOAD_DENIED")
            for field in ("left_address", "right_address"):
                value = row[field]
                if not isinstance(value, str) or not 1 <= len(value) <= 300 or _UNSAFE.search(value):
                    raise PolicyDenied("PAYLOAD_DENIED")
                if not _ADDRESS_CONTEXT.search(value):
                    raise PolicyDenied("ADDRESS_CONTEXT_REQUIRED")
        if len({row["pair_id"] for row in payload}) != len(payload):
            raise PolicyDenied("PAYLOAD_DENIED")
        return payload
    if purpose != "EXPLANATION" or not isinstance(payload, dict):
        raise PolicyDenied("PAYLOAD_DENIED")
    allowed = {"pair_count", "record_count", "unique_record_count", "field_state_counts",
               "field_signal_counts", "reason_counts", "group", "subgroup", "score_is_probability"}
    if set(payload) - allowed:
        raise PolicyDenied("PAYLOAD_DENIED")
    result = {}
    for key in ("pair_count", "record_count", "unique_record_count"):
        if key in payload:
            if type(payload[key]) is not int or not 0 <= payload[key] <= _MAX_COUNT:
                raise PolicyDenied("PAYLOAD_DENIED")
            result[key] = payload[key]
    if "field_state_counts" in payload:
        result["field_state_counts"] = _counts(payload["field_state_counts"], _FIELDS, _STATES)
    if "field_signal_counts" in payload:
        result["field_signal_counts"] = _counts(payload["field_signal_counts"], _FIELDS, _BANDS)
    if "reason_counts" in payload:
        # Internal reason codes only. Anything that is not a code-shaped key is dropped, never forwarded.
        result["reason_counts"] = {key: value for key, value in payload["reason_counts"].items()
                                   if isinstance(key, str) and _CODE.fullmatch(key)
                                   and type(value) is int and 0 <= value <= _MAX_COUNT}
    for key in ("group", "subgroup"):
        if key in payload:
            if not isinstance(payload[key], str) or not _CODE.fullmatch(payload[key]):
                raise PolicyDenied("PAYLOAD_DENIED")
            result[key] = payload[key]
    result["score_is_probability"] = False
    return result


def _counts(data, fields, states):
    if not isinstance(data, dict) or set(data) - fields:
        raise PolicyDenied("PAYLOAD_DENIED")
    for counts in data.values():
        if not isinstance(counts, dict) or set(counts) - states or any(
                type(v) is not int or not 0 <= v <= _MAX_COUNT for v in counts.values()):
            raise PolicyDenied("PAYLOAD_DENIED")
    return data


def validate_output(purpose, output, payload):
    """Strict schema check. Provider output is untrusted until this passes."""
    if not isinstance(output, dict):
        raise PolicyDenied("SCHEMA_INVALID")
    if purpose == 'PAIR_ASSESSMENT':
        from masterone.llm.pair_advice_contract import LIMITATIONS, REASONS
        if (set(output) != {'pair_token', 'recommendation', 'evidence', 'reason_codes', 'limitation_codes'}
                or output['pair_token'] != payload['pair_token']
                or output['recommendation'] not in {'LIKELY_SAME_PERSON', 'LIKELY_DIFFERENT_PERSON', 'INSUFFICIENT_BASIS'}):
            raise PolicyDenied('SCHEMA_INVALID')
        evidence = output['evidence']
        if not isinstance(evidence, list) or len(evidence) > 6:
            raise PolicyDenied('SCHEMA_INVALID')
        found = set()
        for item in evidence:
            if (not isinstance(item, dict) or set(item) != {'field', 'state'} or not isinstance(item['field'], str)
                    or item['field'] not in payload['signals'] or item['field'] in found
                    or item['state'] != payload['signals'][item['field']] or item['state'] in {'MI', 'IV'}):
                raise PolicyDenied('SCHEMA_INVALID')
            found.add(item['field'])
        for key, allowed in [('reason_codes', REASONS), ('limitation_codes', LIMITATIONS)]:
            codes = output[key]
            if (not isinstance(codes, list) or not 1 <= len(codes) <= len(allowed)
                    or any(not isinstance(v, str) or v not in allowed for v in codes) or len(set(codes)) != len(codes)):
                raise PolicyDenied('SCHEMA_INVALID')
        states = [item['state'] for item in evidence]
        relation = payload['unverified_identifier_relation']
        predicates = {
            'CORROBORATING_SIGNALS': sum(s in {'EX','NX','NA','HS','MS','CL'} for s in states) >= 2,
            'CONFLICTING_SIGNALS': any(s in {'DF','CF'} for s in states) or ('government_id' in found and relation == 'DIFFERENT_VALUE'),
            'SPARSE_SIGNALS': sum(s not in {'MI','IV','NC','SH'} for s in payload['signals'].values()) < 3,
            'NORMALIZATION_VARIANT': any(s in {'NX','NA'} for s in states),
            'UNVERIFIED_IDENTIFIER': 'government_id' in found and relation in {'SAME_VALUE','DIFFERENT_VALUE'},
            'NO_VERIFIED_ANCHOR': relation != 'VERIFIED_METADATA',
        }
        if any(not predicates[code] for code in output['reason_codes']):
            raise PolicyDenied('SCHEMA_INVALID')
        if output['recommendation'] == 'LIKELY_SAME_PERSON' and not any(s in {'EX','NX','NA','HS','MS','CL'} for s in states):
            raise PolicyDenied('SCHEMA_INVALID')
        if output['recommendation'] == 'LIKELY_DIFFERENT_PERSON' and not predicates['CONFLICTING_SIGNALS']:
            raise PolicyDenied('SCHEMA_INVALID')
        if not {'SOURCE_NOT_VERIFIED','HUMAN_REVIEW_REQUIRED'} <= set(output['limitation_codes']):
            raise PolicyDenied('SCHEMA_INVALID')
        return output
    if purpose == "ADDRESS_COMPARE":
        if set(output) != {"items"} or not isinstance(output["items"], list):
            raise PolicyDenied("SCHEMA_INVALID")
        expected = {row["pair_id"] for row in payload}
        found = set()
        for item in output["items"]:
            if not isinstance(item, dict) or set(item) != {"pair_id", "result"}:
                raise PolicyDenied("SCHEMA_INVALID")
            if item["pair_id"] not in expected or item["pair_id"] in found or item["result"] not in {
                    "EQUIVALENT", "DIFFERENT", "UNCERTAIN"}:
                raise PolicyDenied("SCHEMA_INVALID")
            found.add(item["pair_id"])
        if found != expected:
            raise PolicyDenied("SCHEMA_INVALID")
        return output
    if set(output) != {"summary_vi", "attention_fields", "limitations"}:
        raise PolicyDenied("SCHEMA_INVALID")
    summary = output["summary_vi"]
    if not isinstance(summary, str) or not 1 <= len(summary) <= 1200:
        raise PolicyDenied("SCHEMA_INVALID")
    for field in ("attention_fields", "limitations"):
        if not isinstance(output[field], list) or len(output[field]) > 8 or any(
                not isinstance(v, str) or len(v) > 300 for v in output[field]):
            raise PolicyDenied("SCHEMA_INVALID")
    if set(output["attention_fields"]) - _FIELDS:
        raise PolicyDenied("SCHEMA_INVALID")
    text_all = " ".join([summary, *output["limitations"]])
    if _OUTPUT_UNSAFE.search(text_all):
        raise PolicyDenied("SCHEMA_INVALID")
    # The prompt forbids merge or reject advice. Enforce it here, because a prompt is not a guarantee.
    # A clause that states a negation ("không đề xuất gộp") is a disclaimer, not advice.
    for clause in _CLAUSE_SPLIT.split(text_all.lower()):
        if any(word in clause for word in ADVICE_WORDS) and not _NEGATION.search(clause):
            raise PolicyDenied("SCHEMA_INVALID")
    return output


@lru_cache(maxsize=4)
def factory_for(url):
    return create_database(url)[1]


def fence(db, run_id):
    """Cheap freshness token: a few primary-key reads, independent of run size."""
    run = db.get(ResolutionRun, run_id)
    if run is None:
        raise PolicyDenied("RUN_INACTIVE")
    task = db.query(ResolutionTask.attempt_id, ResolutionTask.cancel_requested).filter_by(run_id=run_id).one_or_none()
    publication = db.get(ResolutionPublication, run_id)
    control = db.get(LLMPolicyControl, "global")
    return {
        "owner": run.owner_user_id,
        "run_status": run.status,
        "archived": run.archived_at is not None,
        "task_attempt": task[0] if task else None,
        "cancel": bool(task[1]) if task else False,
        "publication_attempt": publication.pipeline_attempt_id if publication else None,
        "generation": publication.active_generation_id if publication else None,
        "control_revision": control.revision if control else None,
    }


def assert_fresh(db, run_id, expected):
    current = fence(db, run_id)
    if current["archived"] or current["cancel"] or current["run_status"] in {"CANCELLED", "FAILED"}:
        raise PolicyDenied("RUN_INACTIVE")
    if current != expected:
        raise PolicyDenied("STALE")


def snapshot(db, run_id):
    """Full run fingerprint. O(run size): call once per stage start and once at publication only."""
    from masterone.mdm.generation_staging import fingerprints
    from masterone.mdm.pipeline import address_mapping
    inputs, review, _ = fingerprints(db, run_id)
    current_fence = fence(db, run_id)
    return {
        "run_id": run_id,
        "user_id": current_fence["owner"],
        "fence": current_fence,
        "input_hash": inputs,
        "review_hash": review,
        "reference_version": address_mapping.MAPPING_VERSION,
        "reference_hash": digest({key: [value.status, sorted(value.unit_ids)]
                                  for key, value in address_mapping._WARD_MAP.items()}),
    }


def _aware(value):
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


@contextmanager
def accounting_transaction(factory):
    """One short database-wide critical section for every budget read-modify-write."""
    with factory() as db:
        if db.bind.dialect.name == "postgresql":
            db.execute(text("SET LOCAL lock_timeout = '5s'"))
            db.execute(text("SELECT pg_advisory_xact_lock(1190026)"))
        elif db.bind.dialect.name == "sqlite":
            db.connection().exec_driver_sql("BEGIN IMMEDIATE")
        else:
            raise PolicyDenied("ACCOUNTING_BACKEND_DENIED")
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise


class PolicyEnforcer:
    def __init__(self, settings, factory=None):
        self.settings = settings
        self.factory = factory or factory_for(settings.active_mdm_database_url)

    @staticmethod
    def check_control(db):
        control = db.get(LLMPolicyControl, "global", populate_existing=True)
        if control is None or not control.enabled:
            raise PolicyDenied("LLM_GLOBAL_DISABLED")
        return control

    def snapshot(self, run_id):
        if not run_id:
            raise PolicyDenied("CONTEXT_REQUIRED")
        with self.factory() as db:
            return snapshot(db, run_id)

    def publication_current(self, run_id, context):
        """Full recheck once, before publication: inputs, human review state and reference must be unchanged."""
        with self.factory() as db:
            current = snapshot(db, run_id)
        return all(current[key] == context[key] for key in ("input_hash", "review_hash",
                                                            "reference_version", "reference_hash"))

    def reserve(self, purpose, payload, context, prompt_version, schema_version):
        s = self.settings
        require_some_provider(s, purpose)
        payload = allowed_payload(purpose, payload)
        stable = {key: context[key] for key in ("run_id", "user_id", "input_hash", "review_hash",
                                                "reference_version", "reference_hash")}
        # Logical identity excludes attempt and worker ids, so a crashed dispatch cannot be resent.
        request_id = digest([stable, payload, purpose, s.gemini_model, s.openrouter_model, prompt_version,
                             schema_version, s.llm_policy_revision, s.gemini_retention_contract,
                             s.openrouter_retention_contract])
        with accounting_transaction(self.factory) as db:
            self.check_control(db)
            assert_fresh(db, context["run_id"], context["fence"])
            existing = db.get(LLMReservation, request_id)
            if existing is not None:
                if existing.state == "SUCCESS":
                    created = _aware(existing.created_at)
                    if created + timedelta(seconds=s.llm_cache_ttl_seconds) < now_utc():
                        raise PolicyDenied("CACHE_EXPIRED")
                    return request_id, existing.response
                if (existing.state == "RESERVED" and existing.calls == 0
                        and existing.reserved_tokens == 0 and existing.reserved_cost_microusd == 0):
                    # A pre-dispatch policy denial has not sent anything to a provider.
                    # attempt() rechecks policy and atomically fences concurrent dispatches.
                    return request_id, None
                raise PolicyDenied("REQUEST_" + existing.state)
            db.add(LLMReservation(id=request_id, run_id=context["run_id"], user_id=context["user_id"],
                                  purpose=purpose, context_hash=digest(context), context=context,
                                  state="RESERVED", calls=0, reserved_tokens=0, reserved_cost_microusd=0))
        return request_id, None

    def attempt(self, request_id, purpose, provider, payload, context, prompt_version, schema_version):
        s = self.settings
        enforce(s, provider, purpose)
        # UTF-8 bytes of the payload plus fixed prompt overhead bound the input. Reject, never truncate.
        tokens = len(json.dumps(payload, ensure_ascii=False).encode()) + 2048
        if tokens > s.llm_max_input_tokens_per_request:
            raise PolicyDenied("INPUT_CAP")
        tokens += s.gemini_max_output_tokens
        cost = tokens * provider_price(s, provider)
        with accounting_transaction(self.factory) as db:
            control = self.check_control(db)
            row = db.get(LLMReservation, request_id)
            if row is None or row.state not in {"RESERVED", "RETRYABLE"}:
                raise PolicyDenied("REQUEST_" + (row.state if row is not None else "MISSING"))
            assert_fresh(db, context["run_id"], context["fence"])
            now = now_utc()
            if provider == "gemini" and _aware(control.gemini_blocked_until) is not None and _aware(
                    control.gemini_blocked_until) > now:
                raise PolicyDenied("PROVIDER_COOLDOWN")
            scopes = [
                ([], s.llm_global_call_cap),
                ([LLMReservation.user_id == row.user_id], s.llm_user_call_cap),
                ([LLMReservation.run_id == row.run_id, LLMReservation.purpose == purpose], s.llm_max_explanation_calls_per_run
                 if purpose == "EXPLANATION" else s.llm_max_pair_assessment_calls_per_run
                 if purpose == 'PAIR_ASSESSMENT' else s.llm_max_semantic_calls_per_run),
            ]
            for filters, cap in scopes:
                if db.scalar(select(func.coalesce(func.sum(LLMReservation.calls), 0)).where(*filters)) >= cap:
                    raise PolicyDenied("CALL_CAP")
            total_tokens, total_cost = db.execute(select(
                func.coalesce(func.sum(LLMReservation.reserved_tokens), 0),
                func.coalesce(func.sum(LLMReservation.reserved_cost_microusd), 0))).one()
            if total_tokens + tokens > s.llm_token_cap or total_cost + cost > s.llm_cost_cap_microusd:
                raise PolicyDenied("BUDGET_CAP")
            from masterone.mdm.job_queue import database_now
            database_time = database_now(db)
            recent = database_time - timedelta(seconds=60)
            if db.scalar(select(func.count()).select_from(LLMRequest).where(
                    LLMRequest.created_at >= recent)) >= s.llm_calls_per_minute:
                raise PolicyDenied("RATE_CAP")
            if db.scalar(select(func.count()).select_from(LLMReservation).where(
                    LLMReservation.state == "DISPATCHING")) >= s.llm_max_concurrency:
                raise PolicyDenied("CONCURRENCY_CAP")
            failures = db.scalar(select(func.count()).select_from(LLMRequest).where(
                LLMRequest.created_at >= recent, LLMRequest.provider == provider,
                LLMRequest.status.in_(CIRCUIT_OUTCOMES)))
            if failures >= s.llm_consecutive_failure_limit:
                raise PolicyDenied("CIRCUIT_OPEN")
            row.calls += 1
            row.reserved_tokens += tokens
            row.reserved_cost_microusd += cost
            row.state = "DISPATCHING"
            attempt_id = str(uuid4())
            db.add(LLMRequest(id=attempt_id, request_type=purpose, provider=provider,
                              model=provider_model(s, provider), prompt_version=prompt_version,
                              schema_version=schema_version, request_hash=request_id,
                              status="DISPATCH_INTENT", created_at=database_time))
        return attempt_id

    def finish(self, request_id, attempt_id, status, context, *, response=None, usage=None,
               latency_ms=None, error_code=None):
        stale = False
        with accounting_transaction(self.factory) as db:
            row = db.get(LLMReservation, request_id)
            attempt = db.get(LLMRequest, attempt_id)
            try:
                assert_fresh(db, context["run_id"], context["fence"])
            except PolicyDenied:
                stale = True
            control = db.get(LLMPolicyControl, "global", populate_existing=True)
            disabled = not self.settings.llm_enabled or control is None or not control.enabled
            if stale:
                status, response = "STALE", None
            if disabled:
                status, response = "DISCARDED", None
            if status in RETRYABLE_OUTCOMES:
                row.state = "RETRYABLE"
            elif status == "SUCCESS":
                row.state, row.response = "SUCCESS", response
            else:
                row.state, row.response = status, None
            attempt.status = status
            attempt.latency_ms = latency_ms
            attempt.error_code = error_code if status not in {"STALE", "DISCARDED"} else status
            if response is not None:
                attempt.response_hash = digest(response)
            if usage:
                for target, source in (("input_tokens", "input"), ("output_tokens", "output")):
                    value = usage.get(source)
                    if type(value) is int and value >= 0:
                        setattr(attempt, target, value)
        if stale:
            raise PolicyDenied("STALE")
        if disabled:
            raise PolicyDenied("DISCARDED")

    def block_gemini(self, seconds):
        """Quota exhausted: every process skips Gemini until the cooldown expires."""
        if seconds <= 0:
            return
        with accounting_transaction(self.factory) as db:
            control = db.get(LLMPolicyControl, "global", populate_existing=True)
            if control is not None:
                control.gemini_blocked_until = now_utc() + timedelta(seconds=seconds)

    def discard(self, request_id):
        """A result that arrived after the run changed: keep the spend, never keep the output."""
        with accounting_transaction(self.factory) as db:
            row = db.get(LLMReservation, request_id)
            if row is not None:
                row.state, row.response = "STALE", None
                for attempt in db.scalars(select(LLMRequest).where(
                        LLMRequest.request_hash == request_id, LLMRequest.status == "SUCCESS")):
                    attempt.status = "STALE"
