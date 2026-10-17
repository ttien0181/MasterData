"""Read-only checks on named finalized runs; export failure remains a separate failure."""
import argparse
import json
import subprocess

from qa_api_multiagent import ROOT, save


def audit_python(code):
    # Heavy read-only diagnostics belong in the existing worker service, never
    # alongside the 1 GiB HTTP server. Do not create another container/service.
    result = subprocess.run(['docker', 'compose', 'exec', '-T', 'worker', 'python', '-'], input=code,
        capture_output=True, text=True, encoding='utf8', cwd=ROOT, check=True)
    return json.loads(result.stdout)


def main():
    from pathlib import Path
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--allow-failed-export', action='store_true',
                        help='Audit a finalized workflow even when the later CSV transport failed; does not change its FAIL status')
    args = parser.parse_args()
    reports = {p.parent.name: json.loads(p.read_text(encoding='utf8')) for p in args.input.glob('TC*/report.json')}
    assert reports and all(r['status'] == 'PASS' or (
        args.allow_failed_export and r.get('status') == 'FAIL'
        and r.get('stages', {}).get('golden-confirm-finalize', {}).get('complete')
        and r['stages']['golden-confirm-finalize']['result']['workflow_state'] == 'FINALIZED'
        and 'csv-export' in r['stages'] and not r['stages']['csv-export'].get('complete'))
        for r in reports.values())
    ids = {name: report['run_id'] for name, report in reports.items()}
    result = audit_python('''
import json,resource
from sqlalchemy import create_engine, select, func, text, exists
from sqlalchemy.orm import Session, aliased, defer
from masterone.config import get_settings
from masterone.mdm.models import ResolutionRun, ResolutionRecord, ResolutionJobAttempt, ResolutionNegativeEdge, ResolutionAuditEvent
from masterone.mdm.workflow_models import WorkflowGeneration, WorkflowMember, WorkflowCluster, WorkflowPair, WorkflowConflict
from masterone.mdm.matching_foundation_models import StewardFeedback
from masterone.mdm.workflow_review import eligibility
from masterone.mdm.workflow_generation_reads import GenerationReader, virtual_cluster_id
with Session(create_engine(get_settings().active_mdm_database_url)) as db:
 db.execute(text('SET TRANSACTION READ ONLY'))
 result={}
 for name, rid in RUN_IDS.items():
  run=db.get(ResolutionRun,rid)
  generation=db.scalars(select(WorkflowGeneration).options(defer(WorkflowGeneration.finalized_result)).where(
   WorkflowGeneration.run_id==rid).order_by(WorkflowGeneration.revision.desc()).limit(1)).first()
  reader=GenerationReader(db,run,generation)
  records=db.scalar(select(func.count()).select_from(ResolutionRecord).where(ResolutionRecord.run_id==rid))
  stored=list(db.execute(select(WorkflowMember.record_id,WorkflowMember.cluster_id).where(WorkflowMember.generation_id==generation.id)))
  membership=dict(stored)
  actual_ids=set(db.scalars(select(ResolutionRecord.id).where(ResolutionRecord.run_id==rid)))
  frozen_ids=set()
  frozen_count=0
  if reader.virtual:
   for record in reader.records():
    frozen_ids.add(record['id'])
    frozen_count+=1
  else:
   frozen_ids=actual_ids
  if reader.virtual:
   for record_id in frozen_ids-membership.keys():
    membership[record_id]=virtual_cluster_id(generation.id,record_id)
  members=len(stored)+len(frozen_ids-{r[0] for r in stored}) if reader.virtual else len(stored)
  distinct=len(membership)
  a,b=aliased(WorkflowMember),aliased(WorkflowMember)
  negative_violations=db.scalar(select(func.count()).select_from(ResolutionNegativeEdge).join(a,
   (a.record_id==ResolutionNegativeEdge.left_record_id)&(a.generation_id==generation.id)).join(b,
   (b.record_id==ResolutionNegativeEdge.right_record_id)&(b.generation_id==generation.id)&(a.cluster_id==b.cluster_id)).where(
    ResolutionNegativeEdge.run_id==rid,ResolutionNegativeEdge.active.is_(True)))
  invalid_members=db.scalar(select(func.count()).select_from(WorkflowMember).join(ResolutionRecord,
   ResolutionRecord.id==WorkflowMember.record_id).where(WorkflowMember.generation_id==generation.id,ResolutionRecord.run_id!=rid))
  unconfirmed=reader.unconfirmed_count()
  pending=db.scalar(select(func.count()).select_from(WorkflowPair).where(WorkflowPair.run_id==rid,WorkflowPair.decision.in_(('UNDECIDED','DEFERRED'))))
  conflicts=db.scalar(select(func.count()).select_from(WorkflowConflict).where(WorkflowConflict.run_id==rid,WorkflowConflict.resolved.is_(False)))
  blocked_accepted=0
  accepted=0
  for pair in db.scalars(select(WorkflowPair).where(WorkflowPair.run_id==rid,WorkflowPair.decision=='ACCEPTED').execution_options(yield_per=128)):
   accepted+=1
   blocked_accepted+=not eligibility(pair)['manual_allowed']
  attempts=[{'id':a.id,'status':a.status,'error_code':a.error_code,'started_at':a.started_at.isoformat(),
   'finished_at':a.finished_at.isoformat() if a.finished_at else None} for a in db.scalars(select(ResolutionJobAttempt).where(ResolutionJobAttempt.run_id==rid))]
  feedback=db.scalar(select(func.count()).select_from(StewardFeedback).where(StewardFeedback.run_id==rid))
  audits=db.scalar(select(func.count()).select_from(ResolutionAuditEvent).where(ResolutionAuditEvent.run_id==rid))
  feedback_exists=exists(select(StewardFeedback.id).where(StewardFeedback.run_id==rid,
   StewardFeedback.workflow_pair_id==WorkflowPair.id,StewardFeedback.pair_revision==WorkflowPair.revision))
  missing_feedback=db.scalar(select(func.count()).select_from(WorkflowPair).where(WorkflowPair.run_id==rid,
   WorkflowPair.decision.notin_(('UNDECIDED','DEFERRED')),~feedback_exists))
  pair_audits=db.scalar(select(func.count()).select_from(ResolutionAuditEvent).where(ResolutionAuditEvent.run_id==rid,
   ResolutionAuditEvent.action.in_(('WORKFLOW_PAIR_ACCEPTED','WORKFLOW_PAIR_REJECTED','WORKFLOW_PAIR_NON_MATCH','WORKFLOW_PAIR_DEFERRED'))))
  pair_count=db.scalar(select(func.count()).select_from(WorkflowPair).where(WorkflowPair.run_id==rid))
  checks={'finalized':run.workflow_state=='FINALIZED','membership_exact':members==distinct==records and set(membership)==actual_ids,
   'frozen_source_exact':frozen_ids==actual_ids and (not reader.virtual or len(frozen_ids)==frozen_count),
   'no_foreign_members':invalid_members==0,'no_negative_link_violation':negative_violations==0,
   'accepted_pairs_eligible':blocked_accepted==0,'golden_confirmed':unconfirmed==0,
   'no_pending_pairs':pending==0,'no_unresolved_conflicts':conflicts==0,'audit_present':audits>0,
   'feedback_present':feedback>0,'latest_pair_feedback_complete':missing_feedback==0,
   'pair_audit_count_preserved':pair_audits>=pair_count-pending,
   'no_failed_attempts':all(a['status']=='SUCCEEDED' for a in attempts)}
  result[name]={'run_id':rid,'checks':checks,'records':records,'members':members,'accepted_pairs':accepted,
   'blocked_accepted':blocked_accepted,'negative_link_violations':negative_violations,'unresolved_conflicts':conflicts,
   'audit_events':audits,'pair_audit_events':pair_audits,'feedback_rows':feedback,'missing_latest_feedback':missing_feedback,'attempts':attempts,
   'diagnostic_service':'worker','probe_peak_rss_bytes':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024}
 print(json.dumps(result))
'''.replace('RUN_IDS', repr(ids)))
    save(args.input / 'final-invariants.json', result)
    ok = all(all(case['checks'].values()) for case in result.values())
    print(json.dumps({'all_postchecks_pass': ok, 'checks': {name: case['checks'] for name, case in result.items()}}))
    raise SystemExit(0 if ok else 1)


if __name__ == '__main__':
    main()
