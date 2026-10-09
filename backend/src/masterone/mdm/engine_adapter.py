"""P-119 engine selection and a strict serving boundary; no reviewer state writes."""
import math
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy

from fastapi import Depends, Request
from fastapi.routing import APIRoute
from starlette.datastructures import Headers

from masterone.config import get_settings
from masterone.db.session import get_session
from masterone.mdm.models import ResolutionEngineSelection, ResolutionPair, ResolutionProposal, ResolutionRun

_profile = ContextVar("p119_engine_profile", default=None)
CORE_FEATURES = ("decision_semantics_v2_enabled", "cluster_safety_v2_enabled", "generation_staging_enabled")
STREAMING_FEATURES = tuple("stream_" + boundary + "_enabled" for boundary in (
    "prepare_normalize", "normalize_build", "build_score", "score_decide", "decide_materialize"))
RESUME_FEATURE = "pipeline_resume_enabled"
FEATURES = (*CORE_FEATURES, *STREAMING_FEATURES, RESUME_FEATURE)


class FrontendJSONRoute(APIRoute):
    """Existing api.ts sends some JSON bodies with fetch's text/plain default.

    Normalize transport only; retain Pydantic/OpenAPI validation and CSRF/auth.
    """
    def get_route_handler(self):
        original = super().get_route_handler()

        async def handle(request):
            if request.method in {"POST", "PUT", "PATCH"} and request.headers.get("content-type", "").split(";")[0] == "text/plain":
                request.scope["headers"] = [(k, v) for k, v in request.scope["headers"] if k.lower() != b"content-type"] + [(b"content-type", b"application/json")]
                request._headers = Headers(scope=request.scope)
            return await original(request)
        return handle


def feature(name):
    profile = _profile.get()
    if profile is None:
        return getattr(get_settings(), name)
    if name in (*STREAMING_FEATURES, RESUME_FEATURE):
        return profile.get(name, False)
    return profile[name]


@contextmanager
def engine_context(configuration):
    token = _profile.set(dict(configuration) if configuration is not None else None)
    try:
        yield
    finally:
        _profile.reset(token)


def select_engine(factory, run_id):
    with factory.begin() as db:
        from masterone.mdm.job_queue import guard
        guard(db, run_id)
        run = db.query(ResolutionRun).filter_by(id=run_id).with_for_update().one_or_none()
        if run is None:
            return None
        selection = db.get(ResolutionEngineSelection, run_id)
        if selection is None:
            settings = get_settings()
            existing = db.query(ResolutionPair.id).filter_by(run_id=run_id).first() or db.query(ResolutionProposal.id).filter_by(run_id=run_id).first()
            new = settings.mdm_engine_v2_enabled and not existing
            configuration = {f: True if new else getattr(settings, f) for f in CORE_FEATURES}
            # Absence pins streaming OFF for old and new profiles alike. Opt-in
            # flags are stored explicitly; the engine-v2 switch never enables them.
            configuration.update({f: True for f in STREAMING_FEATURES if getattr(settings, f)})
            if settings.pipeline_resume_enabled:
                configuration[RESUME_FEATURE] = True
            selection = ResolutionEngineSelection(run_id=run_id,
                engine_version="p119_person_v2" if new else "p119_current", configuration=configuration)
            db.add(selection)
        return dict(selection.configuration)


async def api_engine_context(request: Request, db=Depends(get_session)):
    # Async yield keeps ContextVar setup/reset in the request task; sync routes
    # inherit that context through FastAPI's thread pool. Ownership stays in routes.
    selection = db.get(ResolutionEngineSelection, request.path_params["run_id"]) if "run_id" in request.path_params else None
    with engine_context(selection.configuration if selection else None):
        yield


class EngineResultAdapter:
    """DTO-compatible rule units and evidence; never infer user/cannot-link decisions."""

    @staticmethod
    def evidence(fields):
        result = []
        for source in fields:
            similarity = source["similarity"]
            if isinstance(similarity, bool) or not math.isfinite(similarity) or not 0 <= similarity <= 1:
                raise ValueError("ENGINE_EVIDENCE_SIMILARITY")
            item = {k: deepcopy(source[k]) for k in ("field", "state", "provenance", "normalization_tags") if k in source}
            for side in ("left", "right"):
                value = source.get(side)
                if value is not None and not isinstance(value, str):
                    raise ValueError("ENGINE_EVIDENCE_VALUE")
                item[side] = value or ""
            item["similarity"] = float(similarity)
            result.append(item)
        return result

    def pair(self, payload):
        result = dict(payload)
        probability = result.get("match_probability")
        if probability is not None and (isinstance(probability, bool) or not math.isfinite(probability) or not 0 <= probability <= 1):
            raise ValueError("ENGINE_PROBABILITY")
        if "rule_score" in result:
            score = result["rule_score"]
            if "score" in result and result["score"] != score:
                raise ValueError("ENGINE_SCORE_SEMANTICS")
        elif probability is not None and result.get("score_semantics") != "p119_rule_v1":
            raise ValueError("ENGINE_RULE_SCORE_REQUIRED")
        else:
            score = result["score"]  # Existing P-119 contract, never a probability fallback.
        if isinstance(score, bool) or not math.isfinite(score):
            raise ValueError("ENGINE_RULE_SCORE")
        if result.get("primary_group") not in {None, "A", "B", "C", "D"}:
            raise ValueError("ENGINE_GROUP")
        if result["decision"] not in {"MERGEABLE", "REVIEW", "NOT_MERGEABLE", "INSUFFICIENT_EVIDENCE", "BLOCKED", "DEFERRED", "NON_MATCH"}:
            raise ValueError("ENGINE_DECISION")
        result["score"] = float(score)
        result["field_evidence"] = self.evidence(result["field_evidence"])
        if result["decision"] in {"BLOCKED", "DEFERRED"}:
            result["triage_status"] = "REVIEW"
        return result

    def pair_detail(self, row, records, evidence=None):
        return {"id": row.id, "left_external_id": records[row.left_record_id].external_id,
            "right_external_id": records[row.right_record_id].external_id,
            "primary_group": row.primary_group, "subgroup": row.subgroup,
            "score": row.score, "decision": row.pair_decision, "reason": row.triage_reason,
            "field_evidence": self.evidence(row.field_evidence if evidence is None else evidence)}

    @staticmethod
    def golden(golden):
        result = deepcopy(golden or {})
        fields = result.setdefault("fields", {})
        for name, value in result.get("_golden_override", {}).items():
            fields[name] = {**fields.get(name, {}), "value": value, "provenance": "STEWARD_OVERRIDE", "source_record_id": None}
        return result
