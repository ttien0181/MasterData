"""Optional LLM check for ambiguous addresses in otherwise supported pairs.

Annotation only. A result never changes a decision, a baseline or an automatic merge, and the annotation
carries eligible_for_auto_merge=False. Only address strings leave the server.

Cost is bounded by the number of selected pairs, not by the number of candidates: selection streams over the
decision store and fetches records only for the pairs that qualify. Identical address pairs are sent once.
"""

import json
import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from hashlib import sha256

from masterone.config import get_settings
from masterone.llm.gateway import PROMPT_VERSION, SCHEMA_VERSION, generate
from masterone.llm.policy import PolicyDenied

log = logging.getLogger(__name__)

ADDRESS_LOW, ADDRESS_HIGH = 0.60, 0.85
BATCH_SIZE = 10
LOOKUP_CHUNK = 512
RESULTS = frozenset({"EQUIVALENT", "DIFFERENT", "UNCERTAIN"})
WEAK_ADDRESS_STATES = frozenset({"WS", "MS", "DF"})
ANNOTATION_VERSION = "address-annotation-v3"
# Denials that make every further call pointless in this run. Stop instead of retrying.
STOP_CODES = frozenset({"LLM_DISABLED", "LLM_GLOBAL_DISABLED", "PURPOSE_DISABLED", "CALL_CAP", "BUDGET_CAP",
                        "RATE_CAP", "CONCURRENCY_CAP", "CIRCUIT_OPEN", "STALE", "RUN_INACTIVE",
                        "CONTEXT_REQUIRED", "ACCOUNTING_BACKEND_DENIED"})


