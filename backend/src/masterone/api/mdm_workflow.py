from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from masterone.auth.security import CurrentSession, require_capability, require_mutation_capability
from masterone.db.session import get_session
from masterone.mdm.engine_adapter import FrontendJSONRoute, api_engine_context
from masterone.mdm.ownership import owned_run
from masterone.mdm.workflow_contracts import (
    AutoRequest,
    AssessmentRequest,
    PairAdviceRequest,
    BulkRequest,
    ClusterScopeRequest,
    DecisionRequest,
    FinalizeGoldenRequest,
    FinalizeRequest,
    GoldenRequest,
    GoldenRevisionRequest,
    Mutation,
    OperationRead,
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
    result = get_workflow(db, run_id, auth.user.id)
    from masterone.auth.security import check_capability
    capabilities = {'start-decide': 'start_run', 'decide-pair': 'approve_pair', 'bulk-decisions': 'approve_pair',
                    'finalize-decisions': 'approve_pair', 'save-golden': 'edit_golden', 'auto-select': 'edit_golden',
                    'confirm-golden': 'edit_golden', 'finalize-golden': 'edit_golden',
                    'reopen-decisions': 'approve_pair', 'export': 'export_data', 'retry-operation': 'start_run',
                    'resolve-golden': 'edit_golden', 'reassess-pairs': 'approve_pair', 'assess-with-ai': 'approve_pair',
                    'queue-decisions': 'approve_pair'}
    actions = []
    for action in result['allowed_actions']:
        try:
            check_capability(auth, capabilities[action])
            actions.append(action)
        except HTTPException:
            pass
    result['allowed_actions'] = actions
    return result


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


@router.post('/reassess', response_model=OperationRead, status_code=202)
def reassess_pairs(run_id: str, body: AssessmentRequest, db: Session = Depends(get_session),
                   auth: CurrentSession = Depends(require_mutation_capability('approve_pair'))):
    return begin_operation(db, run_id, 'ASSESS_PAIRS', body.request_id, body.model_dump(),
                           body.expected_workflow_revision, auth.user.id)


@router.post('/pair-advice', response_model=OperationRead, status_code=202)
def assess_with_ai(run_id: str, body: PairAdviceRequest, db: Session = Depends(get_session),
                   auth: CurrentSession = Depends(require_mutation_capability('approve_pair'))):
    return begin_operation(db, run_id, 'PAIR_ADVICE', body.request_id, body.model_dump(),
                           body.expected_workflow_revision, auth.user.id)


@router.get('/data')
def read_data(run_id: str, limit: int = Query(10, ge=1, le=100), cursor: str | None = None,
              offset: int | None = Query(None, ge=0),
              db: Session = Depends(get_session), auth: CurrentSession = Depends(require_capability('read'))):
    owned_run(db, run_id, auth.user.id)
    from masterone.mdm.data_profile import data_read
    return data_read(db, run_id, limit, cursor, offset)


@router.get('/pairs')
def read_pairs(run_id: str, limit: int = Query(10, ge=1, le=100), cursor: str | None = None,
               offset: int | None = Query(None, ge=0),
               group: str | None = None, subgroup: str | None = None, resolution: str | None = None,
               db: Session = Depends(get_session), auth: CurrentSession = Depends(require_capability('read'))):
    owned_run(db, run_id, auth.user.id)
    from masterone.mdm.workflow_pair_reads import pair_page
    return pair_page(db, run_id, limit, cursor, offset, dict(group=group, subgroup=subgroup, resolution=resolution))


@router.get('/pairs/summary')
def read_pair_summary(run_id: str, db: Session = Depends(get_session),
                      auth: CurrentSession = Depends(require_capability('read'))):
    run = owned_run(db, run_id, auth.user.id)
    from masterone.mdm.workflow_pair_reads import pair_summary
    return pair_summary(db, run)


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
    if run.workflow_state != 'DECIDE_REVIEW' or (op and op.operation_type != 'BULK_DECISIONS'):
        conflict('DECISIONS_LOCKED')
    from masterone.mdm.workflow_pair_reads import bulk_preview
    return bulk_preview(db, run, body)


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
    return begin_operation(db, run_id, 'BULK_DECISIONS', body.request_id, body.model_dump(), body.expected_workflow_revision, auth.user.id)


@router.post('/finalize-decisions', response_model=OperationRead, status_code=202)
def finalize_decisions(run_id: str, body: FinalizeRequest, db: Session = Depends(get_session),
                       auth: CurrentSession = Depends(require_mutation_capability('approve_pair'))):
    from masterone.mdm.workflow_finalize import finalize_decisions as finalize
    return finalize(db, run_id, body, auth.user.id)


@router.get('/clusters')
def read_clusters(run_id: str, limit: int = Query(25, ge=1, le=100), cursor: str | None = None,
                  db: Session = Depends(get_session), auth: CurrentSession = Depends(require_capability('read'))):
    run = owned_run(db, run_id, auth.user.id)
    from masterone.mdm.workflow_cluster_reads import list_clusters
    return list_clusters(db, run, limit, cursor)


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
    return golden_read(db, run, cluster_row(db, run, cluster_id))


@router.get('/golden')
def read_golden(run_id: str, limit: int = Query(25, ge=1, le=100), cursor: str | None = None,
                db: Session = Depends(get_session), auth: CurrentSession = Depends(require_capability('read'))):
    run = owned_run(db, run_id, auth.user.id)
    from masterone.mdm.golden_service import golden_read
    from masterone.mdm.workflow_cluster_reads import current_generation
    from masterone.mdm.workflow_models import WorkflowCluster
    from masterone.mdm.workflow_review import paginate
    generation = current_generation(db, run)
    return paginate(db.query(WorkflowCluster).filter_by(generation_id=generation.id), WorkflowCluster.id,
                    limit, cursor, lambda c: golden_read(db, run, c))


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
    return finalize(db, run_id, body, auth.user.id)


@router.post('/reopen-decisions')
def reopen_decisions(run_id: str, body: ReopenRequest, db: Session = Depends(get_session),
                     auth: CurrentSession = Depends(require_mutation_capability('approve_pair'))):
    from masterone.mdm.golden_service import reopen_decisions as reopen
    return reopen(db, run_id, body, auth.user.id)
