"""Annotations reach the published generation as audit events, and are dropped alone when they no longer fit."""

from masterone.llm.policy import snapshot as policy_snapshot
from masterone.mdm.generation_staging import GenerationPublisher
from masterone.mdm.models import ResolutionAuditEvent, ResolutionPair
from masterone.mdm.pipeline.semantic_address import baseline_hash

from tests.test_mdm.test_generation_staging import snapshot  # noqa: F401 - shared pipeline fixture


def _annotation(pair, context, *, result="UNCERTAIN", baseline=None, input_hash=None):
    return {
        "field": "address", "pair_id": pair["id"], "result": result, "provenance": "LLM",
        "provider": "gemini", "model_version": "gemini-test", "gateway_request_id": "req-1",
        "prompt_version": "address-v3", "schema_version": "address-response-v3",
        "annotation_version": "address-annotation-v3",
        "baseline_hash": baseline or baseline_hash(pair),
        "input_hash": "addresses-hash",
        "context": {**{key: context[key] for key in ("run_id", "input_hash", "review_hash",
                                                     "reference_version", "reference_hash")},
                    **({"input_hash": input_hash} if input_hash else {})},
        "eligible_for_auto_merge": False,
    }


def _events(factory, action):
    with factory() as db:
        return [(row.pair_id, row.details) for row in db.query(ResolutionAuditEvent)
                .filter_by(action=action).order_by(ResolutionAuditEvent.id)]


def _publish(factory, computed):
    publisher = GenerationPublisher(factory, batch_size=2)
    identity = publisher.begin("run", config={"v": 1})
    publisher.stage(identity, computed)
    publisher.promote(identity)
    return identity


def test_current_annotation_is_published_as_an_audit_event_bound_to_its_generation(snapshot):  # noqa: F811
    factory, computed, _ = snapshot
    pair = computed["pairs"][0]
    with factory() as db:
        context = snapshot_for(db)
    annotated = {**computed, "semantic_annotations": {pair["id"]: _annotation(pair, context)}}
    identity = _publish(factory, annotated)

    events = _events(factory, "LLM_ADDRESS_ANNOTATION")
    assert [pair_id for pair_id, _ in events] == [pair["id"]]
    details = events[0][1]
    assert details["origin"] == "SYSTEM"
    assert details["semantic_annotation"]["result"] == "UNCERTAIN"
    assert details["semantic_annotation"]["eligible_for_auto_merge"] is False
    assert details["semantic_annotation"]["generation_id"] == identity.generation_id
    with factory() as db:
        stored = db.get(ResolutionPair, pair["id"])
        assert stored.pair_decision == pair["decision"]  # the decision is untouched by the annotation


def test_annotation_for_a_changed_baseline_is_discarded_and_the_pair_still_publishes(snapshot):  # noqa: F811
    factory, computed, _ = snapshot
    pair = computed["pairs"][0]
    with factory() as db:
        context = snapshot_for(db)
    stale = _annotation(pair, context, baseline="0" * 64)
    _publish(factory, {**computed, "semantic_annotations": {pair["id"]: stale}})

    assert _events(factory, "LLM_ADDRESS_ANNOTATION") == []
    discarded = _events(factory, "LLM_ADDRESS_DISCARDED")
    assert [(pair_id, details["reason"]) for pair_id, details in discarded] == [(pair["id"], "BASELINE_CHANGED")]
    with factory() as db:
        assert db.get(ResolutionPair, pair["id"]) is not None


def test_annotation_produced_under_other_inputs_is_discarded_as_stale(snapshot):  # noqa: F811
    factory, computed, _ = snapshot
    pair = computed["pairs"][0]
    with factory() as db:
        context = snapshot_for(db)
    outdated = _annotation(pair, context, input_hash="inputs-before-a-reimport")
    _publish(factory, {**computed, "semantic_annotations": {pair["id"]: outdated}})

    discarded = _events(factory, "LLM_ADDRESS_DISCARDED")
    assert [(pair_id, details["reason"]) for pair_id, details in discarded] == [(pair["id"], "STALE")]
    assert _events(factory, "LLM_ADDRESS_ANNOTATION") == []


def test_publication_without_annotations_writes_no_llm_events(snapshot):  # noqa: F811
    factory, computed, _ = snapshot
    _publish(factory, {**computed, "semantic_annotations": {}})
    assert _events(factory, "LLM_ADDRESS_ANNOTATION") == []
    assert _events(factory, "LLM_ADDRESS_DISCARDED") == []


def snapshot_for(db):
    return policy_snapshot(db, "run")