def _hash(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return sha256(raw.encode()).hexdigest()


def baseline_hash(pair):
    """Everything DECIDE produced. Annotations are a sidecar and never part of the baseline."""
    return _hash({key: value for key, value in pair.items() if key != "semantic_annotations"})


def _evidence(pair):
    return {entry["field"]: entry["state"] for entry in pair["field_evidence"]}


def _may_qualify(pair):
    """Cheap checks on the decision payload only. No record lookup happens for pairs rejected here."""
    if pair["decision"] != "REVIEW":
        return False
    evidence = _evidence(pair)
    if evidence.get("name") == "CF" or evidence.get("address") not in WEAK_ADDRESS_STATES:
        return False
    return bool({evidence.get("phone"), evidence.get("email")} & {"EX", "NX"})


def _address_pair(left_record, right_record):
    left = (left_record or {}).get("normalized_fields", {}).get("address", "")
    right = (right_record or {}).get("normalized_fields", {}).get("address", "")
    if not left or not right:
        return None
    similarity = SequenceMatcher(None, left, right, autojunk=False).ratio()
    return (left, right) if ADDRESS_LOW <= similarity < ADDRESS_HIGH else None


def _review_pairs(pairs):
    """Decision-store pairs with decision REVIEW, indexed; a plain list is filtered in memory."""
    if hasattr(pairs, "iter_decision"):
        return pairs.iter_decision("REVIEW")
    return (pair for pair in pairs if pair["decision"] == "REVIEW")


def candidate_pairs(pairs, lookup, *, max_pairs):
    """Return [(pair, (left_address, right_address))] for at most max_pairs pairs, in decision order.

    lookup(ids) -> {record_id: record}. It is called only for pairs that passed the decision-level checks,
    in chunks, so memory and I/O stay bounded for large candidate sets.
    """
    selected, chunk = [], []
    if max_pairs <= 0:
        return selected
    for pair in _review_pairs(pairs):
        if not _may_qualify(pair):
            continue
        chunk.append(pair)
        if len(chunk) == LOOKUP_CHUNK:
            _take(chunk, lookup, selected, max_pairs)
            chunk = []
            if len(selected) >= max_pairs:
                break
    if chunk and len(selected) < max_pairs:
        _take(chunk, lookup, selected, max_pairs)
    return selected


def _take(chunk, lookup, selected, max_pairs):
    ids = {rid for pair in chunk for rid in (pair["left_id"], pair["right_id"])}
    records = lookup(sorted(ids))
    for pair in chunk:
        if len(selected) >= max_pairs:
            return
        addresses = _address_pair(records.get(pair["left_id"]), records.get(pair["right_id"]))
        if addresses is not None:
            selected.append((pair, addresses))


def with_annotations(pairs, annotations):
    """Attach each pair's annotation to its payload as it streams out. The decision store is not rewritten."""
    for pair in pairs:
        annotation = annotations.get(pair["id"])
        if annotation is None:
            yield pair
        else:
            yield {**pair, "semantic_annotations": [annotation]}


def _default_requester(rows, settings, context, factory):
    result = generate("ADDRESS_COMPARE", rows, settings, context=context, factory=factory)
    results = {item["pair_id"]: item["result"] for item in result["output"]["items"]}
    meta = {"provider": result["provider"], "model_version": result["model_version"],
            "request_id": result["request_id"]}
    return results, meta


@dataclass
class SemanticResult:
    annotations: dict = field(default_factory=dict)
    stats: dict = field(default_factory=dict)


def _empty_stats(reason=None):
    return {"selected": 0, "unique_keys": 0, "batches": 0, "checked": 0,
            "changed": 0, "errors": 0, "outbound_attempts": 0, "stopped": reason}


def annotate(pairs, lookup, *, run_context, settings=None, factory=None, requester=None, parallel=False):
    """Annotate selected pairs. Never raises for provider or policy failures; those leave pairs unannotated."""
    settings = settings or get_settings()
    if not settings.llm_enabled or not settings.semantic_address_enabled:
        return SemanticResult(stats=_empty_stats("DISABLED"))
    if run_context is None:
        raise PolicyDenied("CONTEXT_REQUIRED")
    stats = _empty_stats()
    selected = candidate_pairs(pairs, lookup, max_pairs=settings.semantic_address_max_pairs_per_run)
    stats["selected"] = len(selected)
    if not selected:
        return SemanticResult(stats=stats)

    groups = {}
    for pair, addresses in selected:
        groups.setdefault(tuple(sorted(addresses)), []).append((pair, addresses))
    keys = list(groups)
    stats["unique_keys"] = len(keys)
    batches = [keys[start:start + BATCH_SIZE] for start in range(0, len(keys), BATCH_SIZE)]
    stats["batches"] = len(batches)
    ask = requester or (lambda rows, config, context: _default_requester(rows, config, context, factory))
    workers = max(1, settings.llm_max_concurrency) if parallel else 1
    limit = settings.llm_consecutive_failure_limit

    def dispatch(batch_keys):
        rows = [{"pair_id": groups[key][0][0]["id"], "left_address": key[0], "right_address": key[1]}
                for key in batch_keys]
        try:
            return ask(rows, settings, run_context)
        except Exception as exc:  # the optional service must never fail the publication
            return exc

    results_by_key, provenance_by_key = {}, {}
    failures, stop = 0, None
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for start in range(0, len(batches), workers):
            window = batches[start:start + workers]
            for batch_keys, outcome in zip(window, pool.map(dispatch, window)):
                stats["outbound_attempts"] += 1
                if isinstance(outcome, BaseException):
                    stats["errors"] += len(batch_keys)
                    failures += 1
                    code = getattr(outcome, "code", type(outcome).__name__)
                    log.warning("Semantic address batch skipped: %s", code)
                    if isinstance(outcome, PolicyDenied) and code in STOP_CODES:
                        stop = code
                        break
                    continue
                results, meta = outcome
                expected = {groups[key][0][0]["id"] for key in batch_keys}
                if set(results) != expected or set(results.values()) - RESULTS:
                    stats["errors"] += len(batch_keys)
                    failures += 1
                    continue
                failures = 0
                for key in batch_keys:
                    results_by_key[key] = results[groups[key][0][0]["id"]]
                    provenance_by_key[key] = meta
            if stop or failures >= limit:
                stop = stop or "FAILURE_LIMIT"
                break
    stats["stopped"] = stop

    annotations = {}
    for key, members in groups.items():
        if key not in results_by_key:
            continue
        for pair, addresses in members:
            stats["checked"] += 1
            annotations[pair["id"]] = _annotation(pair, addresses, results_by_key[key],
                                                  provenance_by_key[key], settings, run_context)
            stats["changed"] += 1
    return SemanticResult(annotations=annotations, stats=stats)


def run_annotations(pairs, lookup, *, run_id, factory=None, parallel=False, settings=None):
    """Entry point for both pipelines. Off means no snapshot, no lookup and no outbound work."""
    settings = settings or get_settings()
    if not settings.llm_enabled or not settings.semantic_address_enabled:
        return SemanticResult(stats=_empty_stats("DISABLED"))
    from masterone.llm.policy import PolicyEnforcer
    # One full fingerprint per stage; every later check is the cheap fence.
    context = PolicyEnforcer(settings, factory).snapshot(run_id)
    return annotate(pairs, lookup, run_context=context, settings=settings, factory=factory, parallel=parallel)


def _annotation(pair, addresses, result, meta, settings, context):
    return {
        "field": "address",
        "pair_id": pair["id"],
        "result": result,
        "provenance": "LLM",
        "provider": meta["provider"],
        "model_version": meta["model_version"],
        "gateway_request_id": meta["request_id"],
        "prompt_version": PROMPT_VERSION["ADDRESS_COMPARE"],
        "schema_version": SCHEMA_VERSION["ADDRESS_COMPARE"],
        "annotation_version": ANNOTATION_VERSION,
        "baseline_hash": baseline_hash(pair),
        "input_hash": _hash(list(addresses)),
        "context": {key: context[key] for key in ("run_id", "input_hash", "review_hash",
                                                   "reference_version", "reference_hash")},
        "eligible_for_auto_merge": False,
    }
