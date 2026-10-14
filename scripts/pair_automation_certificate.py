"""Build an operator-reviewable certificate from disjoint shadow components/labels.

Production scope/holdout/representativeness are explicit operator attestations.
This tool never deploys, pins a certificate or enables automatic acceptance.
"""
import argparse
from collections import defaultdict
import csv
from hashlib import sha256
import json
from pathlib import Path
import sys


def build_certificate(shadows,owners,*,data_scope,scope,independent,representative,expires_at,validation_hash):
    if not shadows:raise ValueError('No shadow samples')
    identity=tuple(shadows[0][f] for f in ('code_identity','profile_identity','owner_user_id'))
    if any(tuple(s[f] for f in ('code_identity','profile_identity','owner_user_id'))!=identity for s in shadows):
        raise ValueError('Mixed shadow identity')
    seen=set();sources=set();counts=defaultdict(lambda:dict(correct=0,labelled=0,attempted=0,constraint_violations=0))
    for shadow in shadows:
        sources.update(shadow['source_systems'])
        for component in shadow['components']:
            ids=component['record_ids']
            if len(ids)<2 or len(ids)>20 or len(ids)!=len(set(ids)) or seen & set(ids):raise ValueError('Invalid or overlapping component samples')
            seen.update(ids);c=counts[component['cohort']];c['attempted']+=1
            labels=[owners.get(rid) for rid in ids]
            if all(label not in (None,'') for label in labels):
                c['labelled']+=1;c['correct']+=len(set(labels))==1
    return dict(version='controlled-pair-automation-v1',assessment_version='person-review-assessment-v4',
        code_identity=identity[0],profile_identity=identity[1],owner_user_id=identity[2],data_scope=data_scope,
        scope=scope,independent_holdout=independent,representative=representative,precision_target=.995,
        expires_at=expires_at,validation_dataset_hash=validation_hash,source_systems=sorted(sources),cohorts=dict(counts))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--shadows',nargs='+',type=Path,required=True)
    parser.add_argument('--owners',type=Path,required=True,help='CERTAIN ownership CSV: record_id,entity_id')
    parser.add_argument('--data-scope',required=True)
    parser.add_argument('--scope',choices=['production','synthetic-benchmark'],default='synthetic-benchmark')
    parser.add_argument('--independent-holdout',action='store_true')
    parser.add_argument('--representative',action='store_true')
    parser.add_argument('--expires-at',required=True,help='ISO8601 timezone required')
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    shadows=[];hashes=[]
    for path in args.shadows:
        raw=path.read_bytes();data=json.loads(raw)
        if 'result' in data:
            if data.get('status')!='COMPLETED' or data.get('operation_type')!='AUTOMATION_SHADOW':parser.error('Completed shadow operations required')
            data=data['result']
        shadows.append(data);hashes.append(sha256(raw).hexdigest())
    raw=args.owners.read_bytes();owners={}
    for row in csv.DictReader(raw.decode('utf-8-sig').splitlines()):
        rid=row.get('record_id')
        if not rid or rid in owners:parser.error('Missing/duplicate ownership record ID')
        owners[rid]=row.get('entity_id') or None
    validation_hash=sha256(json.dumps({'shadows':hashes,'owners':sha256(raw).hexdigest()},sort_keys=True).encode()).hexdigest()
    certificate=build_certificate(shadows,owners,data_scope=args.data_scope,scope=args.scope,
        independent=args.independent_holdout,representative=args.representative,expires_at=args.expires_at,validation_hash=validation_hash)
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend/src'))
    from masterone.mdm.pipeline.pair_automation import code_identity,validate_certificate
    gate=validate_certificate(certificate,code_identity=code_identity(),profile_identity=certificate['profile_identity'],
        owner_user_id=certificate['owner_user_id'],data_scope=args.data_scope,sources=set(certificate['source_systems']))
    args.output.parent.mkdir(parents=True,exist_ok=True)
    payload=json.dumps(certificate,indent=2,ensure_ascii=False).encode();args.output.write_bytes(payload)
    args.output.with_suffix('.validation.json').write_text(json.dumps(dict(gate=gate,certificate_sha256=sha256(payload).hexdigest(),
        runtime_activation=False,attestations='Scope/holdout/representativeness asserted by operator; not inferred by software'),indent=2),encoding='utf-8')
    print(json.dumps(dict(eligible=gate['eligible'],reasons=gate['reasons'],runtime_activation=False)))
    return 0 if gate['eligible'] else 2


if __name__=='__main__':raise SystemExit(main())
