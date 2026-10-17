from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from masterone.auth.security import CurrentSession, require_capability, require_mutation_capability
from masterone.db.session import get_session
from masterone.mdm.engine_adapter import FrontendJSONRoute, api_engine_context
from masterone.mdm.ownership import owned_run
from masterone.mdm.workflow_contracts import (
    AmendDecisionRequest,
    AssessmentRequest,
    AutomationApplyRequest,
    AutomationShadowRequest,
    AutoRequest,
    BulkRequest,
    ClusterScopeRequest,
    DecisionRequest,
    FinalizeGoldenRequest,
    FinalizeRequest,
    GoldenRequest,
    GoldenRevisionRequest,
    GroupAnnotationRequest,
    GroupApprovalRequest,
    Mutation,
    OperationRead,
    PairAdviceRequest,
    ProcessRequest,
    ReopenRequest,
    RepairRequest,
    ResolveRequest,
    WorkflowRead,
)
from masterone.mdm.workflow_models import WorkflowOperation
from masterone.mdm.workflow_service import begin_operation, get_workflow, operation_read

router = APIRouter(prefix='/mdm/runs/{run_id}/workflow', tags=['Session workflow'],
                   dependencies=[Depends(api_engine_context)], route_class=FrontendJSONRoute)


@router.get('', response_model=WorkflowRead)
@router.get('/', response_model=WorkflowRead, include_in_schema=False)
def read_workflow(run_id: str, db: Session = Depends(get_session), auth: CurrentSession = Depends(require_capability('read'))):
    result = get_workflow(db, run_id, auth.user.id, cache_reads=True)
    from masterone.auth.security import check_capability
    capabilities = {'start-decide': 'start_run', 'decide-pair': 'approve_pair', 'bulk-decisions': 'approve_pair',
                    'finalize-decisions': 'approve_pair', 'save-golden': 'edit_golden', 'auto-select': 'edit_golden',
                    'confirm-golden': 'edit_golden', 'finalize-golden': 'edit_golden',
                    'reopen-decisions': 'approve_pair', 'export': 'export_data', 'retry-operation': 'start_run',
                    'resolve-golden': 'edit_golden', 'reassess-pairs': 'approve_pair', 'assess-with-ai': 'approve_pair',
                    'queue-decisions': 'approve_pair','automation-shadow':'approve_pair',
                    'automation-apply':'approve_pair','automation-stop':'approve_pair'}
    capabilities.update({'annotate-groups': 'approve_pair', 'amend-pair': 'approve_pair', 'finalize-partial': 'approve_pair',
                         'build-cluster-queue':'approve_pair',
                         'approve-group': 'approve_pair', 'preview-clusters': 'approve_pair'})
    actions = []
    for action in result['allowed_actions']:
        try:
            check_capability(auth, capabilities[action])
            actions.append(action)
        except HTTPException:
            pass
    result['allowed_actions'] = actions
    if 'start-decide' in actions:
        try:
            check_capability(auth, 'approve_pair')
            result['allowed_actions'].append('process-data')
        except HTTPException:
            pass
    if result['workflow_state'] == 'DECIDE_REVIEW':
        try:
            check_capability(auth, 'approve_pair')
        except HTTPException:
            result['blockers'].append({'code': 'APPROVE_PERMISSION_REQUIRED'})
    return result


@router.get('/requests/{request_id}', response_model=OperationRead | None)
def read_request_receipt(run_id: str, request_id: str, db: Session = Depends(get_session),
                         auth: CurrentSession = Depends(require_capability('read'))):
    owned_run(db, run_id, auth.user.id)
    op = db.query(WorkflowOperation).filter_by(run_id=run_id, request_id=request_id).one_or_none()
    return operation_read(op) if op else None


@router.get('/telemetry')
def read_telemetry(run_id: str, db: Session = Depends(get_session),
                   auth: CurrentSession = Depends(require_capability('read'))):
    owned_run(db, run_id, auth.user.id)
    from masterone.mdm.workflow_telemetry import usage_report
    return usage_report(db, run_id)


@router.get('/operations/{operation_id}', response_model=OperationRead)
def read_operation(run_id: str, operation_id: str, db: Session = Depends(get_session),
                   auth: CurrentSession = Depends(require_capability('read'))):
    owned_run(db, run_id, auth.user.id)
    op = db.query(WorkflowOperation).filter_by(run_id=run_id, id=operation_id).one_or_none()
    if op is None:
        raise HTTPException(404, 'Operation not found')
    return operation_read(op)


