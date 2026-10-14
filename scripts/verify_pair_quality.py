"""Raw-only exploratory pair quality and guarded shadow automation; no DB writes.

Labels are opened only after assessment and shadow clustering. Inspected synthetic
bundles cannot certify production automation, irrespective of observed precision.
"""
import argparse
import hashlib
import json
import math
import os
import sys
from collections import Counter, defaultdict
from contextlib import closing
from itertools import combinations
from pathlib import Path


def ratio(numerator, denominator):
    return numerator / denominator if denominator else None


def positive_pairs(owners):
    return sum(n * (n - 1) // 2 for n in Counter(owners.values()).values())


def confidence_interval(successes, trials):
    if type(successes) is not int or type(trials) is not int or not 0 <= successes <= trials:
        raise ValueError('Invalid confidence counts')
    if not trials:
        return None
    z = 1.959963984540054  # conservative two-sided Wilson 95% interval
    p, scale = successes / trials, 1 + z * z / trials
    center = (p + z * z / (2 * trials)) / scale
    width = z * math.sqrt(p * (1 - p) / trials + z * z / (4 * trials * trials)) / scale
    return [max(0, center - width), min(1, center + width)]


def automation_readiness(correct, labelled, attempted, *, scope, independent_holdout,
                         representative, precision_target=.995, constraint_violations=0):
    if (any(type(n) is not int or n < 0 for n in (correct,labelled,attempted,constraint_violations))
            or not correct <= labelled <= attempted or not math.isfinite(precision_target)
            or not 0 < precision_target < 1):
        raise ValueError('Invalid automation gate inputs')
    interval = confidence_interval(correct, labelled)
    lower = interval[0] if interval else None
    reasons = []
    if not attempted:
        reasons.append('NO_ATTEMPTED_MERGES')
    if lower is None or lower < precision_target:
        reasons.append('PRECISION_BOUND_BELOW_TARGET')
    if labelled != attempted:
        reasons.append('INCOMPLETE_LABEL_COVERAGE')
    if scope != 'production':
        reasons.append('SYNTHETIC_ONLY')
    if independent_holdout is not True:
        reasons.append('NO_INDEPENDENT_HOLDOUT')
    if representative is not True:
        reasons.append('NON_REPRESENTATIVE_LABELS')
    if constraint_violations:
        reasons.append('CONSTRAINT_VIOLATION')
    return dict(eligible=not reasons, precision_target=precision_target, precision_lower_bound=lower,
                scope=scope, reasons=reasons, unit='attempted multi-record components',
                confidence='Wilson two-sided 95%; descriptive on correlated/exploratory data',
                runtime_activation=False)


def review_metrics(record_ids, owners, candidates, outcomes):
    owners = {rid: entity for rid, entity in owners.items() if rid in record_ids and entity not in (None,'')}
    total = positive_pairs(owners)
    retrieved = sum(a in owners and b in owners and owners[a] == owners[b] for a,b in candidates)
    required = tp = fp = unknown = strict_tp = strict_fp = strict_unknown = 0
    recovered = defaultdict(lambda: Counter(total=0,true_positive=0,false_positive=0,unlabelled=0))
    outside = Counter()
    for result in outcomes:
        a,b = result['left_id'],result['right_id']
        if a not in record_ids or b not in record_ids:
            continue
        known = a in owners and b in owners
        same = known and owners[a] == owners[b]
        if result['required']:
            required += 1
            tp += same
            fp += known and not same
            unknown += not known
        elif same:
            outside[result['decision']] += 1
        if result['decision'] == 'MERGEABLE':
            strict_tp += same
            strict_fp += known and not same
            strict_unknown += not known
        if result.get('review_support'):
            counts = recovered[result['review_support']['kind']]
            counts['total'] += 1
            counts['true_positive' if same else 'false_positive' if known else 'unlabelled'] += 1
    return dict(known_positive_pairs=total, labelled_records=len(owners),
        retrieved_positive_pairs=retrieved, blocking_missed_positive_pairs=total-retrieved,
        retrieval_recall=ratio(retrieved,total), required_pairs=required, required_positive_pairs=tp,
        required_false_positive_pairs=fp, required_unlabelled_pairs=unknown,
        review_capture_recall=ratio(tp,total), review_known_precision=ratio(tp,tp+fp),
        review_precision_interval=confidence_interval(tp,tp+fp),
        recovered_by_kind={kind:{**counts,'known_precision':ratio(counts['true_positive'],counts['true_positive']+counts['false_positive'])}
                           for kind,counts in sorted(recovered.items())},
        known_same_entity_outside_review=dict(outside),
        strict_mergeable_shadow=dict(true_positive=strict_tp,false_positive=strict_fp,unlabelled=strict_unknown,
                                    known_precision=ratio(strict_tp,strict_tp+strict_fp)))


def cluster_metrics(record_ids, owners, groups):
    owners = {rid:entity for rid,entity in owners.items() if rid in record_ids and entity not in (None,'')}
    groups = [list(group) for group in groups]
    if any(len(set(group)) != len(group) for group in groups):
        raise ValueError('Duplicate cluster membership')
    groups = [set(group) for group in groups]
    seen = set()
    for group in groups:
        if not group or not group <= record_ids or seen & group:
            raise ValueError('Invalid or duplicate cluster membership')
        seen.update(group)
    groups.extend({rid} for rid in sorted(record_ids-seen))
    class_sizes = Counter(owners.values())
    tp = fp = attempted = labelled = correct = false = predicted = bcp = bcr = 0
    for group in groups:
        counts = Counter(owners[rid] for rid in group if rid in owners)
        n = sum(counts.values())
        within = sum(size*(size-1)//2 for size in counts.values())
        tp += within
        fp += n*(n-1)//2-within
        predicted += len(group)*(len(group)-1)//2
        if len(group)>1:
            attempted += 1
            labelled += n==len(group)
            correct += n==len(group) and len(counts)==1
            false += len(counts)>1
        if n:
            bcp += sum(size*size/n for size in counts.values())
            bcr += sum(size*size/class_sizes[entity] for entity,size in counts.items())
    return dict(cluster_pair_true_positive=tp,cluster_pair_false_positive=fp,
        cluster_pair_precision=ratio(tp,tp+fp),cluster_pair_recall=ratio(tp,positive_pairs(owners)),
        labelled_predicted_pairs=tp+fp,total_predicted_pairs=predicted,
        cluster_pair_label_coverage=ratio(tp+fp,predicted),
        cluster_pair_precision_interval=confidence_interval(tp,tp+fp),
        attempted_multi_record_components=attempted,labelled_multi_record_components=labelled,
        correct_multi_record_components=correct,false_merge_components=false,
        b_cubed_precision=ratio(bcp,len(owners)),b_cubed_recall=ratio(bcr,len(owners)),
        b_cubed_scope='CERTAIN-labelled members only',singleton_count=sum(len(g)==1 for g in groups))


def person_scope(records):
    """Use the same missing-type fallback as runtime person_constraints."""
    return {r['id'] for r in records if (r.get('entity_type') or
            r.get('normalized_fields',{}).get('entity_type','UNKNOWN')) in {'PERSON','UNKNOWN'}}


def source_code_identity(source_root):
    hashes={p.relative_to(source_root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted((source_root/'masterone').rglob('*.py'))}
    if not hashes:
        raise ValueError('Empty source code identity')
    return dict(digest=hashlib.sha256(json.dumps(hashes,sort_keys=True).encode()).hexdigest(),files=hashes)


def scoped_cluster_metrics(record_ids, all_record_ids, owners, groups):
    """Exclude whole out-of-scope components, never conceal cross-domain merges."""
    scoped=[]
    seen=set()
    for group in groups:
        members=set(group)
        if not members or len(members)!=len(group) or not members<=all_record_ids or seen & members:
            raise ValueError('Invalid or duplicate cluster membership')
        seen.update(members)
        if members & record_ids:
            if not members<=record_ids:
                raise ValueError('Mixed-scope cluster')
            scoped.append(group)
    return cluster_metrics(record_ids,owners,scoped)


def verify_integrity(bundle):
    import yaml
    path = bundle/'manifest.json'
    manifest = json.loads(path.read_text(encoding='utf-8')) if path.exists() else yaml.safe_load((bundle/'manifest.yaml').read_text(encoding='utf-8'))
    checked = {}
    for relative, expected in manifest['file_hashes'].items():
        if not (relative.startswith(('raw/','input_p119/')) or relative=='truth/records.parquet'):
            continue
        target = (bundle/relative).resolve()
        if not target.is_relative_to(bundle.resolve()):
            raise ValueError('Unsafe manifest path')
        with target.open('rb') as stream:
            actual = hashlib.file_digest(stream,'sha256').hexdigest()
        if actual != expected:
            raise ValueError('Source integrity mismatch: '+relative)
        checked[relative]=actual
    if 'truth/records.parquet' not in checked or len(checked)<2:
        raise ValueError('Incomplete input integrity manifest')
    return checked


def verify(bundle, cap, target):
    from dataclasses import asdict
    from verify_pair_correctness import raw_records
    from masterone.mdm.engine_adapter import engine_context
    from masterone.mdm.pipeline.standardization import standardize
    from masterone.mdm.pipeline.blocking import generate_candidates
    from masterone.mdm.pipeline.matching import value_frequencies
    from masterone.mdm.pipeline import matching
    from masterone.mdm.pipeline.pair_assessment import VERSION,assessment_profile,assessment_record,assess_prepared_pair
    from masterone.mdm.pipeline.clustering import build_proposals
    from masterone.mdm.pipeline.cluster_policy import load_policy
    from masterone.mdm.pipeline.decision_semantics import person_constraints
    from masterone.mdm.pipeline.decision_store import DecisionStore
    from masterone.mdm.workflow_review_rules import review_required
    import pyarrow.parquet as pq

    integrity=verify_integrity(bundle)  # only bytes/hashes, never decoded labels
    diagnostics={}
    source,available=raw_records(bundle,cap,diagnostics)
    records,keys=[],{}
    for key,raw in source:
        rid=hashlib.sha256(key.encode()).hexdigest()
        nf,flags=standardize(raw,raw.get('entity_type','UNKNOWN'))
        records.append(assessment_record(dict(id=rid,external_id=raw['source_record_id'],source_system=raw['source_system'],
            entity_type=raw.get('entity_type','UNKNOWN'),raw_fields=raw,normalized_fields=nf,quality_flags=flags)))
        keys[rid]=key
    by_id={r['id']:r for r in records}
    in_scope=person_scope(records)
    frequencies=value_frequencies(records)
    decisions=Counter()
    outcomes=[]
    candidate_hash,evidence_hash=hashlib.sha256(),hashlib.sha256()
    unsupported_recovery=0
    with engine_context(assessment_profile()),closing(DecisionStore()) as store:
        candidates=generate_candidates(records)
        for a,b in candidates:
            result=assess_prepared_pair(by_id[a],by_id[b],frequencies)
            result['id']=hashlib.sha256(f'{a}:{b}'.encode()).hexdigest()
            decisions[result['decision']]+=1
            unsupported_recovery += result['rule_conclusion']=='INSUFFICIENT_EVIDENCE' and result['decision']=='REVIEW' and not result.get('review_support')
            candidate_hash.update(f'{a}:{b}\n'.encode())
            evidence_hash.update(json.dumps([a,b,result.get('score'),result.get('signature')],sort_keys=True).encode())
            outcomes.append({'left_id':a,'right_id':b,'decision':result['decision'],
                'required':review_required('UNDECIDED',result),'review_support':result.get('review_support')})
            # Counterfactual: propose strict MERGEABLE conclusions through guards.
            # REVIEW recovery is never promoted into this shadow automatic route.
            store.add(result)
        store.seal()
        shadow=build_proposals(by_id,store,'quality-shadow',VERSION)
        groups=[c['member_record_ids'] for c in shadow['proposals']]
        guarded_rejections=Counter(pair.get('triage_reason','UNKNOWN') for pair in shadow['risky_pairs'])
        policy=load_policy()
        violations=sum(len(group)>policy.max_cluster_size or any(person_constraints(by_id[a],by_id[b]).forbids_merge
                        for a,b in combinations(group,2)) for group in groups)
    if unsupported_recovery:
        raise ValueError('Unsupported floor failure was promoted to REVIEW')
    # Raw matching and guarded shadow output are complete before reading truth.
    gold=pq.read_table(bundle/'truth/records.parquet').to_pylist()
    truth={r['record_key']:r['entity_id'] for r in gold if r.get('label_status')=='CERTAIN' and r.get('entity_id') not in (None,'')}
    if not any(key in truth for key in keys.values()):
        truth={r['source_system']+':'+r['source_record_id']:r['entity_id'] for r in gold if r.get('label_status')=='CERTAIN' and r.get('entity_id') not in (None,'')}
    owners={rid:truth[key] for rid,key in keys.items() if key in truth}
    scoped_owners={rid:owner for rid,owner in owners.items() if rid in in_scope}
    review=review_metrics(in_scope,scoped_owners,candidates,outcomes)
    cluster=scoped_cluster_metrics(in_scope,set(by_id),scoped_owners,groups)
    gate=automation_readiness(cluster['correct_multi_record_components'],cluster['labelled_multi_record_components'],
        cluster['attempted_multi_record_components'],scope='synthetic-benchmark',independent_holdout=False,
        representative=False,precision_target=target,constraint_violations=violations)
    return dict(case=bundle.name,assessment_version=VERSION,records_available=available,records_loaded=len(records),
        source_code_identity=source_code_identity(Path(matching.__file__).resolve().parents[3]),
        evaluation_code_hash=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        assessment_profile=assessment_profile(),cluster_policy=asdict(policy),
        in_scope_records=len(in_scope),certain_labelled_records=len(owners),source_parsing=diagnostics,
        partial_sample=len(records)<available,scope='PERSON/UNKNOWN within loaded raw rows',
        sampling='smallest SHA256(raw source identity); labels not used',truth_used_during_compute=False,
        validation='exploratory; inspected synthetic data; no independent holdout',
        raw_record_hash=hashlib.sha256(json.dumps(records,sort_keys=True).encode()).hexdigest(),
        verified_source_hashes=integrity,candidates=len(candidates),candidate_hash=candidate_hash.hexdigest(),
        evidence_hash=evidence_hash.hexdigest(),decisions=dict(decisions),unsupported_recovery=unsupported_recovery,
        mandatory_review_all_scope=sum(o['required'] for o in outcomes),review=review,
        shadow=dict(cluster=cluster,constraint_violations=violations,guard_rejections=dict(guarded_rejections)),
        automation_readiness=gate)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--datasets',type=Path,required=True)
    parser.add_argument('--source-tree',type=Path,default=Path(__file__).resolve().parents[1]/'backend/src')
    parser.add_argument('--max-records',type=int,default=12000)
    parser.add_argument('--precision-target',type=float,default=.995)
    parser.add_argument('--only',nargs='*')
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.max_records<=0 or not math.isfinite(args.precision_target) or not 0<args.precision_target<1:
        parser.error('Positive record cap and finite precision target between 0 and 1 required')
    sys.path[:0]=[str(args.source_tree.resolve()),str(Path(__file__).resolve().parent)]
    os.environ.update(SEMANTIC_ADDRESS_ENABLED='false',LLM_ENABLED='false',MDM_SCORE_WORKERS='1')
    reports=[]
    for bundle in sorted(args.datasets.iterdir()):
        if not bundle.is_dir() or not bundle.name.startswith('TC') or args.only and bundle.name not in args.only:
            continue
        try:
            result=verify(bundle,args.max_records,args.precision_target)
        except Exception as error:
            result=dict(case=bundle.name,error_type=type(error).__name__,error=str(error))
        reports.append(result)
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(reports,indent=2),encoding='utf-8')
        print(json.dumps({k:v for k,v in result.items() if k!='verified_source_hashes'}),flush=True)
    return int(not reports or any('error_type' in r for r in reports))


if __name__=='__main__':
    raise SystemExit(main())
