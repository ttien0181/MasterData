"""Full source-only TC1/TC3 HTTP QA; truth is used only after model processing.

Run against the existing local QA Compose stack. Durable stage receipts support
resuming a long run; no business decisions are written directly to the database.
"""
import argparse
import csv
import hashlib
import io
import json
import math
import secrets
import statistics
import time
from collections import Counter, defaultdict
from itertools import combinations
from pathlib import Path
from uuid import uuid4

import httpx
import pyarrow.parquet as pq
import yaml

from qa_api_multiagent import ROOT, FIELDS, checked, docker_python, metrics, provision, save, sha


def fixture(dataset, out):
    manifest = yaml.safe_load((dataset/'manifest.yaml').read_text(encoding='utf8'))
    for relative, digest in manifest['file_hashes'].items():
        path = (dataset/relative).resolve()
        assert path.is_relative_to(dataset.resolve()) and sha(path) == digest
    mappings = yaml.safe_load((dataset/'raw/source_mapping.yaml').read_text(encoding='utf8'))['files']
    rows = {}
    for mapping in mappings:
        with (dataset/mapping['path']).open(encoding=mapping['encoding'], newline='') as handle:
            for original in csv.DictReader(handle, delimiter=mapping['delimiter']):
                row = {key: original.get(mapping['columns'].get(key, key), '') for key in FIELDS}
                key = f"{row['source_system']}:{row['source_record_id']}:{mapping['batch_id']}"
                assert key not in rows
                rows[key] = row
    truth = {r['record_key']: r for r in pq.read_table(dataset/'truth/records.parquet').to_pylist()}
    assert rows.keys() == truth.keys() and all(r['label_status']=='CERTAIN' for r in truth.values())
    external = { (r['source_system'],r['source_record_id']):key for key,r in rows.items() }
    assert len(external)==len(rows)
    groups = defaultdict(list)
    for key,r in truth.items():
        groups[r['entity_id']].append(key)
    positive = {tuple(sorted(pair)) for keys in groups.values() for pair in combinations(keys,2)}
    source = out/'source-only.csv'
    with source.open('w',encoding='utf8',newline='') as handle:
        writer=csv.DictWriter(handle,FIELDS,lineterminator='\n')
        writer.writeheader()
        writer.writerows(rows[key] for key in sorted(rows))
    receipt={'dataset':dataset.relative_to(ROOT).as_posix(),'manifest_sha256':sha(dataset/'manifest.yaml'),
        'verified_hashes':len(manifest['file_hashes']),'source_sha256':sha(source),'records':len(rows),
        'types':dict(Counter(r['entity_type'] for r in truth.values())), 'entities':len(groups),
        'truth_positive_pairs':len(positive),'truth_kind':'SYNTHETIC_GENERATOR',
        'selection':'ALL raw rows, all entity types; canonical source mapping only; no truth columns uploaded'}
    save(out/'fixture.json',receipt)
    return source,receipt,truth,positive,external