@router.post('/operations/{operation_id}/retry', response_model=OperationRead, status_code=202)
def retry_operation(run_id: str, operation_id: str, body: Mutation, db: Session = Depends(get_session),
                    auth: CurrentSession = Depends(require_mutation_capability('start_run'))):
    from masterone.mdm.workflow_service import retry_operation as retry
    return retry(db, run_id, operation_id, body, auth.user.id)


@router.post('/start-decide', response_model=OperationRead, status_code=202)
def start_decide(run_id: str, body: Mutation, db: Session = Depends(get_session),
                 auth: CurrentSession = Depends(require_mutation_capability('start_run'))):
    return begin_operation(db, run_id, 'DECIDE', body.request_id, body.model_dump(), body.expected_workflow_revision, auth.user.id)


@router.post('/process', response_model=OperationRead, status_code=202)
def process_data(run_id: str, body: ProcessRequest, db: Session = Depends(get_session),
                 auth: CurrentSession = Depends(require_mutation_capability('start_run'))):
    from masterone.auth.security import check_capability
    from masterone.mdm.workflow_processing import begin_processing
    check_capability(auth, 'approve_pair')
    return begin_processing(db, run_id, body, auth.user.id)


@router.post('/reassess', response_model=OperationRead, status_code=202)
def reassess_pairs(run_id: str, body: AssessmentRequest, db: Session = Depends(get_session),
                   auth: CurrentSession = Depends(require_mutation_capability('approve_pair'))):
    return begin_operation(db, run_id, 'ASSESS_PAIRS', body.request_id, body.model_dump(),
                           body.expected_workflow_revision, auth.user.id)


@router.post('/automation/shadow',response_model=OperationRead,status_code=202)
def automation_shadow(run_id:str,body:AutomationShadowRequest,db:Session=Depends(get_session),
                      auth:CurrentSession=Depends(require_mutation_capability('approve_pair'))):
    return begin_operation(db,run_id,'AUTOMATION_SHADOW',body.request_id,body.model_dump(),body.expected_workflow_revision,auth.user.id)


@router.post('/automation/apply',response_model=OperationRead,status_code=202)
def automation_apply(run_id:str,body:AutomationApplyRequest,db:Session=Depends(get_session),
                     auth:CurrentSession=Depends(require_mutation_capability('approve_pair'))):
    from masterone.mdm.workflow_automation import begin_apply
    return begin_apply(db,run_id,body,auth.user.id)


@router.post('/automation/stop',response_model=OperationRead)
def automation_stop(run_id:str,body:Mutation,db:Session=Depends(get_session),
                    auth:CurrentSession=Depends(require_mutation_capability('approve_pair'))):
    from masterone.mdm.workflow_automation import stop_automation
    return stop_automation(db,run_id,body,auth.user.id)


@router.post('/pair-advice', response_model=OperationRead, status_code=202)
def assess_with_ai(run_id: str, body: PairAdviceRequest, db: Session = Depends(get_session),
                   auth: CurrentSession = Depends(require_mutation_capability('approve_pair'))):
    return begin_operation(db, run_id, 'PAIR_ADVICE', body.request_id, body.model_dump(),
                           body.expected_workflow_revision, auth.user.id)


@router.get('/data')
def read_data(run_id: str, limit: int = Query(10, ge=1, le=100), cursor: str | None = None,
              offset: int | None = Query(None, ge=0),
              include_profile: bool = True, include_records: bool = True,
              db: Session = Depends(get_session), auth: CurrentSession = Depends(require_capability('read'))):
    owned_run(db, run_id, auth.user.id)
    from masterone.mdm.data_profile import data_read
    return data_read(db, run_id, limit, cursor, offset, include_profile=include_profile, include_records=include_records)


@router.get('/pairs')
def read_pairs(run_id: str, limit: int = Query(10, ge=1, le=100), cursor: str | None = None,
               offset: int | None = Query(None, ge=0),
               group: str | None = None, subgroup: str | None = None, resolution: str | None = None,
               visibility: Literal['review'] | None = None,
               taxonomy: Literal['actions_v1'] | None = None,
               db: Session = Depends(get_session), auth: CurrentSession = Depends(require_capability('read'))):
    run = owned_run(db, run_id, auth.user.id)
    from masterone.mdm.pair_read_cache import cached_read
    from masterone.mdm.workflow_pair_reads import pair_page
    filters = dict(group=group, subgroup=subgroup, resolution=resolution, visibility=visibility,taxonomy=taxonomy)
    return cached_read(db, run, 'page', [limit, cursor, offset, filters],
                       lambda: pair_page(db, run_id, limit, cursor, offset, filters))


