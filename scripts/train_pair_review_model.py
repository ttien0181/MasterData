"""Reproducible synthetic-only review evaluation; no production identity certification.

PYTHONPATH=backend/src .venv/Scripts/python.exe scripts/train_pair_review_model.py --source ... --output data/models/pair-review-v1
Source must be the approved TC4 seed-7 10k records.parquet and its sibling gold key.
Entity split precedes variants; no label or source metadata enters the model.
"""
import argparse
import itertools
import json
import random
from copy import deepcopy
from hashlib import sha256
from importlib.metadata import version
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score
from splink import comparison_library as cl

from masterone.mdm.pipeline.scoring_engine import SplinkPredictor, canonical_json
from masterone.mdm.pipeline.soft_model import FIELDS, calibrate, fit_calibrator, projected_record
from masterone.mdm.pipeline.standardization import standardize


def digest_file(path):
    return sha256(Path(path).read_bytes()).hexdigest()


def build_splits(rows, truth, seed=119, entities=1500):
    # Original E... gold keys cannot be joined to hashed CRM/ERP source IDs in this partial
    # archive. Labels below describe fictitious template-derived identities by construction,
    # NOT the identity of original TC4 records. Same-template variants share a synthetic person.
    groups = {f'synthetic-person-{i}': row for i, row in enumerate(rows[:10000])}
    source_keys, fingerprints = set(), set()
    for row in groups.values():
        key = (row.get('source_system'), row['source_record_id'])
        normalized, _ = standardize({field: str(row[field] or '') for field in FIELDS})
        fingerprint = canonical_json({k:v for k,v in projected_record({'id':'template','normalized_fields':normalized}).items() if k != 'record_id'})
        if key in source_keys or fingerprint in fingerprints:
            raise ValueError('DUPLICATE_SYNTHETIC_TEMPLATE')
        source_keys.add(key)
        fingerprints.add(fingerprint)
    rng = random.Random(seed)
    keys = sorted(groups)
    rng.shuffle(keys)
    keys = keys[:entities]
    if len(keys) < 100:
        raise ValueError('INSUFFICIENT_GOLD_ENTITIES')
    splits = {}
    for split, selected in [('train', keys[:int(.7 * len(keys))]),
                            ('dev', keys[int(.7 * len(keys)):int(.85 * len(keys))]),
                            ('test', keys[int(.85 * len(keys)):])]:
        records, owners, origins = [], {}, []
        for index, entity in enumerate(selected):
            source = groups[entity]
            nf = {field: str(source[field] or '').strip() for field in FIELDS}
            for variant in range(3):
                values = deepcopy(nf)
                if variant == 1:
                    # Typography and sparse contact changes, no artificial matching token.
                    name = values['name']
                    if len(name) > 5:
                        pos = rng.randrange(1, len(name) - 1)
                        values['name'] = name[:pos] + name[pos + 1:] 
                    values[rng.choice(['phone', 'email', 'dob'])] = ''
                    values['address'] = values['address'].replace('Phường', 'P.').replace('Đường', 'Đ.')
                if variant == 2:
                    values[rng.choice(['phone', 'email'])] = ''
                    if index % 3 == 0:
                        values['address'] = ''
                    if index % 4 == 0:
                        values['dob'] = ''
                    if index % 5 == 0:
                        values['name'] = ' '.join(reversed(values['name'].split()))
                rid = f'{split}-{index}-{variant}'
                normalized, _ = standardize(values)
                records.append({'id': rid, 'normalized_fields': normalized})
                owners[rid] = entity
                origins.append({'record_id': rid, 'source_record_id': source['source_record_id'],
                                'synthetic_entity': entity, 'variant': variant})
        pairs = {}
        for index in range(len(selected)):
            own = [f'{split}-{index}-{v}' for v in range(3)]
            for left, right in itertools.combinations(own, 2):
                pairs[(left, right)] = 1
            # Include neighbors (often similarly named in TC4) and random negatives.
            others = {(index + 1) % len(selected), (index + 2) % len(selected)}
            others.update(rng.randrange(len(selected)) for _ in range(4))
            for other in others - {index}:
                left, right = sorted((rng.choice(own), f'{split}-{other}-{rng.randrange(3)}'))
                pairs[(left, right)] = 0
        splits[split] = {'entities': selected, 'records': records, 'origins': origins,
            'pairs': [dict(id=f'{split}-p{i}', left_id=l, right_id=r, label=label)
                      for i, ((l, r), label) in enumerate(sorted(pairs.items()))]}
    # Check actual source lineage and normalized feature templates independently of invented IDs.
    for a, b in itertools.combinations(splits, 2):
        left = {groups[key]['source_record_id'] for key in splits[a]['entities']}
        right = {groups[key]['source_record_id'] for key in splits[b]['entities']}
        if left & right:
            raise ValueError('SYNTHETIC_SOURCE_OVERLAP')
    return splits


