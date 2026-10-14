'use client';
import { useCallback, useEffect, useState } from 'react';
import { ApiError } from '@/lib/api';
import type { BulkPreview, BulkRequest } from '@/lib/workflowContracts';
import { previewBulkDecisions } from '@/lib/workflowApi';
import { number } from '@/lib/pairReview';
import { useWorkflow } from './SessionWorkspace';
import { useSessionRead } from './useSessionRead';
import { ReadNotice } from './WorkflowReads';
import PairReviewDialog from './PairReviewDialog';
import styles from './DecisionWorkspace.module.css';
export default function PairBulkConfirmation({body,label,close,confirm,busy,error,queued=false}: {body:BulkRequest;label:string;close:()=>void;confirm:(preview?:BulkPreview,reason?:string)=>void;busy:boolean;error:string;queued?:boolean}) {
  const [reason,setReason]=useState(body.reason || '');
  const {workflow:w,refresh}=useWorkflow();
  const load=useCallback((signal:AbortSignal)=>previewBulkDecisions(w.run_id,body,signal),[w.run_id,body]);
  const read=useSessionRead(w.run_id,['bulk-preview',body],{},load,true,false,0);
  useEffect(()=>{
    if(read.cause instanceof ApiError && read.cause.status===409) void refresh().catch(()=>undefined);
  },[read.cause,refresh]);
  const stale=(w.assessment_id??null)!==(body.expected_assessment_id??null) || w.workflow_revision!==body.expected_workflow_revision || w.decision_revision!==body.expected_decision_revision || !!read.data && (read.data.workflow_revision!==w.workflow_revision || read.data.decision_revision!==w.decision_revision);
  const verb=body.decision==='merge'?'Accept':body.decision==='deny'?'Deny':'Hoãn';
  return <PairReviewDialog title={`Xác nhận ${verb}`} close={()=>{if(!busy)close();}}><p className={styles.scopeLabel}>Phạm vi: {label}</p><p className={styles.muted}>Bao gồm các trang chưa xem trong đúng phạm vi này. Các cặp đã có quyết định sẽ được giữ nguyên.</p>
    {queued&&<p>Sau xác nhận, yêu cầu vào hàng chờ và bạn có thể tiếp tục duyệt. Yêu cầu giữ phiên bản và phạm vi đã xem trước; nếu quyết định thay đổi, cần mở lại xác nhận.</p>}
    {!read.data?<ReadNotice error={read.error} retry={read.retry}/>:<div className={styles.preview}><p>Kết quả xem trước chỉ được áp dụng khi dữ liệu và quyết định liên quan chưa thay đổi.</p><strong>{number(read.data.applicable)} cặp có thể áp dụng</strong><p>{number(read.data.total)} cặp trong phạm vi · {number(read.data.blocked)} cặp bị chặn · {number(read.data.skipped)} cặp đã có quyết định được giữ lại.</p><p>{verb==='Deny'?'Tách riêng (Deny) tạo ràng buộc lâu dài, ngăn hai bản ghi cùng nhóm kể cả qua liên kết gián tiếp.':verb==='Accept'?'Accept dùng quyền duyệt thủ công; giữ mọi ràng buộc chặn. Nhóm chỉ được tạo khi hoàn tất duyệt và kiểm tra hợp lệ.':'Hoãn giữ cặp ở trạng thái cần xử lý và tiếp tục chặn hoàn tất duyệt.'}</p>{body.decision==='deny'&&<p>Ràng buộc mới dự kiến: {number(read.data.new_negative_links ?? read.data.applicable)}.</p>}</div>}
    {body.decision==='merge'&&<label>Lý do Accept{read.data?.reason_required?` (bắt buộc cho ${read.data.reason_required} cặp có mâu thuẫn mềm)`:' (không bắt buộc)'}<textarea aria-label="Lý do Accept" maxLength={500} value={reason} onChange={event=>setReason(event.target.value)}/></label>}
    {read.data?.group_review_required&&<p role="alert">Nhóm D chỉ được Accept hàng loạt trong một nhóm đã đối chiếu, tối đa 20 bản ghi. Đóng hộp thoại và chọn Duyệt theo cụm tại một cặp cần xử lý.</p>}{error&&<p role="alert">{error}</p>}{stale&&<p role="alert">Phiên đã thay đổi. Đóng hộp thoại và xem trước lại phạm vi trước khi xác nhận.</p>}
    <footer><button disabled={busy} onClick={close}>Hủy</button><button aria-label={`Xác nhận ${verb}`} className={verb==='Deny'?styles.deny:styles.accept} disabled={busy || stale || !!read.data?.group_review_required || read.fetching || !!read.error || !read.data?.applicable || !!read.data?.reason_required && !reason.trim() || reason.length>500} onClick={()=>reason.trim()?confirm(read.data ?? undefined,reason.trim()):confirm(read.data ?? undefined)}>{body.decision==='deny'?'Xác nhận khác thực thể (Deny)':body.decision==='merge'?'Xác nhận cùng thực thể (Accept)':'Chờ bổ sung bằng chứng (Hoãn)'}</button></footer>
  </PairReviewDialog>;
}