@router.get('/pairs/summary')
def read_pair_summary(run_id: str, visibility: Literal['review'] | None = None, taxonomy: Literal['actions_v1'] | None = None, db: Session = Depends(get_session),
                      auth: CurrentSession = Depends(require_capability('read'))):
    run = owned_run(db, run_id, auth.user.id)
    from masterone.mdm.pair_read_cache import cached_read
    from masterone.mdm.workflow_pair_reads import pair_summary
    filters = dict(visibility=visibility,taxonomy=taxonomy)
    return cached_read(db, run, 'summary', filters, lambda: pair_summary(db, run, filters))


@router.get('/pairs/{pair_id}/review-cluster')
def read_review_cluster(run_id: str, pair_id: str, db: Session = Depends(get_session),
                        auth: CurrentSession = Depends(require_capability('read'))):
    run = owned_run(db, run_id, auth.user.id)
    from masterone.mdm.workflow_review_clusters import review_cluster
    return review_cluster(db, run, pair_id)


@router.post('/bulk-decisions/preview')
def preview_bulk_decisions(run_id: str, body: BulkRequest, db: Session = Depends(get_session),
                           auth: CurrentSession = Depends(require_mutation_capability('approve_pair'))):
    from masterone.auth.security import check_capability
    from masterone.mdm.workflow_service import active_operation, check_revision, conflict, enabled_run
    check_capability(auth, 'approve_pair' if body.decision == 'merge' else 'reject_pair')
    run = enabled_run(db, run_id, auth.user.id)
    op = active_operation(db, run_id)
    # Read-only counts can evolve while a bulk job runs. Assessment identity must still match.
    check_revision(run, {'expected_assessment_id': body.expected_assessment_id})
    if run.workflow_state not in ('DECIDE_REVIEW', 'GOLDEN_REVIEW') or (op and op.operation_type != 'BULK_DECISIONS'):
        conflict('DECISIONS_LOCKED')
    from masterone.mdm.pair_read_cache import cached_read
    from masterone.mdm.workflow_pair_reads import bulk_preview
    params = body.model_dump(exclude={'request_id', 'preview_token', 'reason'})
    return cached_read(db, run, 'bulk-preview', params, lambda: bulk_preview(db, run, body))


@router.get('/pairs/{pair_id}')
def read_pair(run_id: str, pair_id: str, db: Session = Depends(get_session),
              auth: CurrentSession = Depends(require_capability('read'))):
    run = owned_run(db, run_id, auth.user.id)
    from masterone.mdm.workflow_pair_reads import pair_detail
    return pair_detail(db, run, pair_id)


@router.get('/conflicts')
def read_conflicts(run_id: str, limit: int = Query(10, ge=1, le=100), cursor: str | None = None,
                   db: Session = Depends(get_session), auth: CurrentSession = Depends(require_capability('read'))):
    run = owned_run(db, run_id, auth.user.id)
    from masterone.mdm.workflow_conflict_reads import conflict_page
    return conflict_page(db, run, limit, cursor)


@router.post('/pairs/{pair_id}/amend')
def amend_pair(run_id: str, pair_id: str, body: AmendDecisionRequest, db: Session = Depends(get_session),
               auth: CurrentSession = Depends(require_mutation_capability('approve_pair'))):
    from masterone.auth.security import check_capability
    check_capability(auth, 'reject_pair')
    from masterone.mdm.workflow_amendments import amend_decision
    return amend_decision(db, run_id, pair_id, body, auth.user.id)


@router.post('/pairs/{pair_id}/decision')
def review_pair(run_id: str, pair_id: str, body: DecisionRequest, db: Session = Depends(get_session),
                auth: CurrentSession = Depends(require_mutation_capability('approve_pair'))):
    from masterone.auth.security import check_capability
    check_capability(auth, 'approve_pair' if body.decision == 'merge' else 'reject_pair')
    from masterone.mdm.workflow_review import decide_pair
    return decide_pair(db, run_id, pair_id, body, auth.user.id)