def comparisons():
    return [(cl.JaroWinklerAtThresholds(field, [.95, .85]) if field in {'name', 'address'}
             else cl.ExactMatch(field)).create_comparison_dict('duckdb') for field in FIELDS]


def fit_model(split):
    model = {'link_type': 'dedupe_only', 'unique_id_column_name': 'record_id',
             'probability_two_random_records_match': 2 / (len(split['records']) - 1),
             'comparisons': comparisons()}
    with duckdb.connect() as db:
        db.register('records', pa.Table.from_pylist([projected_record(r) for r in split['records']]))
        db.register('pairs', pa.Table.from_pylist(split['pairs']))
        for comparison in model['comparisons']:
            levels = comparison['comparison_levels']
            aliases = ', '.join(f'l."{f}" AS "{f}_l", r."{f}" AS "{f}_r"' for f in FIELDS)
            cases = ' '.join(f'WHEN {level["sql_condition"]} THEN {i}' for i, level in enumerate(levels[:-1]))
            counts = db.execute(f'SELECT label, CASE {cases} ELSE {len(levels)-1} END AS level, count(*) '
                f'FROM (SELECT p.label, {aliases} FROM pairs p JOIN records l ON p.left_id=l.record_id '
                'JOIN records r ON p.right_id=r.record_id) GROUP BY 1,2').fetchall()
            counts = {(label, level): count for label, level, count in counts}
            nonnull = [i for i, level in enumerate(levels) if not level.get('is_null_level')]
            for label, parameter in [(1, 'm_probability'), (0, 'u_probability')]:
                denominator = sum(counts.get((label, i), 0) + 1 for i in nonnull)
                for i in nonnull:
                    levels[i][parameter] = (counts.get((label, i), 0) + 1) / denominator
    return model


def predict(model, split):
    with duckdb.connect() as db:
        db.execute("SET threads=1")
        db.register('records', pa.Table.from_pylist([projected_record(r) for r in split['records']]))
        db.execute('CREATE TABLE review_records AS SELECT * FROM records')
        predictor, weights = SplinkPredictor(db, 'review_records', model), []
        pairs = split['pairs']
        for offset in range(0, len(pairs), 2048):
            keys = [(p['left_id'], p['right_id']) for p in pairs[offset:offset + 2048]]
            result = predictor.predict(keys)
            weights.extend(result[key][1] for key in keys)
        return weights


def choose_band(probabilities, labels):
    # Predeclared dev quality target, not a hand-picked probability threshold.
    lower, upper = 0.0, 1.0
    for threshold in [i / 100 for i in range(1, 100)]:
        low = [label for p, label in zip(probabilities, labels) if p <= threshold]
        high = [label for p, label in zip(probabilities, labels) if p >= threshold]
        if len(low) >= 30 and sum(low) / len(low) <= .05:
            lower = threshold
        if upper == 1.0 and len(high) >= 30 and sum(high) / len(high) >= .95:
            upper = threshold
    if lower >= upper:
        midpoint = (lower + upper) / 2
        lower, upper = max(0, midpoint - .01), min(1, midpoint + .01)
    return {'lower': lower, 'upper': upper, 'fit_split': 'dev', 'precision_target': .95, 'minimum_band_samples': 30}


def metrics(probabilities, labels, band):
    low = [label for p, label in zip(probabilities, labels) if p <= band['lower']]
    high = [label for p, label in zip(probabilities, labels) if p >= band['upper']]
    return {'roc_auc': roc_auc_score(labels, probabilities), 'average_precision': average_precision_score(labels, probabilities),
        'brier': brier_score_loss(labels, probabilities), 'different_band_count': len(low), 'same_band_count': len(high),
        'different_band_precision': 1 - sum(low) / len(low) if low else None,
        'same_band_precision': sum(high) / len(high) if high else None,
        'uncertain_count': len(labels) - len(low) - len(high)}