def projection(run, final=False):
    # Read-only scalar projection: avoid transferring raw pair evidence for 200k+
    # pairs. The evaluator below receives truth only AFTER the model run finishes.
    code='''
import json
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, defer
from masterone.config import get_settings
from masterone.mdm.models import ResolutionRecord, ResolutionRun
from masterone.mdm.workflow_models import WorkflowPair, WorkflowGeneration, WorkflowMember
from masterone.mdm.workflow_assessment_reads import effective_column
from masterone.mdm.workflow_review import eligibility
s=get_settings()
with Session(create_engine(s.active_mdm_database_url)) as db:
    db.execute(__import__('sqlalchemy').text('SET TRANSACTION READ ONLY'))
    records=[list(r) for r in db.execute(select(ResolutionRecord.id,ResolutionRecord.source_system,ResolutionRecord.external_id,ResolutionRecord.entity_type).where(ResolutionRecord.run_id==RUN))]
    e=effective_column()
    pairs=[list(r) for r in db.execute(select(WorkflowPair.id,WorkflowPair.baseline['left_id'].as_string(),WorkflowPair.baseline['right_id'].as_string(),e['primary_group'].as_string(),e['rule_conclusion'].as_string(),e['soft_evaluation'],WorkflowPair.decision,e['decision'].as_string(),WorkflowPair.advice).where(WorkflowPair.run_id==RUN))] if not FINAL else []
    generation=db.query(WorkflowGeneration).options(defer(WorkflowGeneration.finalized_result)).filter_by(run_id=RUN).order_by(WorkflowGeneration.revision.desc()).first() if FINAL else None
    members=[list(r) for r in db.execute(select(WorkflowMember.cluster_id,WorkflowMember.record_id).where(WorkflowMember.generation_id==generation.id))] if FINAL and generation else []
    if FINAL and generation:
        from masterone.mdm.workflow_generation_reads import GenerationReader,virtual_cluster_id
        reader=GenerationReader(db,db.get(ResolutionRun,RUN),generation)
        if reader.virtual:
            covered={rid for cid,rid in members}
            frozen_ids=set()
            for record in reader.records():
                frozen_ids.add(record['id'])
            assert frozen_ids=={r[0] for r in records}, 'FROZEN_SOURCE_COVERAGE'
            members.extend([virtual_cluster_id(generation.id,rid),rid] for rid in sorted(frozen_ids-covered))
    print(json.dumps(dict(records=records,pairs=pairs,members=members)))
'''.replace('RUN',repr(run)).replace('FINAL',repr(final))
    return docker_python(code, service='worker')


def evaluate(run, out, truth, positives, external):
    data=projection(run)
    keys={r[0]:external[(r[1],r[2])] for r in data['records']}
    assert len(keys)==len(truth)
    predicted=defaultdict(set, {name:set() for name in
        ('candidates','rule_A','rule_mergeable','splink','gemini_selected')})
    groups=Counter()
    status=Counter()
    true_candidates=[]
    samples=[]
    brier=[]
    advised=Counter()
    rule_decisions=Counter()
    for pid,left,right,group,rule,soft,decision,conclusion,advice in data['pairs']:
        key=tuple(sorted((keys[left],keys[right])))
        predicted['candidates'].add(key)
        groups[group]+=1
        rule_decisions[rule or conclusion]+=1
        status[(soft or {}).get('status')]+=1
        if group=='A': predicted['rule_A'].add(key)
        if rule=='MERGEABLE': predicted['rule_mergeable'].add(key)
        if (soft or {}).get('recommendation')=='LIKELY_SAME_PERSON': predicted['splink'].add(key)
        if (advice or {}).get('recommendation')=='LIKELY_SAME_PERSON': predicted['gemini_selected'].add(key)
        if (advice or {}).get('status')=='COMPLETED':
            recommendation=advice.get('recommendation')
            advised['completed']+=1
            advised['truth_match' if key in positives else 'truth_non_match']+=1
            if recommendation in ('LIKELY_SAME_PERSON','LIKELY_DIFFERENT_PERSON'):
                advised['classified']+=1
                advised['correct']+=int((recommendation=='LIKELY_SAME_PERSON')==(key in positives))
        probability=(soft or {}).get('calibrated_probability')
        if probability is not None: brier.append((float(probability)-int(key in positives))**2)
        if key in positives:
            true_candidates.append({'id':pid,'group':group,'members':[left,right]})
        samples.append((hashlib.sha256('|'.join(key).encode()).hexdigest(),pid))
    # Resolve actual permissions only for known true candidates, in a read-only
    # post-hoc query. No truth is inserted into the application or scorer inputs.
    permissions=docker_python('''
import json
from sqlalchemy import create_engine,text
from sqlalchemy.orm import Session
from masterone.config import get_settings
from masterone.mdm.workflow_models import WorkflowPair
from masterone.mdm.workflow_review import eligibility
with Session(create_engine(get_settings().active_mdm_database_url)) as db:
    db.execute(text('SET TRANSACTION READ ONLY'))
    print(json.dumps({p.id:eligibility(p) for p in db.query(WorkflowPair).filter(WorkflowPair.run_id==RUN,WorkflowPair.id.in_(IDS))}))
'''.replace('RUN',repr(run)).replace('IDS',repr([p['id'] for p in true_candidates])))
    for pair in true_candidates: pair['eligibility']=permissions[pair['id']]
    c=len(predicted['candidates'])
    report={'candidate_count':c,'candidate_recall':len(predicted['candidates']&positives)/len(positives),
        'candidate_reduction_ratio':1-c/(len(truth)*(len(truth)-1)/2), 'groups':dict(groups),
        'splink_status':dict(status),'splink_brier_on_candidates':statistics.mean(brier) if brier else None,
        'rule_decisions':dict(rule_decisions), 'gemini_selected_accuracy':{
            **dict(advised),'accuracy_when_classified':advised['correct']/advised['classified'] if advised['classified'] else None},
        'scores':{name:metrics(pairs,positives) for name,pairs in predicted.items() if name!='candidates'},
        'true_candidates':len(true_candidates),
        'true_candidates_merge_blocked':sum(not p['eligibility']['manual_allowed'] for p in true_candidates),
        'by_entity_type':{kind:{'truth_pairs':len(gold),'candidate_recall':len(predicted['candidates']&gold)/len(gold) if gold else None,
            'rule_A':metrics({p for p in predicted['rule_A'] if truth[p[0]]['entity_type']==kind},gold),
            'splink':metrics({p for p in predicted['splink'] if truth[p[0]]['entity_type']==kind},gold)}
            for kind in sorted({r['entity_type'] for r in truth.values()})
            for gold in [{p for p in positives if truth[p[0]]['entity_type']==kind}]},
        'notes':['Metrics against synthetic generator truth; not independent human labels.',
                 'Recommendation sets do not authorize merges. Splink recall includes missing candidates.',
                 'Gemini is selected, so its recall is not a standalone model evaluation.']}
    result={'quality':report,'true_candidates':true_candidates,'explanation_samples':[pid for _,pid in sorted(samples)[:10]]}
    save(out/'evaluation.json',result)
    return result


