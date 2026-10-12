"""Retry-safe publication: existing serving identities and human state are retained.

This is conservative insert-only retry, not a new generation/recompute protocol.
Conflicting proposals require an explicit future reconciliation workflow.
"""
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import insert, select

from masterone.db.base import now_utc
from masterone.mdm.models import (
    ResolutionAuditEvent,
    ResolutionPair,
    ResolutionProposal,
    ResolutionPublication,
    ResolutionReviewCase,
    ResolutionRun,
)

BULK_ROWS = 5000
ANNOTATION_ACTION = "LLM_ADDRESS_ANNOTATION"
DISCARD_ACTION = "LLM_ADDRESS_DISCARDED"
FRESHNESS_KEYS = ("input_hash", "review_hash", "reference_version", "reference_hash")


class _AnnotationAudit:
    """Turns pending LLM annotations into audit rows.

    An annotation is kept only if its baseline is unchanged and the run's inputs, human review state and
    reference data still match the context it was produced under. The full fingerprint is computed at most once
    per publication. Discarded annotations leave the pair and its decision untouched.
    """

    def __init__(self, db, run, generation_id):
        self.db = db
        self.run = run
        self.generation_id = generation_id
        self.rows = []
        self._current = None

    def _snapshot(self):
        if self._current is None:
            from masterone.llm.policy import snapshot
            self._current = snapshot(self.db, self.run.id)
        return self._current

    def add(self, pair_id, raw, annotation):
        from masterone.mdm.pipeline.semantic_address import baseline_hash
        if annotation.get("pair_id") != pair_id or annotation.get("baseline_hash") != baseline_hash(raw):
            self._discard(pair_id, annotation, "BASELINE_CHANGED")
            return
        context = annotation.get("context") or {}
        current = self._snapshot()
        if context.get("run_id") != self.run.id or any(context.get(key) != current[key] for key in FRESHNESS_KEYS):
            self._discard(pair_id, annotation, "STALE")
            return
        self._record(pair_id, ANNOTATION_ACTION, annotation, {"semantic_annotation": {
            key: annotation[key] for key in ("field", "result", "provenance", "provider", "model_version",
                                             "prompt_version", "schema_version", "annotation_version",
                                             "baseline_hash", "input_hash", "gateway_request_id")
            if key in annotation} | {"eligible_for_auto_merge": False, "generation_id": self.generation_id}})

    def _discard(self, pair_id, annotation, reason):
        self._record(pair_id, DISCARD_ACTION, annotation, {"reason": reason, "generation_id": self.generation_id,
                                                           "gateway_request_id": annotation.get("gateway_request_id")})

    def _record(self, pair_id, action, annotation, details):
        # Deterministic id: a retried publication writes the same event at most once.
        key = f"{self.run.id}:{self.generation_id or 'legacy'}:{pair_id}:{annotation.get('baseline_hash')}:{action}"
        self.rows.append({"id": str(uuid5(NAMESPACE_URL, key)), "run_id": self.run.id,
                          "actor_user_id": self.run.owner_user_id, "pair_id": pair_id, "proposal_id": None,
                          "action": action, "details": {"origin": "SYSTEM", **details}, "created_at": now_utc()})

    def flush(self):
        """Call after the pair rows exist: audit rows reference them."""
        if not self.rows:
            return
        ids = [row["id"] for row in self.rows]
        existing = set()
        for start in range(0, len(ids), BULK_ROWS):
            existing.update(self.db.scalars(select(ResolutionAuditEvent.id).where(
                ResolutionAuditEvent.id.in_(ids[start:start + BULK_ROWS]))))
        _bulk_insert(self.db, ResolutionAuditEvent, [row for row in self.rows if row["id"] not in existing])


def _bulk_insert(db, model, rows):
    # Core executemany in batches. The ORM path built one identity-mapped object per
    # row and flushed them all at once; Core applies the same column defaults without that cost.
    for start in range(0, len(rows), BULK_ROWS):
        db.execute(insert(model), rows[start:start + BULK_ROWS])