@router.post('/bulk-decisions', response_model=OperationRead, status_code=202)
def bulk_decisions(run_id: str, body: BulkRequest, db: Session = Depends(get_session),
                   auth: CurrentSession = Depends(require_mutation_capability('approve_pair'))):
    from masterone.auth.security import check_capability
    check_capability(auth, 'approve_pair' if body.decision == 'merge' else 'reject_pair')
    owned_run(db, run_id, auth.user.id)
    from masterone.mdm.workflow_bulk import selected_pairs
    selected_pairs(db, run_id, body.scope.model_dump())
    payload = body.model_dump(exclude={key for key in
        ('preview_token', 'reason', 'review_group_token', 'review_group_record_ids') if getattr(body, key) is None})
    return begin_operation(db, run_id, 'BULK_DECISIONS', body.request_id, payload, body.expected_workflow_revision, auth.user.id)


@router.get('/cluster-queue')
def read_cluster_queue(run_id: str, db: Session = Depends(get_session),
                       auth: CurrentSession = Depends(require_capability('read'))):
    from masterone.mdm.workflow_cluster_queue import queue_summary
    return queue_summary(db,owned_run(db,run_id,auth.user.id))


@router.post('/cluster-queue',response_model=OperationRead,status_code=202)
def build_cluster_queue(run_id: str, body: FinalizeRequest, db: Session = Depends(get_session),
                        auth: CurrentSession = Depends(require_mutation_capability('approve_pair'))):
    if body.queue_mode is None:
        raise HTTPException(422,'queue_mode is required')
    from masterone.mdm.workflow_finalize import finalize_decisions as finalize
    return finalize(db,run_id,body,auth.user.id)


@router.post('/finalize-decisions', response_model=OperationRead, status_code=202)
def finalize_decisions(run_id: str, body: FinalizeRequest, db: Session = Depends(get_session),
                       auth: CurrentSession = Depends(require_mutation_capability('approve_pair'))):
    from masterone.mdm.workflow_finalize import finalize_decisions as finalize
    return finalize(db, run_id, body, auth.user.id)


@router.post('/cluster-preview', response_model=OperationRead, status_code=202)
def preview_clusters(run_id: str, body: FinalizeRequest, db: Session = Depends(get_session),
                     auth: CurrentSession = Depends(require_mutation_capability('approve_pair'))):
    return begin_operation(db, run_id, 'CLUSTER_PREVIEW', body.request_id, body.model_dump(),
                           body.expected_workflow_revision, auth.user.id)


@router.post('/group-approvals', response_model=OperationRead)
def approve_group(run_id: str, body: GroupApprovalRequest, db: Session = Depends(get_session),
                  auth: CurrentSession = Depends(require_mutation_capability('approve_pair'))):
    from masterone.mdm.workflow_group_approvals import approve_group as approve
    return approve(db, run_id, body, auth.user.id)


@router.get('/cluster-preview/{operation_id}/groups')
def read_preview_groups(run_id: str, operation_id: str, limit: int = Query(25, ge=1, le=100),
                        cursor: int = Query(0, ge=0), db: Session = Depends(get_session),
                        auth: CurrentSession = Depends(require_capability('read'))):
    run = owned_run(db, run_id, auth.user.id)
    from masterone.mdm.workflow_cluster_preview import preview_groups
    return preview_groups(db, run, operation_id, limit, cursor)


@router.get('/clusters')
def read_clusters(run_id: str, limit: int = Query(25, ge=1, le=100), cursor: str | None = None,
                  db: Session = Depends(get_session), auth: CurrentSession = Depends(require_capability('read'))):
    run = owned_run(db, run_id, auth.user.id)
    from masterone.mdm.workflow_cluster_reads import list_clusters
    return list_clusters(db, run, limit, cursor)


@router.get('/cluster-previews/{operation_id}/review-groups/{fingerprint}/{kind}')
def read_review_group_page(run_id: str, operation_id: str, fingerprint: str, kind: str,
                          limit: int = Query(25, ge=1, le=100), cursor: int = Query(0, ge=0),
                          db: Session = Depends(get_session),
                          auth: CurrentSession = Depends(require_capability('read'))):
    run = owned_run(db, run_id, auth.user.id)
    from masterone.mdm.workflow_cluster_preview import review_group_page
    return review_group_page(db, run, operation_id, fingerprint, kind, limit, cursor)


@router.post('/group-annotations', response_model=OperationRead, status_code=202)
def annotate_groups(run_id: str, body: GroupAnnotationRequest, db: Session = Depends(get_session),
                    auth: CurrentSession = Depends(require_mutation_capability('approve_pair'))):
    return begin_operation(db, run_id, 'GROUP_ANNOTATIONS', body.request_id, body.model_dump(),
                           body.expected_workflow_revision, auth.user.id)


