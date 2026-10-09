from datetime import date

import pytest

from standalone.algorithms.ablations import ablation_catalog
from standalone.algorithms.blocking import CharNgramTfidfTopK, ExactBlocker, HybridBlocker
from standalone.algorithms.calibration import CalibrationBlockedError, PlattCalibrator
from standalone.algorithms.fellegi_sunter import (
    FellegiSunterFieldScorer, FellegiSunterScorer, FitScopeError, LevelParameters, compare,
)
from standalone.algorithms.normalization import (
    VietnameseNormalizer, accentless_vietnamese, address_compatibility, address_evidence,
    normalize_unicode,
)
from standalone.algorithms.profiling import FitScope, LabelBlindProfiler
from standalone.algorithms.routing import RoutingThresholds, ThresholdPairRouter, route, choose_thresholds_dev
from standalone.algorithms.temporal import compare_temporal
from standalone.baselines.splink import (
    OptionalDependencyUnavailable, SplinkAdapter, UnsupportedSplinkCapability,
)
from standalone.contracts.core import DatasetMetadata, DataSplit, Decision, Probability, Record, RecordFeatures, TaskType
from standalone.contracts.interfaces import CandidateRecord, FieldComparison, IndexPair, NormalizationResult
from standalone.contracts.labels import PairLabel


def features(**kwargs):
    values = {"name": None, "phone": None, "email": None, "address": None}
    values.update(kwargs)
    return RecordFeatures(values)


def metadata(task=TaskType.DEDUPE, sources=("s",), allowed=frozenset()):
    return DatasetMetadata("fixture", "1", task, sources, allowed_source_pairs=allowed)


def test_unicode_vietnamese_and_normalization_idempotent():
    assert normalize_unicode("  Hoa\u0300ng  ") == normalize_unicode("  Hoàng  ")
    assert accentless_vietnamese("Đặng Thị Hòa") == "dang thi hoa"
    normalizer = VietnameseNormalizer()
    record = Record("a", "s", {"name": "  HOÀNG  ", "phone": "+84 912 345 678", "email": "A+B@EXAMPLE.com"})
    normalized = normalizer.transform(record)
    assert normalized.fields["name"].normalized == "hoàng"
    assert normalized.fields["phone"].normalized == "0912345678"
    assert normalized.fields["email"].normalized == "a+b@example.com"
    assert normalizer.transform(Record("b", "s", normalized.feature_values())).feature_values() == normalized.feature_values()
    assert normalized.fields["name"].raw == "  HOÀNG  "
    assert normalized.feature_values()["name"] == "hoang"
    accented = VietnameseNormalizer(use_accentless_name=False).transform(record)
    assert accented.feature_values()["name"] == "hoàng"


def test_normalization_missing_phone_and_ambiguous_reference():
    class Provider:
        version = "ref-v1"
        def resolve_alias(self, alias, admin_version=None):
            from standalone.contracts.interfaces import MappingCandidate
            return (MappingCandidate("1", alias, "ward", "v1"), MappingCandidate("2", alias, "ward", "v1"))
    normalizer = VietnameseNormalizer(reference_provider=Provider())
    missing = normalizer.transform(Record("x", "s", {"phone": "12"}))
    assert missing.fields["phone"].normalized is None
    assert "phone_invalid" in missing.quality_flags
    assert missing.fields["email"].normalized is None
    left = address_evidence("same", Provider())
    right = address_evidence("same", Provider())
    assert left.mapping_status == "ambiguous"
    assert address_compatibility(left, right) == "ambiguous"
    normalized_address = normalizer.transform(Record("address", "s", {"address": "same"}))
    assert normalized_address.address_reference_evidence.mapping_status == "ambiguous"


def test_blockers_are_unique_canonical_source_legal_and_deterministic():
    rows = [features(name="alpha"), features(name="alpha"), features(name="alphi"), features()]
    meta = metadata()
    exact = ExactBlocker().generate(rows, ["s"]*4, meta)
    assert [(p.left_index, p.right_index) for p in exact] == [(0, 1)]
    assert exact[0].reasons == ("exact:name",)
    tfidf = CharNgramTfidfTopK(top_k=1).generate(rows, ["s"]*4, meta)
    assert tfidf == CharNgramTfidfTopK(top_k=1).generate(rows, ["s"]*4, meta)
    hybrid = HybridBlocker().generate(rows, ["s"]*4, meta)
    assert len({(x.left_index, x.right_index) for x in hybrid}) == len(hybrid)
    multi = metadata(TaskType.MULTI_SOURCE, ("a", "b", "c"), frozenset({("a", "b")}))
    cross = ExactBlocker().generate([features(email="x"), features(email="x"), features(email="x")],
                                    ["a", "b", "c"], multi)
    assert [(x.left_index, x.right_index) for x in cross] == [(0, 1)]
    clean = ExactBlocker().generate([features(email="x")]*3, ["a", "b", "b"],
                                    metadata(TaskType.CLEAN_CLEAN, ("a", "b")))
    assert [(x.left_index, x.right_index) for x in clean] == [(0, 1), (0, 2)]
    protocol_rows = [CandidateRecord("s", features(name="same")),
                     CandidateRecord("s", features(name="same"))]
    protocol_output = ExactBlocker().generate(protocol_rows, TaskType.DEDUPE)
    assert isinstance(protocol_output[0], IndexPair)


