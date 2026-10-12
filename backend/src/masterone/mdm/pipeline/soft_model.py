"""Measured, scope-bound Splink review assistance. Never a merge permission."""
import json
import math
from dataclasses import dataclass
from hashlib import sha256
from importlib.metadata import version
from pathlib import Path

import pyarrow as pa

from masterone.mdm.pipeline.scoring_engine import SplinkPredictor, canonical_json

FIELDS = ('name', 'phone', 'email', 'dob', 'address')


class ReviewModelError(ValueError):
    pass


def calibrate(weight, coefficients):
    z = max(-40.0, min(40.0, coefficients['slope'] * weight + coefficients['intercept']))
    return 1.0 / (1.0 + math.exp(-z))


def fit_calibrator(weights, labels):
    if (len(weights) != len(labels) or not weights or set(labels) != {0, 1}
            or any(not math.isfinite(weight) for weight in weights)):
        raise ReviewModelError('CALIBRATION_REQUIRES_BOTH_DEV_CLASSES')
    slope, intercept = 0.0, math.log((sum(labels) + 1) / (len(labels) - sum(labels) + 1))
    for _ in range(100):
        ga = gb = haa = hab = hbb = 0.0
        for weight, label in zip(weights, labels, strict=True):
            probability = calibrate(weight, {'slope': slope, 'intercept': intercept})
            variance = max(1e-12, probability * (1 - probability))
            ga += (probability - label) * weight
            gb += probability - label
            haa += variance * weight * weight
            hab += variance * weight
            hbb += variance
        haa += 1e-4
        hbb += 1e-4
        ga += 1e-4 * slope
        gb += 1e-4 * intercept
        determinant = haa * hbb - hab * hab
        if determinant <= 1e-18:
            raise ReviewModelError('CALIBRATION_NOT_IDENTIFIABLE')
        da, db = (hbb * ga - hab * gb) / determinant, (haa * gb - hab * ga) / determinant
        slope, intercept = slope - da, intercept - db
        if max(abs(da), abs(db)) < 1e-8:
            break
    if not math.isfinite(slope + intercept) or slope <= 0:
        raise ReviewModelError('CALIBRATION_NON_MONOTONIC')
    return {'slope': slope, 'intercept': intercept, 'fit_split': 'dev'}


def projected_record(record):
    nf = record['normalized_fields']
    values = {'record_id': record['id']}
    for field in FIELDS:
        value = nf.get(field) or None
        if nf.get('_field_quality', {}).get(field) in {'INVALID', 'PLACEHOLDER', 'SHARED'}:
            value = None
        values[field] = value
    return values


@dataclass
class ReviewModel:
    manifest: dict
    model: dict
    model_hash: str

    def evaluate(self, records, pairs, progress=lambda *_: None):
        import duckdb
        output = {}
        with duckdb.connect() as connection:
            connection.execute("SET memory_limit='256MB'")
            connection.execute('SET threads=1')
            connection.register('input_records', pa.Table.from_pylist([projected_record(r) for r in records]))
            connection.execute('CREATE TABLE review_records AS SELECT * FROM input_records')
            predictor = SplinkPredictor(connection, 'review_records', self.model)
            for offset in range(0, len(pairs), 2048):
                batch = pairs[offset:offset + 2048]
                keys = [(pair['left_id'], pair['right_id']) for pair in batch]
                predictions = predictor.predict(keys)
                for pair, key in zip(batch, keys, strict=True):
                    raw_probability, weight = predictions[key]
                    probability = calibrate(weight, self.manifest['calibrator'])
                    band = self.manifest['routing']
                    recommendation = ('LIKELY_SAME_PERSON' if probability >= band['upper'] else
                                      'LIKELY_DIFFERENT_PERSON' if probability <= band['lower'] else 'INSUFFICIENT_BASIS')
                    output[pair['id']] = {'status': 'EVALUATED', 'scope': self.manifest['scope'],
                        'model_hash': self.model_hash, 'model_version': self.manifest['model_version'],
                        'raw_match_probability': raw_probability, 'match_weight': weight,
                        'calibrated_probability': probability, 'recommendation': recommendation,
                        'report_hash': self.manifest['evaluation_report_sha256']}
                progress('soft_model', min(offset + 2048, len(pairs)), len(pairs))
        return output