@router.get('/unresolved-records')
def read_unresolved_records(run_id: str, limit: int = Query(25, ge=1, le=100), cursor: str | None = None,
                             db: Session = Depends(get_session), auth: CurrentSession = Depends(require_capability('read'))):
    run = owned_run(db, run_id, auth.user.id)
    from masterone.mdm.workflow_cluster_reads import unresolved_records
    return unresolved_records(db, run, limit, cursor)


@router.get('/clusters/{cluster_id}')
def read_cluster(run_id: str, cluster_id: str, limit: int = Query(25, ge=1, le=100), cursor: str | None = None,
                 evidence_limit: int = Query(25, ge=1, le=100), evidence_cursor: int = Query(0, ge=0),
                 db: Session = Depends(get_session), auth: CurrentSession = Depends(require_capability('read'))):
    run = owned_run(db, run_id, auth.user.id)
    from masterone.mdm.workflow_cluster_reads import cluster_detail
    return cluster_detail(db, run, cluster_id, limit, cursor, evidence_limit, evidence_cursor)


@router.get('/clusters/{cluster_id}/golden')
def read_cluster_golden(run_id: str, cluster_id: str, db: Session = Depends(get_session),
                        auth: CurrentSession = Depends(require_capability('read'))):
    run = owned_run(db, run_id, auth.user.id)
    from masterone.mdm.golden_service import golden_read
    from masterone.mdm.workflow_cluster_reads import cluster_row
    return golden_read(db, run, cluster_row(db, run, cluster_id, allow_stale=True))


@router.get('/golden')
def read_golden(run_id: str, limit: int = Query(25, ge=1, le=100), cursor: str | None = None,
                db: Session = Depends(get_session), auth: CurrentSession = Depends(require_capability('read'))):
    run = owned_run(db, run_id, auth.user.id)
    from masterone.mdm.golden_service import golden_read
    from masterone.mdm.workflow_cluster_reads import current_generation
    from masterone.mdm.workflow_generation_reads import GenerationReader
    generation = current_generation(db, run, allow_stale=True)
    from masterone.mdm.workflow_cluster_reads import generation_metadata
    metadata = generation_metadata(db, run, generation)
    reader = GenerationReader(db, run, generation)
    return {**reader.group_page(limit, cursor,
                    lambda c: golden_read(db, run, c, metadata=metadata, reader=reader)), 'generation': metadata}


@router.get('/golden/index')
def read_golden_index(run_id: str, limit: int = Query(25, ge=1, le=100), cursor: str | None = None,
                      status: Literal['all', 'review', 'confirmed'] = 'all', q: str = Query('', max_length=200),
                      db: Session = Depends(get_session), auth: CurrentSession = Depends(require_capability('read'))):
    run = owned_run(db, run_id, auth.user.id)
    from masterone.mdm.workflow_golden_index import golden_index
    return golden_index(db, run, limit, cursor, status, q)


@router.put('/clusters/{cluster_id}/golden')
def update_golden(run_id: str, cluster_id: str, body: GoldenRequest, db: Session = Depends(get_session),
                  auth: CurrentSession = Depends(require_mutation_capability('edit_golden'))):
    from masterone.mdm.golden_service import golden_command
    return golden_command(db, run_id, body, auth.user.id, 'SAVE_GOLDEN', cluster_id)


@router.post('/golden/auto-select')
def auto_golden(run_id: str, body: AutoRequest, db: Session = Depends(get_session),
                auth: CurrentSession = Depends(require_mutation_capability('edit_golden'))):
    from masterone.mdm.golden_service import golden_command
    return golden_command(db, run_id, body, auth.user.id, 'AUTO_GOLDEN')


@router.post('/clusters/{cluster_id}/golden/confirm')
def confirm_golden(run_id: str, cluster_id: str, body: GoldenRevisionRequest, db: Session = Depends(get_session),
                   auth: CurrentSession = Depends(require_mutation_capability('edit_golden'))):
    from masterone.mdm.golden_service import golden_command
    return golden_command(db, run_id, body, auth.user.id, 'CONFIRM_GOLDEN', cluster_id)


@router.post('/golden/confirm-selected')
def confirm_scope(run_id: str, body: ClusterScopeRequest, db: Session = Depends(get_session),
                  auth: CurrentSession = Depends(require_mutation_capability('edit_golden'))):
    from masterone.mdm.golden_service import confirm_selected
    return confirm_selected(db, run_id, body, auth.user.id)