def test_fellegi_sunter_hand_calculation_smoothing_and_scope():
    params = {(field, level): LevelParameters(.8, .1)
              for field in ("name", "phone", "email", "address")
              for level in ("exact", "disagreement", "missing", "fuzzy_high", "fuzzy_medium")}
    params.update({(field, "missing"): LevelParameters(.2, .9) for field in ("name", "phone", "email", "address")})
    scorer = FellegiSunterScorer(params, .2)
    result = scorer.score(features(name="same"), features(name="same"))
    # log(.2/.8) + log(.8/.1) + 3*log(.2/.9) because other fields are missing.
    expected = __import__("math").log(.2/.8) + __import__("math").log(.8/.1) + 3*__import__("math").log(.2/.9)
    assert result.score.value == pytest.approx(expected)
    assert result.score.kind == "log_odds"
    assert result.trace[0].field == "name" and result.trace[0].level == "exact"
    assert all(__import__("math").isfinite(x.contribution) for x in result.trace)
    assert isinstance(FellegiSunterFieldScorer(params, .2).score(
        FieldComparison({"name": "exact"})), type(result.score))
    examples = [(features(name="a"), features(name="a"), PairLabel.POSITIVE),
                (features(name="a"), features(name="b"), PairLabel.NEGATIVE)]
    fit = FellegiSunterScorer.fit(examples, DataSplit.TRAIN, smoothing=1)
    assert all(0 < p.m <= 1 and 0 < p.u <= 1 for p in fit.parameters.values())
    with pytest.raises(FitScopeError):
        FellegiSunterScorer.fit(examples, DataSplit.TEST)


def test_term_frequency_hub_and_calibration_dev_contract():
    params = {(f, l): LevelParameters(.8, .1) for f in ("name", "phone", "email", "address")
              for l in ("exact", "disagreement", "missing", "fuzzy_high", "fuzzy_medium")}
    a = features(name="common")
    b = features(name="common")
    base = FellegiSunterScorer(params, .5).score(a, b).score.value
    common_value = FellegiSunterScorer(params, .5, term_frequency=True,
                                       frequencies={"name": {"common": 10}}).score(a, b).score.value
    rare_value = FellegiSunterScorer(params, .5, term_frequency=True,
                                     frequencies={"name": {"common": 1}}).score(a, b).score.value
    assert common_value < base and rare_value > common_value
    hub = FellegiSunterScorer(params, .5, frequencies={"name": {"common": 10}}, hub_threshold=5).score(a, b).score.value
    assert hub < base
    cal = PlattCalibrator.fit([-2, -1, 1, 2], [PairLabel.NEGATIVE, PairLabel.NEGATIVE,
                                                PairLabel.POSITIVE, PairLabel.POSITIVE], DataSplit.DEV)
    output = cal.predict(raw_similarity=.9, fs_log_score=2, score=2)
    assert 0 <= output.probability.value <= 1 and output.raw_similarity == .9
    with pytest.raises(CalibrationBlockedError):
        PlattCalibrator.fit([-2, 2], [PairLabel.NEGATIVE, PairLabel.POSITIVE], DataSplit.TEST)


def test_fellegi_sunter_conflict_quality_and_numerical_stability():
    params = {(f, l): LevelParameters(1e-300, 1e-300)
              for f in ("name", "phone", "email", "address")
              for l in ("exact", "disagreement", "missing", "fuzzy_high", "fuzzy_medium")}
    scorer = FellegiSunterScorer(params, .5, probability_floor=1e-12)
    compared = compare(features(phone="0911111111"), features(phone="0922222222"))
    phone = next(item for item in compared if item.field == "phone")
    assert phone.conflict is True
    invalid = scorer.score(features(phone=None), features(phone=None), quality_left=("phone_invalid",))
    assert all(__import__("math").isfinite(item.contribution) for item in invalid.trace)


