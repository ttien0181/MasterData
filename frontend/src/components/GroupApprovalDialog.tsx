'use client';
import {useState} from 'react';
import {approveGroup,getReviewGroupMembers,mutation} from '@/lib/workflowApi';
import {useWorkflow} from './SessionWorkspace';
import {useWorkflowAction} from './useWorkflowAction';
import PairReviewDialog from './PairReviewDialog';
import GroupMembersTable from './GroupMembersTable';
import ReviewGroupEvidence from './ReviewGroupEvidence';
import type {PreviewMember} from '@/lib/workflowContracts';
import ReviewReason,{reviewReason} from './ReviewReason';
export default function GroupApprovalDialog({recordIds,members,close,approved,disabled=false,fingerprint,previewOperationId,totalMemberCount,maxMembers=100}:{recordIds:string[];members?:PreviewMember[];close:()=>void;approved:()=>void;disabled?:boolean;fingerprint?:string;previewOperationId?:string;totalMemberCount?:number;maxMembers?:number}) {
 const {workflow:w}=useWorkflow(),action=useWorkflowAction();const [reason,setReason]=useState('');
 const [frozenIds]=useState(()=>[...recordIds]);const total=totalMemberCount??frozenIds.length;
 const [kind,setKind]=useState('CUSTOM');const fullReason=reviewReason(kind,reason);
 const [source]=useState({decision:w.decision_revision,assessment:w.assessment_id??null});
 const stale=recordIds.join('|')!==frozenIds.join('|')||source.decision!==w.decision_revision||source.assessment!==(w.assessment_id??null);
 async function save(){if(disabled||stale||!fullReason||fullReason.length>500||frozenIds.length<2||total!==frozenIds.length||total>maxMembers)return;const result=await action.run(()=>approveGroup(w.run_id,{...mutation(w),expected_decision_revision:w.decision_revision,record_ids:frozenIds,reason:fullReason,...(fingerprint&&previewOperationId?{fingerprint,preview_operation_id:previewOperationId}:{})}));if(result?.status==='COMPLETED')approved();}
 return <PairReviewDialog title="Duyệt ngoại lệ nhóm" wide={!!members?.length} close={()=>{if(!action.busy)close();}}><p>Phê duyệt đúng nhóm gồm {total} thành viên dưới đây:</p>{members?.length?<GroupMembersTable members={members} totalMemberCount={total} loadPage={fingerprint&&previewOperationId?cursor=>getReviewGroupMembers(w.run_id,previewOperationId,fingerprint,new URLSearchParams({cursor,limit:'25'})):undefined}/>:<p>{recordIds.join(' / ')}</p>}<p>Phê duyệt xác nhận các thành viên cùng thực thể và Accept các cặp nội bộ còn chờ bằng cùng lý do. Quyết định Deny hoặc Không gộp được giữ nguyên.</p><p>Ngoại lệ xác nhận thông tin đối chiếu liên kết gián tiếp còn thiếu cho đúng tập thành viên này. Mọi quy định bắt buộc, Deny, Không gộp, nguồn hồ sơ và giới hạn kích thước vẫn được kiểm tra; ngoại lệ không cho phép bỏ qua chúng.</p><>{fingerprint&&previewOperationId&&<ReviewGroupEvidence runId={w.run_id} operationId={previewOperationId} fingerprint={fingerprint}/>}</><ReviewReason kind={kind} note={reason} onKind={setKind} onNote={setReason} label="Lý do ngoại lệ nhóm" kindLabel="Loại lý do ngoại lệ"/>{action.error&&<p role="alert">{action.error}</p>}{stale&&<p role="alert">Quyết định hoặc đánh giá đã thay đổi. Đóng hộp thoại và đối chiếu lại nhóm.</p>}<footer><button disabled={action.busy} onClick={close}>Hủy</button><button disabled={disabled||stale||action.busy||!fullReason||fullReason.length>500||frozenIds.length<2||total!==frozenIds.length||total>maxMembers||!w.allowed_actions.includes('approve-group')} onClick={()=>void save()}>Xác nhận ngoại lệ nhóm</button></footer></PairReviewDialog>;
}