@router.post('/pairs/{pair_id}/explanation')
def explain(run_id: str, pair_id: str, body: Mutation, db: Session = Depends(get_session),
            auth: CurrentSession = Depends(require_mutation_capability('explain'))):
    from masterone.mdm.workflow_explanation import explain_pair
    return explain_pair(db, run_id, pair_id, body, auth.user.id)


@router.post('/golden/resolve', response_model=OperationRead, status_code=202)
def resolve(run_id: str, body: ResolveRequest, db: Session = Depends(get_session),
            auth: CurrentSession = Depends(require_mutation_capability('edit_golden'))):
    from masterone.mdm.workflow_resolution import submit_resolution
    return submit_resolution(db, run_id, body, auth.user.id)


@router.post('/golden/resolution/apply', response_model=OperationRead, status_code=202)
def apply_repair(run_id: str, body: RepairRequest, db: Session = Depends(get_session),
                 auth: CurrentSession = Depends(require_mutation_capability('approve_pair'))):
    from masterone.auth.security import check_capability
    check_capability(auth, 'reject_pair')
    check_capability(auth, 'edit_golden')
    from masterone.mdm.workflow_resolution import repair_command
    return repair_command(db, run_id, body, auth.user.id)


@router.get('/golden/resolution')
def read_resolution(run_id: str, db: Session = Depends(get_session),
                    auth: CurrentSession = Depends(require_capability('read'))):
    from masterone.mdm.workflow_resolution import versions
    run = owned_run(db, run_id, auth.user.id)
    op = db.query(WorkflowOperation).filter_by(run_id=run_id, operation_type='GOLDEN_RESOLVE', status='COMPLETED').order_by(WorkflowOperation.created_at.desc()).first()
    undo = db.query(WorkflowOperation).filter_by(run_id=run_id, operation_type='APPLY_REPAIR', status='COMPLETED').filter(
        WorkflowOperation.result['decision_revision'].as_integer() == run.decision_revision).first()
    undo_id = undo.id if undo and undo.result.get('undo_versions') == versions(run) else None
    return {'resolution': operation_read(op) if op else None, 'stale': bool(op and op.result.get('versions') != versions(run)), 'undo_operation_id': undo_id}


@router.get('/golden/resolution/changes')
def read_repair_changes(run_id: str, operation_id: str, offset: int = Query(0, ge=0), limit: int = Query(25, ge=1, le=100),
                        db: Session = Depends(get_session), auth: CurrentSession = Depends(require_capability('read'))):
    owned_run(db, run_id, auth.user.id)
    op = db.query(WorkflowOperation).filter_by(id=operation_id, run_id=run_id, operation_type='GOLDEN_RESOLVE', status='COMPLETED').one_or_none()
    if op is None:
        raise HTTPException(404, 'Resolution not found')
    from masterone.mdm.workflow_artifacts import load
    from masterone.mdm.workflow_cluster_reads import evidence_page
    plan = load(db, op.artifact_id, run_id, 'GOLDEN_REPAIR')
    return evidence_page(plan['proposal']['changes'] if plan['proposal'] else [], limit, offset)


@router.post('/golden/resolution/undo', response_model=OperationRead, status_code=202)
def undo_repair(run_id: str, body: RepairRequest, db: Session = Depends(get_session),
                auth: CurrentSession = Depends(require_mutation_capability('approve_pair'))):
    from masterone.auth.security import check_capability
    check_capability(auth, 'reject_pair')
    check_capability(auth, 'edit_golden')
    from masterone.mdm.workflow_resolution import repair_command
    return repair_command(db, run_id, body, auth.user.id, undo=True)


@router.post('/finalize-golden')
def finalize_golden(run_id: str, body: FinalizeGoldenRequest, db: Session = Depends(get_session),
                    auth: CurrentSession = Depends(require_mutation_capability('edit_golden'))):
    from masterone.mdm.golden_service import finalize_golden as finalize
    result = finalize(db, run_id, body, auth.user.id)
    if result.get('operation_id'):
        from fastapi.encoders import jsonable_encoder
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=202, content=jsonable_encoder(result))
    return result


@router.post('/reopen-decisions')
def reopen_decisions(run_id: str, body: ReopenRequest, db: Session = Depends(get_session),
                     auth: CurrentSession = Depends(require_mutation_capability('approve_pair'))):
    from masterone.mdm.golden_service import reopen_decisions as reopen
    return reopen(db, run_id, body, auth.user.id)