def load_review_model(path):
    if not path:
        return None
    try:
        manifest_path = Path(path).resolve()
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        from masterone.mdm.pipeline import standardization
        if (manifest.get('projection_version') != 'standardized-review-fields-v1'
                or manifest.get('normalization_code_sha256') != sha256(Path(standardization.__file__).read_bytes()).hexdigest()):
            raise ReviewModelError('REVIEW_MODEL_PREPROCESSING_MISMATCH')
        if (manifest.get('schema_version') != 'review-model-v1' or manifest.get('scope') != 'synthetic-benchmark'
                or manifest.get('evaluated') is not True or manifest.get('split_entity_overlap') != 0
                or manifest.get('review_assistance_approved') is not True or manifest.get('production_approved') is not False
                or manifest.get('splink_version') != version('splink') or version('splink') != '5.0.0'):
            raise ReviewModelError('REVIEW_MODEL_NOT_EVALUATED_OR_APPROVED')
        files = {}
        for kind in ('model', 'evaluation_report', 'training_snapshot'):
            target = (manifest_path.parent / manifest[kind + '_file']).resolve()
            if not target.is_relative_to(manifest_path.parent):
                raise ReviewModelError('REVIEW_MODEL_PATH_ESCAPE')
            files[kind] = target.read_bytes()
            if sha256(files[kind]).hexdigest() != manifest[kind + '_sha256']:
                raise ReviewModelError('REVIEW_MODEL_HASH_MISMATCH')
        report = json.loads(files['evaluation_report'])
        if (report.get('gate') != 'PASS_SYNTHETIC_REVIEW' or report.get('split_entity_overlap') != 0
                or report.get('split_source_overlap') != 0 or report.get('split_template_overlap') != 0):
            raise ReviewModelError('REVIEW_MODEL_EVALUATION_FAILED')
        for split in ('train', 'dev', 'test'):
            if any(report['splits'][split].get(key, 0) <= 0 for key in ('positive', 'negative')):
                raise ReviewModelError('REVIEW_MODEL_INCOMPLETE_SPLITS')
        coefficients, band = manifest['calibrator'], manifest['routing']
        if (coefficients.get('fit_split') != 'dev' or coefficients['slope'] <= 0
                or not all(math.isfinite(coefficients[k]) for k in ('slope', 'intercept'))
                or band.get('fit_split') != 'dev' or not 0 <= band['lower'] < band['upper'] <= 1):
            raise ReviewModelError('REVIEW_MODEL_CALIBRATION_INVALID')
        model = json.loads(files['model'])
        if model.get('link_type') != 'dedupe_only' or model.get('unique_id_column_name') != 'record_id':
            raise ReviewModelError('REVIEW_MODEL_CONTRACT_INVALID')
        if not 0 < model.get('probability_two_random_records_match', 0) < 1 or not model.get('comparisons'):
            raise ReviewModelError('REVIEW_MODEL_NOT_FITTED')
        for comparison in model['comparisons']:
            if comparison.get('output_column_name') not in FIELDS:
                raise ReviewModelError('REVIEW_MODEL_UNSUPPORTED_FEATURE')
            levels = [level for level in comparison['comparison_levels'] if not level.get('is_null_level')]
            for parameter in ('m_probability', 'u_probability'):
                values = [level[parameter] for level in levels]
                if any(not math.isfinite(v) or not 0 < v < 1 for v in values) or abs(sum(values) - 1) > 1e-6:
                    raise ReviewModelError('REVIEW_MODEL_INVALID_PARAMETERS')
        return ReviewModel(manifest, model, manifest['model_sha256'])
    except ReviewModelError:
        raise
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise ReviewModelError('REVIEW_MODEL_ARTIFACT_INVALID') from error
