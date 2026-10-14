"""Bounded, certified component automation; recovered evidence remains manual."""
from collections import Counter
from contextlib import closing
from copy import deepcopy
from datetime import datetime,timezone
from hashlib import sha256
from itertools import combinations
import json
import math
from pathlib import Path
from statistics import NormalDist

from masterone.mdm.generation_staging import digest

VERSION='controlled-pair-automation-v1'
PRECISION_TARGET=.995


def code_identity():
    root=Path(__file__).resolve().parents[3]
    files=[p for p in (root/'masterone/mdm').rglob('*') if p.suffix in {'.py','.yaml','.yml'}]
    files += [root/'masterone/config.py',root/'masterone/auth/security.py',root/'masterone/api/mdm_workflow.py']
    return digest({p.relative_to(root).as_posix():sha256(p.read_bytes()).hexdigest() for p in sorted(files)})


def validate_certificate(certificate, *, code_identity,profile_identity,sources,owner_user_id,data_scope,now=None):
    from masterone.mdm.pipeline.pair_assessment import VERSION as assessment_version
    now=now or datetime.now(timezone.utc)
    reasons=[];bounds={}
    def reject(code):
        if code not in reasons:reasons.append(code)
    if not isinstance(certificate,dict):
        return dict(eligible=False,reasons=['INVALID_CERTIFICATE'],cohorts={},precision_target=PRECISION_TARGET)
    c=certificate
    if c.get('version')!=VERSION or c.get('assessment_version')!=assessment_version:reject('POLICY_CHANGED')
    if c.get('scope')!='production':reject('NON_PRODUCTION_VALIDATION')
    if c.get('independent_holdout') is not True:reject('NO_INDEPENDENT_HOLDOUT')
    if c.get('representative') is not True:reject('NON_REPRESENTATIVE_LABELS')
    if c.get('precision_target')!=PRECISION_TARGET:reject('PRECISION_TARGET_MISMATCH')
    if c.get('code_identity')!=code_identity:reject('CODE_CHANGED')
    if c.get('profile_identity')!=profile_identity:reject('PROFILE_CHANGED')
    if not owner_user_id or c.get('owner_user_id')!=owner_user_id:reject('OWNER_SCOPE_MISMATCH')
    if not data_scope or c.get('data_scope')!=data_scope:reject('DATA_SCOPE_MISMATCH')
    validation_hash=c.get('validation_dataset_hash')
    if not isinstance(validation_hash,str) or len(validation_hash)!=64 or any(x not in '0123456789abcdef' for x in validation_hash):reject('INVALID_VALIDATION_IDENTITY')
    try:
        expires=datetime.fromisoformat(c['expires_at'].replace('Z','+00:00'))
        if expires.tzinfo is None:reject('INVALID_CERTIFICATE_EXPIRY')
        elif expires<=now:reject('CERTIFICATE_EXPIRED')
    except (KeyError,ValueError,TypeError,AttributeError):reject('INVALID_CERTIFICATE_EXPIRY')
    declared=c.get('source_systems')
    if (not isinstance(declared,list) or not declared or len(declared)>50
            or any(not isinstance(s,str) or not s.strip() for s in declared)
            or len(set(declared))!=len(declared)):
        reject('INVALID_SOURCE_SCOPE')
    elif not sources<=set(declared):reject('SOURCE_SCOPE_MISMATCH')
    cohorts=c.get('cohorts')
    if not isinstance(cohorts,dict) or not cohorts or len(cohorts)>100:
        reject('NO_CERTIFIED_COHORTS');cohorts={}
    # Family-wise two-sided95% confidence over the declared cohort set.
    z=NormalDist().inv_cdf(1-.025/max(1,len(cohorts)))
    for key,counts in cohorts.items():
        if not isinstance(key,str) or len(key)!=64 or not isinstance(counts,dict):
            reject('INVALID_COHORT_EVIDENCE');continue
        nums=[counts.get(field) for field in ('correct','labelled','attempted','constraint_violations')]
        if any(type(n) is not int or n<0 for n in nums):reject('INVALID_COHORT_EVIDENCE');continue
        correct,labelled,attempted,violations=nums
        if not 0<=correct<=labelled<=attempted:reject('INVALID_COHORT_EVIDENCE');continue
        if not attempted or labelled!=attempted:reject('INCOMPLETE_LABEL_COVERAGE')
        if violations:reject('CONSTRAINT_VIOLATION')
        lower=None
        if labelled:
            p=correct/labelled;scale=1+z*z/labelled
            lower=max(0,(p+z*z/(2*labelled)-z*math.sqrt(p*(1-p)/labelled+z*z/(4*labelled*labelled)))/scale)
        bounds[key]=dict(precision_lower_bound=lower,attempted=attempted,labelled=labelled,correct=correct)
        if lower is None or lower<PRECISION_TARGET:reject('PRECISION_BOUND_BELOW_TARGET')
    return dict(eligible=not reasons,reasons=reasons,cohorts=bounds,precision_target=PRECISION_TARGET,
                confidence='Wilson95% two-sided, Bonferroni across certified component cohorts')