def train(source, output):
    source, output = Path(source), Path(output)
    truth_path = source.parents[2] / 'truth/answer_keys/BRANCH_SEPARATE.json'
    truth = json.loads(truth_path.read_text(encoding='utf-8'))
    splits = build_splits(pq.read_table(source).to_pylist(), truth)
    overlap = sum(len(set(splits[a]['entities']) & set(splits[b]['entities'])) for a, b in itertools.combinations(splits, 2))
    assert overlap == 0 and all(len(s['records']) <= 10000 for s in splits.values())
    model = fit_model(splits['train'])
    dev_weights = predict(model, splits['dev'])
    dev_labels = [p['label'] for p in splits['dev']['pairs']]
    calibrator = fit_calibrator(dev_weights, dev_labels)
    dev_probabilities = [calibrate(w, calibrator) for w in dev_weights]
    band = choose_band(dev_probabilities, dev_labels)
    test_labels = [p['label'] for p in splits['test']['pairs']]
    test_probabilities = [calibrate(w, calibrator) for w in predict(model, splits['test'])]
    test_metrics = metrics(test_probabilities, test_labels, band)
    passed = (test_metrics['roc_auc'] >= .75 and test_metrics['brier'] <= .25 and
              all(test_metrics[k] is None or test_metrics[k] >= .9 for k in ('different_band_precision', 'same_band_precision')))
    report = {'gate': 'PASS_SYNTHETIC_REVIEW' if passed else 'FAIL', 'scope': 'synthetic-benchmark',
        'split_entity_overlap': overlap, 'split_source_overlap': 0, 'split_template_overlap': 0,
        'label_source': 'Synthetic identity assignments by construction; original gold not joinable',
        'limitations': ['Controlled TC4 templates/variants; not original TC4 gold or real-world validation.',
        'Case-controlled pair sampling; calibrated probabilities are specific to this benchmark sampling.',
        'Conditional-independence assumption; never used for automatic acceptance or constraints.'],
        'source_sha256': digest_file(source), 'truth_sha256': digest_file(truth_path), 'seed': 119,
        'preprocessing': 'Same production standardization and invalid/placeholder masking as review runtime',
        'training': 'Supervised Fellegi-Sunter m/u with add-one smoothing; train only',
        'calibration': 'Platt calibration and routing fit on dev only; test never tuned',
        'splits': {name: {'entities': len(s['entities']), 'records': len(s['records']), 'pairs': len(s['pairs']),
            'positive': sum(p['label'] for p in s['pairs']), 'negative': sum(not p['label'] for p in s['pairs'])}
            for name, s in splits.items()}, 'dev': metrics(dev_probabilities, dev_labels, band), 'test': test_metrics}
    output.mkdir(parents=True, exist_ok=True)
    artifacts = {'model': model, 'evaluation_report': report,
                 'training_snapshot': {name: {'entities': s['entities'], 'origins': s['origins'], 'pairs': s['pairs']}
                                       for name, s in splits.items()}}
    import masterone.mdm.pipeline.standardization as normalization
    manifest = {'schema_version': 'review-model-v1', 'scope': 'synthetic-benchmark', 'model_version': 'tc4-synthetic-v2',
        'projection_version': 'standardized-review-fields-v1', 'normalization_code_sha256': digest_file(normalization.__file__),
        'evaluated': passed, 'split_entity_overlap': 0, 'review_assistance_approved': passed, 'production_approved': False,
        'splink_version': version('splink'), 'calibrator': calibrator, 'routing': band}
    for kind, value in artifacts.items():
        path = output / f'{kind}.json'
        path.write_text(canonical_json(value), encoding='utf-8')
        manifest[kind + '_file'], manifest[kind + '_sha256'] = path.name, digest_file(path)
    (output / 'manifest.json').write_text(canonical_json(manifest), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not passed:
        raise SystemExit('SYNTHETIC_REVIEW_GATE_FAILED')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    train(args.source, args.output)
