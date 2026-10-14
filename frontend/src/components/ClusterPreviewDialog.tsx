'use client';
import {useCallback,useEffect,useRef,useState} from 'react';
import type {ClusterPreview,OperationRead,WorkflowRead} from '@/lib/workflowContracts';
import {finalizeDecisions,getOperation,mutation,previewClusters} from '@/lib/workflowApi';
import {reasonLabel} from '@/lib/pairReview';
import {useWorkflow} from './SessionWorkspace';
import {useWorkflowAction} from './useWorkflowAction';
import PairReviewDialog from './PairReviewDialog';
import GroupApprovalDialog from './GroupApprovalDialog';
import {sessionKey,useSessionCache} from './SessionCache';
import PreviewGroups from './PreviewGroups';
export default function ClusterPreviewDialog({partial,pending,attention,close,created,reviewPair}:{partial:boolean;pending:number;attention:number;close:()=>void;created:()=>void;reviewPair?:(id:string)=>void}) {
 const {workflow:w,refresh}=useWorkflow(),action=useWorkflowAction();
 const cache=useSessionCache();
 const latest=useRef({w,refresh});latest.current={w,refresh};
 const alive=useRef(false),poll=useRef<ReturnType<typeof setTimeout>|null>(null);
 const [operation,setOperation]=useState<OperationRead|null>(null),[loading,setLoading]=useState(true),[error,setError]=useState(''),[approval,setApproval]=useState<string[]|null>(null);
 const [initialRequest]=useState(()=>mutation(w));
 const acceptOperation=useCallback(async(op:OperationRead)=>{
  if(!alive.current)return;setOperation(op);setLoading(false);
  if(op.status==='COMPLETED'&&op.result.publication){const data=op.result as unknown as ClusterPreview;cache.client.setQueryData(sessionKey(cache.userId,op.run_id,'cluster-preview',data.source_decision_revision,data.source_assessment_id,data.allow_partial),op);}
  await Promise.resolve(latest.current.refresh()).catch(()=>undefined);
 },[cache.client,cache.userId]);
 const load=useCallback(async(initial=false)=>{
  if(poll.current)clearTimeout(poll.current);setLoading(true);setError('');setOperation(null);
  try{const view=latest.current.w;const refreshed=cache.client.getQueryData<WorkflowRead>(sessionKey(cache.userId,view.run_id,'workflow'));const current=refreshed&&refreshed.workflow_revision>=view.workflow_revision?refreshed:view;const saved=initial?cache.client.getQueryData<OperationRead>(sessionKey(cache.userId,current.run_id,'cluster-preview',current.decision_revision,current.assessment_id??null,partial)):undefined;const op=saved??await previewClusters(current.run_id,{...mutation(current),...(initial?{request_id:initialRequest.request_id}:{}),expected_decision_revision:current.decision_revision,allow_partial:partial});await acceptOperation(op);}
  catch(cause){if(alive.current){setError(cause instanceof Error?cause.message:'Không tạo được bản xem trước.');setLoading(false);await Promise.resolve(latest.current.refresh()).catch(()=>undefined);}}
 },[w.run_id,partial,initialRequest,acceptOperation,cache.client,cache.userId]);
 useEffect(()=>{alive.current=true;void load(true);return()=>{alive.current=false;if(poll.current)clearTimeout(poll.current);};},[load]);
 useEffect(()=>{
  if(!operation||!['QUEUED','RUNNING'].includes(operation.status))return;
  poll.current=setTimeout(()=>{void getOperation(w.run_id,operation.operation_id).then(acceptOperation).catch(cause=>{if(alive.current)setError(cause instanceof Error?cause.message:'Không đọc được tiến độ xem trước.');});},1500);
  return()=>{if(poll.current)clearTimeout(poll.current);};
 },[operation,w.run_id,acceptOperation]);
 const result=operation?.status==='COMPLETED'&&operation.result.publication?operation.result as unknown as ClusterPreview:null;
 const stale=!!result&&(result.source_decision_revision!==w.decision_revision||result.source_assessment_id!==(w.assessment_id??null));
 const running=loading||!!operation&&['QUEUED','RUNNING'].includes(operation.status);
 const serverPending=w.blockers.find(blocker=>blocker.code==='PENDING_DECISIONS')?.count??0;
 const canCreate=!!result&&!stale&&!running&&!error&&!pending&&!attention&&!action.busy&&w.allowed_actions.includes(partial?'finalize-partial':'finalize-decisions')&&(partial||!result.conflict_count&&!result.publication.unresolved_records);
 async function readProgress(){if(!operation)return;setError('');try{await acceptOperation(await getOperation(w.run_id,operation.operation_id));}catch(cause){if(alive.current)setError(cause instanceof Error?cause.message:'Không đọc được tiến độ xem trước.');}}
 async function create(){if(!canCreate||!result||!operation)return;const op=await action.run(()=>finalizeDecisions(w.run_id,{...mutation(w),expected_decision_revision:w.decision_revision,allow_partial:partial,preview_operation_id:operation.operation_id,preview_token:result.preview_token}));if(op)created();}
 if(approval){const draft=result?.conflicts.find(c=>c.record_ids.join('|')===approval.join('|'));return <GroupApprovalDialog recordIds={approval} fingerprint={draft?.fingerprint} previewOperationId={operation?.operation_id} totalMemberCount={draft?.member_count} maxMembers={result?.review_limits?.reviewed??100} members={result?.conflicts.find(c=>c.record_ids.join('|')===approval.join('|'))?.preview_members} close={()=>setApproval(null)} disabled={!!pending||!!attention} approved={()=>{setApproval(null);void load();}}/>;}
 return <PairReviewDialog title="Xem trước các nhóm sẽ tạo" wide close={()=>{if(!action.busy)close();}}>
  <p>Xem thành viên nhóm dự kiến trước khi ghi kết quả chính thức. Đủ độ phủ chỉ cho biết mọi bản ghi được xử lý, không chứng nhận chất lượng nhận dạng.</p>
  <p>{partial?'Bản xem trước chỉ gồm các vùng an toàn đã xử lý.':'Bản xem trước đối chiếu toàn bộ quyết định hiện tại, bao gồm vùng còn cặp chưa duyệt. Các cặp chưa duyệt hoặc đang hoãn cần được xử lý trước khi hoàn tất phân loại; có thể duyệt ngoại lệ nhóm khi đủ điều kiện.'}</p>
  {running&&<p role="status">{loading?'Đang yêu cầu xem trước…':`Đang tính nhóm dự kiến · ${operation?.stage} · ${Object.entries(operation?.progress||{}).map(([key,value])=>`${key}: ${value}`).join(' · ')}`}</p>}
  {operation&&['FAILED','RETRYABLE_FAILED','CANCELLED'].includes(operation.status)&&<p role="alert">Xem trước chưa thành công: {operation.error_code || operation.status}</p>}
  {operation?.status==='COMPLETED'&&operation.result.stale===true&&!result&&<p role="alert">Quyết định đã thay đổi trong lúc chuẩn bị. Tính lại xem trước để lấy kết quả mới.</p>}
  {error&&<p role="alert">{error}</p>}{error&&operation&&running&&<button onClick={()=>void readProgress()}>Đọc lại tiến độ</button>}{action.error&&<p role="alert">{action.error}</p>}{stale&&<p role="alert">Quyết định hoặc đánh giá đã thay đổi. Tính lại bản xem trước trước khi tạo nhóm.</p>}
  {!!pending&&<p>{pending} yêu cầu đang chờ; chỉ xem trước, chờ hàng chờ hoàn tất để tạo nhóm.</p>}{!!attention&&<p role="alert">Có yêu cầu lỗi cần đối chiếu trước khi tạo nhóm.</p>}
  {serverPending>0&&<p>Server còn {serverPending} cặp chưa duyệt hoặc đang hoãn.</p>}
  {result&&<>
   <p>{result.publication.covered_records} / {result.publication.total_records} bản ghi · {result.publication.multi_record_groups} nhóm nhiều bản ghi · {result.publication.singletons} singleton · {result.publication.unresolved_records} bản ghi chưa giải quyết.</p>
   <p>Độ phủ dự kiến: {result.publication.publication_status==='COMPLETE'?'Đủ độ phủ':'Một phần'}.</p>
   <PreviewGroups key={operation?.operation_id} runId={w.run_id} operationId={operation!.operation_id} result={result} stale={stale}/>
   <h4>Xung đột ({result.conflict_count})</h4><p>Hiển thị {result.conflicts.length} / {result.conflict_count} xung đột.</p>
   {result.conflicts.map((conflict,index)=><article key={index}><p>{reasonLabel(conflict.reason)} · {conflict.record_ids.join(' / ')}</p>{['CLUSTER_BRIDGE_MISSING_EVIDENCE','CLUSTER_SIZE_LIMIT'].includes(conflict.reason)&&conflict.approvable!==false&&<button disabled={stale||!!pending||!!attention||action.busy||conflict.record_ids.length<2||(conflict.member_count??conflict.record_ids.length)>(result.review_limits?.reviewed??100)||!w.allowed_actions.includes('approve-group')} onClick={()=>setApproval(conflict.record_ids)}>Duyệt ngoại lệ nhóm</button>}</article>)}
   {!!result.pending_regions.length&&<><h4>Vùng còn chờ duyệt</h4>{result.pending_regions.map((region,index)=><section key={index}><p>{region.record_ids.slice(0,100).join(' / ')} · {region.record_count} bản ghi · {region.pending_pair_count??region.pending_pair_ids.length} cặp còn chờ.</p><p>Hiển thị {Math.min(region.record_ids.length,100)} / {region.record_count} mã bản ghi (tối đa 100).</p>{reviewPair&&region.pending_pair_ids[0]&&<button onClick={()=>reviewPair(region.pending_pair_ids[0])}>Đối chiếu vùng này</button>}</section>)}</>}
  </>}
  <footer><button disabled={action.busy} onClick={close}>Đóng</button><button disabled={running||action.busy} onClick={()=>void load()}>Tính lại xem trước</button><button disabled={!canCreate} onClick={()=>void create()}>{partial?'Tạo các nhóm đủ điều kiện':'Xác nhận tạo nhóm'}</button></footer>
 </PairReviewDialog>;
}
