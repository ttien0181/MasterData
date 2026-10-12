"""Review models require measured provenance and never alter rule permissions."""
import json

import pytest


def test_missing_model_is_explicit_and_has_no_probability():
    from masterone.mdm.pipeline.soft_model import load_review_model
    assert load_review_model('') is None


def test_unmeasured_model_or_tampered_artifact_is_not_loaded(tmp_path):
    from masterone.mdm.pipeline.soft_model import ReviewModelError, load_review_model
    path = tmp_path / 'manifest.json'
    path.write_text(json.dumps({'model': {}, 'scope': 'synthetic', 'evaluated': True}), encoding='utf-8')
    with pytest.raises(ReviewModelError):
        load_review_model(str(path))


def test_calibration_is_finite_and_fit_requires_both_dev_classes():
    from masterone.mdm.pipeline.soft_model import ReviewModelError, calibrate, fit_calibrator
    with pytest.raises(ReviewModelError):
        fit_calibrator([1, 2], [0, 0])
    coefficients = fit_calibrator([-5, -2, 2, 5], [0, 0, 1, 1])
    assert 0 <= calibrate(-10000, coefficients) < calibrate(10000, coefficients) <= 1


def test_model_scope_and_holdout_are_required(tmp_path):
    from masterone.mdm.pipeline.soft_model import ReviewModelError, load_review_model
    path = tmp_path / 'manifest.json'
    path.write_text(json.dumps({'schema_version': 'review-model-v1', 'evaluated': True,
                               'scope': 'production', 'split_entity_overlap': 3}), encoding='utf-8')
    with pytest.raises(ReviewModelError):
        load_review_model(str(path))
