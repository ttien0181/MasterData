"""Optional Gemini check for ambiguous addresses in otherwise supported pairs.

Only address strings leave the server. Failure leaves deterministic evidence
unchanged; LLM output never creates an automatic merge on its own.
"""

import json
import logging
import time
from difflib import SequenceMatcher
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from masterone.config import Settings, get_settings

log = logging.getLogger(__name__)
_BATCH_SIZE = 10
_MAX_FAILURES = 3
_ADDRESS_WEIGHTS = {"EX": 0.8, "NX": 0.7, "HS": 0.5, "MS": 0.2}
_RESULTS = {"EQUIVALENT", "DIFFERENT", "UNCERTAIN"}


def _candidate(pair: dict, by_id: dict[str, dict]) -> tuple[str, str] | None:
    if pair["decision"] != "REVIEW":
        return None
    evidence = {entry["field"]: entry["state"] for entry in pair["field_evidence"]}
    if evidence.get("name") == "CF" or evidence.get("address") not in {"WS", "MS", "DF"}:
        return None
    if not ({evidence.get("phone"), evidence.get("email")} & {"EX", "NX"}):
        return None
    left = by_id[pair["left_id"]]["normalized_fields"].get("address", "")
    right = by_id[pair["right_id"]]["normalized_fields"].get("address", "")
    if not left or not right:
        return None
    similarity = SequenceMatcher(None, left, right, autojunk=False).ratio()
    return (left, right) if 0.60 <= similarity < 0.85 else None


def _ask_gemini(batch: list[dict], settings: Settings) -> dict[str, str]:
    """Validate every response ID and enum before applying any result."""
    model = settings.gemini_model.strip()
    if not model or any(char in model for char in "?/&#"):
        raise ValueError("Invalid Gemini model")
    endpoint = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    schema = {
        "type": "object",
        "properties": {"items": {"type": "array", "items": {"type": "object", "properties": {
            "pair_id": {"type": "string"}, "result": {"type": "string", "enum": sorted(_RESULTS)},
        }, "required": ["pair_id", "result"]}}},
        "required": ["items"],
    }
    prompt = (
        "Chỉ so sánh hai cách viết địa chỉ cho từng pair_id. "
        "Trả EQUIVALENT khi cùng địa chỉ rõ ràng, DIFFERENT khi khác rõ ràng, "
        "UNCERTAIN khi thiếu căn cứ. Không quyết định gộp khách hàng. "
        "Không suy diễn từ các trường khác. Dữ liệu: "
        + json.dumps(batch, ensure_ascii=False, separators=(",", ":"))
    )
    request = Request(endpoint, data=json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"responseFormat": {"text": {"mimeType": "APPLICATION_JSON", "schema": schema}}},
    }).encode(), headers={"Content-Type": "application/json", "x-goog-api-key": settings.gemini_api_key}, method="POST")
    for attempt in range(2):
        try:
            with urlopen(request, timeout=settings.semantic_address_timeout_seconds) as response:
                body = json.loads(response.read())
            break
        except HTTPError as exc:
            if attempt or exc.code not in {429, 500, 502, 503, 504}:
                raise
        except (URLError, TimeoutError):
            if attempt:
                raise
        time.sleep(0.5)
    output = json.loads(body["candidates"][0]["content"]["parts"][0]["text"])
    items = output["items"]
    expected = {entry["pair_id"] for entry in batch}
    if not isinstance(items, list) or len(items) != len(batch):
        raise ValueError("Incomplete semantic response")
    result = {}
    for item in items:
        pair_id, value = item["pair_id"], item["result"]
        if pair_id in result or pair_id not in expected or value not in _RESULTS:
            raise ValueError("Invalid semantic response")
        result[pair_id] = value
    if set(result) != expected:
        raise ValueError("Mismatched semantic response IDs")
    return result


def _apply(pair: dict, result: str, model: str) -> bool:
    if result == "UNCERTAIN":
        return False
    address = next(entry for entry in pair["field_evidence"] if entry["field"] == "address")
    before = address["state"]
    after = "HS" if result == "EQUIVALENT" else "DF"
    if before == after:
        return False
    address["state"] = after
    address["provenance"] = "LLM"
    address["llm_result"] = result
    address["llm_model"] = model
    address["llm_prompt_version"] = "address-v1"
    pair["score"] = round(pair["score"] + _ADDRESS_WEIGHTS.get(after, 0.0)
                          - _ADDRESS_WEIGHTS.get(before, 0.0), 6)
    pair["match_weight"] = pair["score"]
    # Retain REVIEW even if an address upgrade raises the numerical score.
    return True


def enrich_pairs_with_semantic(pairs: list[dict], by_id: dict[str, dict],
                               settings: Settings | None = None,
                               requester=None) -> dict[str, int]:
    """Use bounded batches; network and schema errors fail closed."""
    stats = {"eligible": 0, "checked": 0, "changed": 0, "errors": 0}
    settings = settings or get_settings()
    if not settings.semantic_address_enabled or not settings.gemini_api_key:
        return stats
    ask = requester or _ask_gemini
    selected = []
    for pair in pairs:
        addresses = _candidate(pair, by_id)
        if addresses is None:
            continue
        stats["eligible"] += 1
        if len(selected) < settings.semantic_address_max_pairs_per_run:
            selected.append((pair, addresses))
    failures = 0
    cache: dict[tuple[str, str], str] = {}
    for start in range(0, len(selected), _BATCH_SIZE):
        batch = selected[start:start + _BATCH_SIZE]
        request_data = []
        representative: dict[tuple[str, str], str] = {}
        for pair, addresses in batch:
            key = tuple(sorted(addresses))
            if key not in cache and key not in representative:
                representative[key] = pair["id"]
                request_data.append({"pair_id": pair["id"], "left_address": addresses[0],
                                     "right_address": addresses[1]})
        try:
            received = ask(request_data, settings) if request_data else {}
            expected = {entry["pair_id"] for entry in request_data}
            if set(received) != expected or set(received.values()) - _RESULTS:
                raise ValueError("Invalid semantic response")
            for key, pair_id in representative.items():
                cache[key] = received[pair_id]
        except Exception as exc:  # optional service must never fail the run
            stats["errors"] += len(batch)
            failures += 1
            log.warning("Semantic address batch skipped: %s", type(exc).__name__)
            if failures >= _MAX_FAILURES:
                break
            time.sleep(min(failures, 2))
            continue
        failures = 0
        for pair, addresses in batch:
            stats["checked"] += 1
            if _apply(pair, cache[tuple(sorted(addresses))], settings.gemini_model):
                stats["changed"] += 1
    return stats