def load_certificate(settings,profile,sources,owner_user_id):
    if not settings.pair_automation_certificate_path or not settings.pair_automation_certificate_sha256:
        return None,dict(eligible=False,reasons=['NO_CERTIFICATE'],cohorts={},precision_target=PRECISION_TARGET)
    try:
        path=Path(settings.pair_automation_certificate_path)
        if path.stat().st_size>1024*1024:raise ValueError('Certificate too large')
        raw=path.read_bytes();identity=sha256(raw).hexdigest()
        if identity!=settings.pair_automation_certificate_sha256:
            return None,dict(eligible=False,reasons=['CERTIFICATE_PIN_MISMATCH'],cohorts={},precision_target=PRECISION_TARGET)
        certificate=json.loads(raw)
        gate=validate_certificate(certificate,code_identity=code_identity(),profile_identity=digest(profile),sources=sources,
            owner_user_id=owner_user_id,data_scope=settings.pair_automation_data_scope)
        gate['certificate_hash']=identity
        return certificate,gate
    except (OSError,ValueError,TypeError):
        return None,dict(eligible=False,reasons=['INVALID_CERTIFICATE'],cohorts={},precision_target=PRECISION_TARGET)


def component_cohort(pairs,by_id):
    members={rid for p in pairs for rid in (p['left_id'],p['right_id'])}
    return digest({'record_count':len(members),
                   'patterns':sorted({(p.get('signature'),p.get('decision_subgroup'),p.get('subgroup')) for p in pairs}),
                   'sources':sorted({by_id[rid]['source_system'] for p in pairs for rid in (p['left_id'],p['right_id'])})})


def strict_candidate(pair,overlay):
    from masterone.mdm.pipeline.pair_assessment import VERSION as assessment_version
    return (overlay=='UNDECIDED' and pair.get('assessment_version')==assessment_version
        and pair.get('decision')==pair.get('rule_conclusion')=='MERGEABLE' and pair.get('primary_group')=='A'
        and not pair.get('review_support') and not pair.get('constraint_codes')
        and pair.get('unverified_identifier_relation')!='DIFFERENT_VALUE'
        and not any(v.get('state') in {'CF','SH','IV'} for v in pair.get('comparisons',{}).values()))


def _pair_key(a,b):
    return tuple(sorted((a,b)))


def plan_components(snapshot,limit=20,certified_cohorts=None):
    from masterone.mdm.engine_adapter import engine_context
    from masterone.mdm.pipeline.clustering import build_proposals
    from masterone.mdm.pipeline.decision_store import DecisionStore
    from masterone.mdm.pipeline.matching import value_frequencies
    from masterone.mdm.pipeline.pair_assessment import assess_prepared_pair
    by_id={r['id']:r for r in snapshot['records']}
    if len(by_id)!=len(snapshot['records']):raise ValueError('Duplicate automation record')
    overlays=snapshot['overlays'];eligible={};all_pairs=deepcopy(snapshot['pairs'])
    if len({_pair_key(p['left_id'],p['right_id']) for p in all_pairs})!=len(all_pairs):raise ValueError('Duplicate automation pair')
    frequencies=value_frequencies(snapshot['records']);rejected=Counter()
    links=frozenset(tuple(sorted(p)) for p in snapshot['negative_pairs'])
    vetoes=frozenset(_pair_key(p['left_id'],p['right_id']) for p in all_pairs if overlays.get(p['id']) in {'REJECTED','NON_MATCH','DEFERRED'})
    with engine_context(snapshot['profile']),closing(DecisionStore()) as store:
        for pair in all_pairs:
            key=_pair_key(pair['left_id'],pair['right_id'])
            if strict_candidate(pair,overlays.get(pair['id'],'UNDECIDED')) and key not in links|vetoes:
                fresh=assess_prepared_pair(by_id[pair['left_id']],by_id[pair['right_id']],frequencies,links)
                fields=('decision','rule_conclusion','primary_group','subgroup','signature','score','match_weight','comparisons')
                if strict_candidate(fresh,'UNDECIDED') and all(fresh.get(k)==pair.get(k) for k in fields):eligible[key]=pair
            if key not in eligible:pair['decision']='REVIEW' if pair['decision']=='MERGEABLE' else pair['decision']
            store.add(pair)
        store.seal()
        result=build_proposals(by_id,store,'automation-shadow',VERSION,negative_pairs=links,execution_blocked_pairs=vetoes)
        result['risky_pairs']=list(result['risky_pairs'])
        result['proposals']=list(result['proposals'])
    components=[]
    for group in result['proposals']:
        ids=group['member_record_ids'];keys=[_pair_key(a,b) for a,b in combinations(ids,2)]
        if len(ids)>20 or len(keys)>190 or not all(key in eligible for key in keys):
            rejected['INCOMPLETE_DIRECT_EVIDENCE']+=1;continue
        pairs=[eligible[key] for key in keys]
        item=dict(record_ids=sorted(ids),pair_ids=sorted(p['id'] for p in pairs),cohort=component_cohort(pairs,by_id))
        item['fingerprint']=digest(item);components.append(item)
    components.sort(key=lambda c:c['record_ids'])
    selected=components if certified_cohorts is None else [c for c in components if c['cohort'] in certified_cohorts]
    return dict(components=selected[:max(0,limit)],eligible_components=len(components),
                eligible_pairs=len(eligible),guard_rejections=dict(Counter(p.get('triage_reason','UNKNOWN') for p in result['risky_pairs'])),
                skipped_components=dict(rejected),truncated=len(selected)>limit)