def persist_snapshot(db, run_id, computed, *, generation_id=None):
    from masterone.mdm.job_queue import guard
    guard(db, run_id)
    from masterone.mdm.engine_adapter import EngineResultAdapter
    adapter = EngineResultAdapter()
    # Same lock as active pair review: do not publish over a concurrent reviewer.
    run = db.query(ResolutionRun).filter_by(id=run_id).with_for_update().one()
    publication = db.get(ResolutionPublication, run_id)
    if publication is not None and generation_id is None:
        raise RuntimeError("Generation publication gate required for this run")
    # Plain tuples rather than ORM objects: a large run would otherwise keep every stored pair in the session.
    stored = db.query(ResolutionPair.id, ResolutionPair.left_record_id, ResolutionPair.right_record_id,
                      ResolutionPair.user_decision).filter_by(run_id=run_id).all()
    id_by_key = {tuple(sorted((left, right))): pid for pid, left, right, _ in stored}
    # id -> (left_record_id, right_record_id, user_decision); user_decision is None for rows not yet flushed.
    pair_info = {pid: (left, right, decision) for pid, left, right, decision in stored}
    id_map = {}
    new_pairs = []
    annotations = _AnnotationAudit(db, run, generation_id)
    for pair in computed["pairs"]:
        raw = pair
        pair = adapter.pair(pair)
        key = tuple(sorted((pair["left_id"], pair["right_id"])))
        if key in id_by_key:
            id_map[pair["id"]] = id_by_key[key]
            continue
        new_pairs.append({
            "id": pair["id"], "run_id": run_id,
            "left_record_id": pair["left_id"], "right_record_id": pair["right_id"],
            "blocking_reasons": pair["blocking_reasons"], "field_evidence": pair["field_evidence"],
            "score": pair["score"], "pair_decision": pair["decision"], "decision_reason": pair["reason"],
            "triage_status": pair["triage_status"], "triage_reason": pair["triage_reason"],
            "primary_group": pair["primary_group"], "subgroup": pair["subgroup"],
            "signature": pair["signature"], "normalization_tags": pair["normalization_tags"],
            "policy_id": pair["policy_id"], "policy_version": pair["policy_version"],
        })
        id_by_key[key] = pair["id"]
        pair_info[pair["id"]] = (pair["left_id"], pair["right_id"], None)
        id_map[pair["id"]] = pair["id"]
        for annotation in raw.get("semantic_annotations") or []:
            annotations.add(pair["id"], raw, annotation)
    _bulk_insert(db, ResolutionPair, new_pairs)
    annotations.flush()

    saved_proposals = db.query(ResolutionProposal.id, ResolutionProposal.member_record_ids).filter_by(run_id=run_id).all()
    proposal_ids = {pid for pid, _ in saved_proposals}
    # Historical/rejected proposals also reserve their members during retry.
    reserved = {rid for _, members in saved_proposals for rid in members}
    saved_cases = db.query(ResolutionReviewCase.id, ResolutionReviewCase.status, ResolutionReviewCase.revision,
                           ResolutionReviewCase.member_record_ids).filter_by(run_id=run_id).all()
    reserved.update(rid for _, status, revision, members in saved_cases
                    if status != "OPEN" or revision > 1 for rid in members)
    reserved.update(rid for _, left, right, decision in stored
                    if decision != "UNDECIDED" for rid in (left, right))
    new_proposals = []
    for proposal in computed["proposals"]:
        if proposal["id"] in proposal_ids or reserved.intersection(proposal["member_record_ids"]):
            continue
        new_proposals.append({
            "id": proposal["id"], "run_id": run_id, "member_record_ids": proposal["member_record_ids"],
            "golden_fields": proposal["golden_fields"], "status": proposal["status"],
            "revision": proposal["revision"],
        })
        reserved.update(proposal["member_record_ids"])
    _bulk_insert(db, ResolutionProposal, new_proposals)

    case_ids = {cid for cid, *_ in saved_cases}
    new_cases = []
    for case in computed["review_cases"]:
        if case["id"] in case_ids:
            continue
        ids = [id_map[p] for p in case["pair_ids"] if p in id_map
               and pair_info[id_map[p]][2] in {None, "UNDECIDED", "DEFERRED"}]
        if not ids:
            continue
        new_cases.append({
            "id": case["id"], "run_id": run_id, "pair_ids": ids,
            "member_record_ids": sorted({rid for p in ids for rid in (pair_info[p][0], pair_info[p][1])}),
            "primary_group": case["primary_group"], "subgroup": case["subgroup"],
            "signature_hash": case["signature_hash"], "triage_reason": case["triage_reason"],
        })
    _bulk_insert(db, ResolutionReviewCase, new_cases)

    from masterone.mdm.run_summary import refresh_payload
    refresh_payload(db, run)  # overview figures describe exactly the pairs written above
    guard(db, run_id)
