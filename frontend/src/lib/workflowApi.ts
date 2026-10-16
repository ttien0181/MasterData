import { request } from './api';
import type {ClusterPreviewRequest,FinalizeRequest,GroupApprovalRequest} from './workflowContracts';
import type { BulkPreview, BulkRequest, ConflictRead, GenerationPage, ClusterDetail, ClusterRead, DataRead, DecisionRequest, GoldenRead, GoldenRequest, GoldenResult, GoldenRevision, Mutation, OperationRead, PageRead, PairRead, PairSummary, RecordRead, WorkflowRead } from './workflowContracts';
const path = (run: string, suffix = '') => `/runs/${encodeURIComponent(run)}/workflow${suffix}`;
const get = <T>(run: string, suffix = '', signal?: AbortSignal) => request<T>(path(run, suffix), { signal });
const write = <T>(run: string, suffix: string, body: unknown, method = 'POST', signal?: AbortSignal) => request<T>(path(run, suffix), { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), signal });
export const shadowAutomation=(run:string,body:import('./workflowContracts').AutomationShadowRequest)=>write<OperationRead>(run,'/automation/shadow',body);
export const applyAutomation=(run:string,body:import('./workflowContracts').AutomationApplyRequest)=>write<OperationRead>(run,'/automation/apply',body);
export const stopAutomation=(run:string,body:Mutation)=>write<OperationRead>(run,'/automation/stop',body);
export const mutation = (workflow: WorkflowRead): Mutation => ({ request_id: crypto.randomUUID(), expected_workflow_revision: workflow.workflow_revision, expected_assessment_id: workflow.assessment_id ?? null });
export const goldenRevision = (w: WorkflowRead): GoldenRevision => ({ ...mutation(w), expected_cluster_revision: w.cluster_revision, expected_golden_revision: w.golden_revision });
export const getWorkflow = (run: string, signal?: AbortSignal) => get<WorkflowRead>(run, '', signal);
export const getData = (run: string, query = new URLSearchParams(), signal?: AbortSignal) => get<DataRead>(run, `/data?${query}`, signal);
export const getPairs = (run: string, query = new URLSearchParams(), signal?: AbortSignal) => get<PageRead<PairRead> & { offset?: number }>(run, `/pairs?${query}`, signal);
export const getPairSummary = (run: string, signal?: AbortSignal) => get<PairSummary>(run, '/pairs/summary?visibility=review&taxonomy=actions_v1', signal);
export const previewBulkDecisions = (run: string, body: BulkRequest, signal?: AbortSignal) => request<BulkPreview>(path(run, '/bulk-decisions/preview'), { method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body),signal });
export const getClusters = (run: string, query = new URLSearchParams(), signal?: AbortSignal) => get<GenerationPage<ClusterRead>>(run, `/clusters?${query}`, signal);
export const getCluster = (run: string, id: string, query = new URLSearchParams(), signal?: AbortSignal) => get<ClusterDetail>(run, `/clusters/${encodeURIComponent(id)}?${query}`, signal);
export const getClusterGolden = (run: string, id: string, signal?: AbortSignal) => get<GoldenRead>(run, `/clusters/${encodeURIComponent(id)}/golden`, signal);
export const getGoldenRecords = (run: string, query = new URLSearchParams(), signal?: AbortSignal) => get<GenerationPage<GoldenRead>>(run, `/golden?${query}`, signal);
export const getOperation = (run: string, id: string, signal?: AbortSignal) => get<OperationRead>(run, `/operations/${encodeURIComponent(id)}`, signal);
export const startDecide = (run: string, body: Mutation) => write<OperationRead>(run, '/start-decide', body);
export const processData = (run: string, body: Mutation & { include_advice?: boolean; advice_limit?: number; model_scope?: 'synthetic-benchmark' }) => write<OperationRead>(run, '/process', body);
export const decidePair = (run: string, id: string, body: DecisionRequest) => write<PairRead>(run, `/pairs/${encodeURIComponent(id)}/decision`, body);
export const bulkDecisions = (run: string, body: BulkRequest) => write<OperationRead>(run, '/bulk-decisions', body);
export const previewClusters = (run:string,body:ClusterPreviewRequest) => write<OperationRead>(run,'/cluster-preview',body);
export const getPreviewGroups = (run:string,operationId:string,query=new URLSearchParams(),signal?:AbortSignal) =>
  get<import('./workflowContracts').PreviewGroupPage>(run,`/cluster-preview/${encodeURIComponent(operationId)}/groups?${query}`,signal);
export const getReviewGroupMembers = (run:string,operationId:string,fingerprint:string,query=new URLSearchParams(),signal?:AbortSignal) =>
 get<import('./workflowContracts').PageRead<import('./workflowContracts').PreviewMember>&{stale:boolean}>(run,`/cluster-previews/${encodeURIComponent(operationId)}/review-groups/${encodeURIComponent(fingerprint)}/members?${query}`,signal);
export const getReviewGroupEvidence = (run:string,operationId:string,fingerprint:string,query=new URLSearchParams(),signal?:AbortSignal) =>
 get<import('./workflowContracts').PageRead<import('./workflowContracts').PairBaseline&{computed_only:boolean}>>(run,`/cluster-previews/${encodeURIComponent(operationId)}/review-groups/${encodeURIComponent(fingerprint)}/evidence?${query}`,signal);