def run_case(client,dataset,out, *, model_scope=None, advice_limit=20, explanation_samples=10):
    out.mkdir(exist_ok=True)
    source,receipt,truth,positive,external=fixture(dataset,out)
    path=out/'report.json'
    report=json.loads(path.read_text(encoding='utf8')) if path.exists() else {'status':'RUNNING','fixture':receipt,'stages':{}}
    if report.get('status')=='FAIL':
        report.setdefault('harness_interruptions',[]).append({'resume_epoch':time.time(),
            'seconds':max(0,time.time()-path.stat().st_mtime),'previous_error':report.get('error')})
        report['status']='RUNNING'
    def persist(): save(path,report)
    def stage(name,fn):
        if name in report['stages'] and report['stages'][name].get('complete'): return report['stages'][name]['result']
        entry=report['stages'].setdefault(name,{'started_epoch':time.time()})
        persist()
        print(dataset.parts[-3],name,'START',flush=True)
        result=fn()
        entry.update(complete=True,seconds=time.time()-entry['started_epoch'],finished_epoch=time.time(),result=result)
        persist()
        print(dataset.parts[-3],name,round(entry['seconds'],3),'DONE',flush=True)
        return result
    try:
        if 'run_id' not in report:
            t=time.time()
            run=checked(client.post('/api/v1/mdm/runs',data={'workflow_version':'2'},files={'files':('source-only.csv',source.read_bytes(),'text/csv')}))['run_id']
            report.update(run_id=run,upload_started_epoch=t,upload_seconds=time.time()-t)
            persist()
        workflow=f"/api/v1/mdm/runs/{report['run_id']}/workflow"
        def state(): return checked(client.get(workflow))
        def mutate(suffix,**extra):
            s=state()
            return checked(client.post(workflow+suffix,json={'request_id':str(uuid4()),'expected_workflow_revision':s['workflow_revision'],**extra}))
        def idle(expected):
            deadline=time.monotonic()+10800
            last=0
            while time.monotonic()<deadline:
                s=state()
                if s.get('retryable_operation'):
                    raise RuntimeError('WORKER_FAILED_'+str(s['retryable_operation']))
                if s['workflow_state']==expected and not s.get('active_operation'): return s
                if expected=='GOLDEN_REVIEW' and s['workflow_state']=='DECIDE_REVIEW' and not s.get('active_operation'):
                    raise RuntimeError('CLUSTER_REVIEW_REQUIRED')
                if time.monotonic()-last>30:
                    print(dataset.parts[-3],s['workflow_state'],str(s.get('active_operation'))[:700],flush=True)
                    last=time.monotonic()
                time.sleep(3)
            raise TimeoutError(expected)
        stage('prepare',lambda:idle('DATA_READY'))
        def process():
            s=state()
            if s['workflow_state']=='DATA_READY' and not s.get('active_operation'):
                mutate('/process',model_scope=model_scope,include_advice=True,advice_limit=advice_limit)
            return idle('DECIDE_REVIEW')
        stage('matching-advice-preview',process)
        def eval_stage():
            value=evaluate(report['run_id'],out,truth,positive,external)
            return value['quality']
        stage('posthoc-evaluation',eval_stage)
        evaluation=json.loads((out/'evaluation.json').read_text(encoding='utf8'))
        def explanations():
            calls=[]
            for pid in evaluation['explanation_samples'][:explanation_samples]:
                start=time.perf_counter()
                value=mutate(f'/pairs/{pid}/explanation')
                calls.append({'pair_id':pid,'seconds':time.perf_counter()-start,'response':value})
                save(out/'explanation-sample.json',calls)
            return {'measured_logical_requests':len(calls),'required_at_30_percent_all_candidates':math.ceil(.3*evaluation['quality']['candidate_count']),
                    'mean_seconds':statistics.mean(c['seconds'] for c in calls) if calls else None,
                    'sample_wall_seconds':sum(c['seconds'] for c in calls)}
        stage('gemini-explanation-sample',explanations)
        def replay():
            true=[p for p in evaluation['true_candidates'] if p['eligibility']['manual_allowed']]
            ordinary=[p['id'] for p in true if p['group']!='D']
            for start in range(0,len(ordinary),1000):
                mutate('/bulk-decisions',expected_decision_revision=state()['decision_revision'],decision='merge',scope={'pair_ids':ordinary[start:start+1000]},reason='Scripted synthetic truth QA review')
                idle('DECIDE_REVIEW')
            soft=[p for p in true if p['group']=='D']
            revisions=docker_python('''
import json
from sqlalchemy import create_engine,select,text
from sqlalchemy.orm import Session
from masterone.config import get_settings
from masterone.mdm.workflow_models import WorkflowPair
with Session(create_engine(get_settings().active_mdm_database_url)) as db:
    db.execute(text('SET TRANSACTION READ ONLY'))
    rows=db.execute(select(WorkflowPair.id,WorkflowPair.decision,WorkflowPair.revision).where(WorkflowPair.run_id==RUN,WorkflowPair.id.in_(IDS)))
    print(json.dumps({pid:[decision,revision] for pid,decision,revision in rows}))
'''.replace('RUN',repr(report['run_id'])).replace('IDS',repr([p['id'] for p in soft])))
            revision=state()['workflow_revision']
            for index,p in enumerate(soft):
                decision,pair_revision=revisions[p['id']]
                if decision=='UNDECIDED':
                    result=checked(client.post(workflow+f"/pairs/{p['id']}/decision",json={
                        'request_id':str(uuid4()),'expected_workflow_revision':revision,
                        'expected_pair_revision':pair_revision,'decision':'merge',
                        'reason':'Scripted synthetic truth QA review; explicit reason for D'}))
                    revision=result['workflow_revision']
                if index%50==0: print('D review',index,flush=True)
            mutate('/bulk-decisions',expected_decision_revision=state()['decision_revision'],decision='no_merge',scope={'filter':{}},reason='Scripted synthetic truth QA review; reject all remaining candidates')
            return idle('DECIDE_REVIEW')
        stage('scripted-truth-review',replay)
        def cluster():
            # Pair approval and exact component approval are separate actions in
            # the product. Replay only eligible, truth-correct missing-bridge
            # exceptions; never bypass a hard constraint or a negative decision.
            def groups():
                conflicts=docker_python('''
import json
from sqlalchemy import create_engine,select,text
from sqlalchemy.orm import Session
from masterone.config import get_settings
from masterone.mdm.workflow_models import WorkflowConflict
with Session(create_engine(get_settings().active_mdm_database_url)) as db:
    db.execute(text('SET TRANSACTION READ ONLY'))
    rows=db.scalars(select(WorkflowConflict.payload).where(WorkflowConflict.run_id==RUN,WorkflowConflict.resolved.is_(False)))
    print(json.dumps(list(rows)))
'''.replace('RUN',repr(report['run_id'])))
                eligible={tuple(sorted(p['members'])) for p in evaluation['true_candidates'] if p['eligibility']['manual_allowed']}
                for conflict in conflicts:
                    assert conflict['reason']=='CLUSTER_BRIDGE_MISSING_EVIDENCE'
                    assert tuple(sorted(conflict['record_ids'])) in eligible, 'NO_NON_TRUTH_GROUP_APPROVAL'
                s=state()
                workflow_revision,decision_revision=s['workflow_revision'],s['decision_revision']
                for index,conflict in enumerate(conflicts):
                    checked(client.post(workflow+'/group-approvals',json={
                        'request_id':str(uuid4()),'expected_workflow_revision':workflow_revision,
                        'expected_decision_revision':decision_revision,'record_ids':sorted(conflict['record_ids']),
                        'reason':'Explicit scripted synthetic QA truth group approval; existing reviewed exception flow'}))
                    workflow_revision+=1
                    decision_revision+=1
                    if index%10==0:
                        print('GROUP review',index+1,'/',len(conflicts),flush=True)
                        save(out/'group-review-progress.json',{'approved_this_attempt':index+1,'total_this_attempt':len(conflicts),'epoch':time.time()})
                return {'approved_this_attempt':len(conflicts),'reason':'CLUSTER_BRIDGE_MISSING_EVIDENCE'}
            if state()['workflow_state']=='DECIDE_REVIEW' and state().get('cluster_revision',0)>0:
                stage('scripted-group-review',groups)
            if state()['workflow_state']=='DECIDE_REVIEW':
                mutate('/finalize-decisions',expected_decision_revision=state()['decision_revision'])
            try:
                return idle('GOLDEN_REVIEW')
            except RuntimeError as error:
                if str(error)!='CLUSTER_REVIEW_REQUIRED': raise
                stage('scripted-group-review',groups)
                mutate('/finalize-decisions',expected_decision_revision=state()['decision_revision'])
                return idle('GOLDEN_REVIEW')
        stage('cluster-and-golden',cluster)
        report['upload_to_golden_seconds']=report['stages']['cluster-and-golden']['finished_epoch']-report['upload_started_epoch']
        def final_quality():
            data=projection(report['run_id'],True)
            keys={r[0]:external[(r[1],r[2])] for r in data['records']}
            members=defaultdict(list)
            for cid,rid in data['members']: members[cid].append(keys[rid])
            flat=[key for group in members.values() for key in group]
            assert len(flat)==len(set(flat))==len(truth),'MEMBERSHIP_INTEGRITY'
            pairs={tuple(sorted(p)) for group in members.values() for p in combinations(group,2)}
            return {'pairwise':metrics(pairs,positive),'clusters':len(members),'records_preserved':len(flat),
                    'overmerge_pairs':len(pairs-positive),'undermerge_pairs':len(positive-pairs),
                    'qualification':'AFTER SCRIPTED TRUTH REVIEW; not autonomous accuracy'}
        stage('golden-quality',final_quality)
        def confirm():
            s=state()
            if s['workflow_state']!='FINALIZED':
                mutate('/golden/confirm-selected',expected_cluster_revision=s['cluster_revision'],expected_golden_revision=s['golden_revision'])
                mutate('/finalize-golden',expected_golden_revision=state()['golden_revision'])
            return idle('FINALIZED')
        stage('golden-confirm-finalize',confirm)
        def export():
            base=f"/api/v1/mdm/runs/{report['run_id']}"
            preview=checked(client.post(base+'/previews',json={'choices':{},'acknowledge_unreviewed':False}))
            response=client.post(base+f"/previews/{preview['preview_id']}/execute",json={'execute_request_id':str(uuid4())})
            assert response.status_code==200,'EXPORT_FAILED'
            rows=list(csv.DictReader(io.StringIO(response.content.decode('utf-8-sig'))))
            assert sum(int(row['record_count']) for row in rows)==len(truth),'EXPORT_LOST_RECORDS'
            assert all(row['master_record_id'] for row in rows),'EXPORT_ID_MISSING'
            (out/'export.csv').write_bytes(response.content)
            return {'rows':len(rows),'records_preserved':len(truth),'sha256':sha(out/'export.csv')}
        stage('csv-export',export)
        report['status']='PASS'
    except Exception as error:
        report.update(status='FAIL',error={'type':type(error).__name__,'code':str(error)[:600]})
        raise
    finally:
        try: report['telemetry']=checked(client.get(workflow+'/telemetry'))
        except Exception: pass
        persist()
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--dataset',type=Path,action='append',required=True)
    parser.add_argument('--model-scope',choices=['none','synthetic-benchmark'],default='none')
    parser.add_argument('--advice-limit',type=int,choices=range(0,21),default=20)
    parser.add_argument('--explanation-samples',type=int,choices=range(0,11),default=10)
    args=parser.parse_args()
    out=args.output.resolve()
    assert out.is_relative_to((ROOT/'artifacts/full10k-20261016').resolve())
    out.mkdir(parents=True,exist_ok=True)
    private=out/'qa-access.private.json'
    with httpx.Client(base_url='http://localhost:8001',headers={'Origin':'http://localhost:3000'},timeout=600) as client:
        if private.exists():
            credentials=json.loads(private.read_text(encoding='utf8'))
        else:
            credentials={'email':f'api-multiagent-10k-{uuid4().hex[:10]}@example.com','password':secrets.token_urlsafe(24)}
            actor=checked(client.post('/api/v1/auth/register',json=credentials))
            provision(actor.get('id') or actor['user']['id'])
            save(private,credentials)
        client.headers['X-CSRF-Token']=checked(client.post('/api/v1/auth/login',json=credentials))['csrf_token']
        if not (out/'profile.json').exists():
            profile=checked(client.post('/api/v1/mdm/matching/profiles',json={'version':1,'advanced_agents':True,'fallback_mode':'fail_operation'}))
            checked(client.post(f"/api/v1/mdm/matching/profiles/{profile['id']}/validate",json={'expected_revision':1}))
            checked(client.put('/api/v1/mdm/matching/defaults/owner-private',json={'profile_id':profile['id'],'expected_revision':0}))
            save(out/'profile.json',profile)
        reports=[]
        for dataset in args.dataset:
            dataset=dataset.resolve()
            reports.append(run_case(client,dataset,out/dataset.parts[-3],
                model_scope=None if args.model_scope=='none' else args.model_scope,
                advice_limit=args.advice_limit,explanation_samples=args.explanation_samples))
            save(out/'summary.json',{'runs':reports})


if __name__=='__main__': main()