def test_routing_exact_boundaries_conflict_and_dev_only_thresholds():
    thresholds = RoutingThresholds(.8, .2, "dev")
    assert route(.8, thresholds) is Decision.AUTO_MATCH
    assert route(.2, thresholds) is Decision.NO_MATCH
    assert route(.5, thresholds) is Decision.REVIEW
    assert route(.95, thresholds, hard_conflict=True) is Decision.REVIEW
    assert route(.05, thresholds, sufficient_evidence=False) is Decision.REVIEW
    assert ThresholdPairRouter(thresholds).route(Probability(.8)) is Decision.AUTO_MATCH
    fitted = choose_thresholds_dev([.1, .2, .9, .95], [PairLabel.NEGATIVE, PairLabel.NEGATIVE,
                                                       PairLabel.POSITIVE, PairLabel.POSITIVE], DataSplit.DEV,
                                  target_auto_precision=1)
    assert fitted.source == "dev"


def test_splink_optional_capabilities_and_ablations():
    adapter = SplinkAdapter()
    assert adapter.capabilities.fixed_pair_scoring is False
    with pytest.raises(UnsupportedSplinkCapability):
        adapter.predict_fixed_pairs([])
    available, reason = adapter.availability()
    if not available:
        with pytest.raises(OptionalDependencyUnavailable, match="splink==5.0.0"):
            adapter.require_available()
    else:
        assert "Splink" in reason
    normalized = adapter.normalize_native_prediction({"match_probability": .8, "match_weight": 2.0},
                                                     pair_id="p", auto_match_threshold=.8,
                                                     no_match_threshold=.2)
    assert normalized.score.value == .8 and normalized.decision is Decision.AUTO_MATCH
    class Frame:
        def to_dict(self, orient):
            assert orient == "records"
            return [{"source_dataset_l": "a", "unique_id_l": "1",
                     "source_dataset_r": "b", "unique_id_r": "2",
                     "match_probability": .8, "match_weight": 2.0}]
    class PredictionResult:
        def as_pandas_dataframe(self): return Frame()
    class Inference:
        def predict(self, **kwargs):
            assert kwargs == {"threshold_match_probability": .4}
            return PredictionResult()
    class Linker:
        inference = Inference()
    adapter.require_available = lambda: None
    assert len(adapter.predict_native(Linker(), auto_match_threshold=.8,
                                      no_match_threshold=.2, prediction_threshold=.4)) == 1
    assert ablation_catalog()[-1].available is False


def test_temporal_missing_is_unknown_not_conflict():
    unknown = compare_temporal(None, date(2024, 1, 1))
    known = compare_temporal(date(2023, 1, 1), date(2024, 1, 1))
    assert unknown.status == "unknown"
    assert known.status == "known_dates"


def test_profiler_fit_scope_and_reference_evidence_is_not_a_conflict():
    class EmptyReference:
        version = "reference-v1"
        def resolve_alias(self, alias, admin_version=None): return ()
    normalized = VietnameseNormalizer(reference_provider=EmptyReference()).transform(
        Record("a", "s", {"name": "same", "address": "ward a"}))
    profile = LabelBlindProfiler(hub_min_count=2).fit([normalized], fit_scope=FitScope.TRAIN)
    assert profile.record_count == 1 and profile.fields["name"].cardinality == 1
    with pytest.raises(ValueError, match="dev/test forbidden"):
        LabelBlindProfiler().fit([normalized], fit_scope=FitScope.TEST)
    params = {(f, l): LevelParameters(.8, .1) for f in ("name", "phone", "email", "address")
              for l in ("exact", "disagreement", "missing", "fuzzy_high", "fuzzy_medium")}
    params[("address", "disagreement")] = LevelParameters(.1, .1)
    scorer = FellegiSunterScorer(params, .5)
    left, right = features(address="ward a"), features(address="ward b")
    no_ref = scorer.score(left, right).score.value
    ambiguous = scorer.score(left, right, address_reference="ambiguous",
                             reference_version="reference-v1").score.value
    assert ambiguous == pytest.approx(no_ref)
    assert compare(left, right, address_reference="ambiguous")[3].level == "reference_ambiguous"
    normalized_features = VietnameseNormalizer(reference_provider=EmptyReference()).transform(
        RecordFeatures({"name": "same"}), "s")
    assert isinstance(normalized_features, NormalizationResult)
    proto_profile = LabelBlindProfiler().fit([features(name="x")], DataSplit.TRAIN, sources=["s"])
    assert proto_profile.field_statistics["name"]["cardinality"] == 1