export const approveGroup = (run:string,body:GroupApprovalRequest) => write<OperationRead>(run,'/group-approvals',body);
export const finalizeDecisions = (run: string, body: FinalizeRequest) => write<OperationRead>(run, '/finalize-decisions', body);
export const saveGolden = (run: string, id: string, body: GoldenRequest) => write<GoldenResult>(run, `/clusters/${encodeURIComponent(id)}/golden`, body, 'PUT');
export const autoSelectGolden = (run: string, body: GoldenRevision & { cluster_ids: string[]; reset_manual?: boolean }) => write<GoldenResult>(run, '/golden/auto-select', body);
export const confirmGolden = (run: string, id: string, body: GoldenRevision) => write<GoldenResult>(run, `/clusters/${encodeURIComponent(id)}/golden/confirm`, body);
export type ClusterScope = { cluster_ids?: string[]; excluded_cluster_ids?: string[] };
export type ResolveBody = GoldenRevision & ClusterScope & { expected_decision_revision: number };
export type ExplanationRead = { status: string; error_code?: string; model_version?: string; provider?: string; explanation: { summary_vi: string; attention_fields?: string[]; limitations: string[] } | null };
export type RepairProposal = { status: string; reason_codes: string[]; changes: { pair_id: string; left_id: string; right_id: string; before: string; after: string }[]; change_count: number; before_record_ids: string[]; after_groups: string[][]; explanation_vi: string };
export type ResolutionResult = { confirmed: number; blocked: number; proposal: RepairProposal | null; versions: Record<string, number> };
export type ResolutionRead = { resolution: (OperationRead & { result: ResolutionResult }) | null; undo_operation_id: string | null; stale: boolean };
export const confirmSelectedGolden = (run: string, body: GoldenRevision & ClusterScope) => write<{ confirmed: number }>(run, '/golden/confirm-selected', body);
export const resolveGolden = (run: string, body: ResolveBody) => write<OperationRead>(run, '/golden/resolve', body);
export const getGoldenResolution = (run: string, signal?: AbortSignal) => get<ResolutionRead>(run, '/golden/resolution', signal);
export const applyGoldenRepair = (run: string, body: ResolveBody & { operation_id: string }, undo = false) => write<OperationRead>(run, `/golden/resolution/${undo ? 'undo' : 'apply'}`, body);
export const explainWorkflowPair = (run: string, id: string, body: Mutation, signal?: AbortSignal) => write<ExplanationRead>(run, `/pairs/${encodeURIComponent(id)}/explanation`, body, 'POST', signal);
export const finalizeGolden = (run: string, body: Mutation & { expected_golden_revision: number }) => write<{ result_hash: string } | OperationRead>(run, '/finalize-golden', body);
export const reopenDecisions = (run: string, body: Mutation & { reason: string }) => write<WorkflowRead>(run, '/reopen-decisions', body);
export const retryOperation = (run: string, id: string, body: Mutation) => write<OperationRead>(run, `/operations/${encodeURIComponent(id)}/retry`, body);
export const prepareWorkflowExport = (run: string) => request<{ preview_id: string; estimated_output_rows: number; result_hash: string }>(`/runs/${encodeURIComponent(run)}/previews`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ choices: {}, acknowledge_unreviewed: false }) });

export const reassessPairs = (run:string,body:Mutation & {model_scope?:'synthetic-benchmark'}) => write<OperationRead>(run,'/reassess',body);
export const requestPairAdvice = (run:string,body:Mutation & {pair_ids?:string[];limit?:number}) => write<OperationRead>(run,'/pair-advice',body);

export const getPair = (run:string,id:string,signal?:AbortSignal) => get<PairRead>(run,`/pairs/${encodeURIComponent(id)}`,signal);
export const getReviewCluster = (run:string,id:string,signal?:AbortSignal) => get<import('./workflowContracts').ReviewCluster>(run,`/pairs/${encodeURIComponent(id)}/review-cluster`,signal);
export const getOperationByRequestId = (run:string,id:string,signal?:AbortSignal) => get<OperationRead|null>(run,`/requests/${encodeURIComponent(id)}`,signal);
export const getConflicts = (run:string,query=new URLSearchParams(),signal?:AbortSignal) => get<PageRead<ConflictRead>>(run,`/conflicts?${query}`,signal);
export const getUnresolvedRecords = (run:string,query=new URLSearchParams(),signal?:AbortSignal) => get<GenerationPage<RecordRead>>(run,`/unresolved-records?${query}`,signal);
export const amendPair = (run:string,id:string,body:DecisionRequest & {reason:string}) => write<PairRead>(run,`/pairs/${encodeURIComponent(id)}/amend`,body);

export const requestGroupAnnotations = (run:string,body:Mutation & {expected_cluster_revision:number}) => write<OperationRead>(run,'/group-annotations',body);

export type ClusterQueueSummary={pending_count:number;accepted_count:number;published_count:number;decision_revision:number;cluster_revision:number};
export const getClusterQueue=(run:string,signal?:AbortSignal)=>get<ClusterQueueSummary>(run,'/cluster-queue',signal);
export const buildClusterQueue=(run:string,body:Mutation & {expected_decision_revision:number;queue_mode:'pending'|'all'})=>write<OperationRead>(run,'/cluster-queue',body);
