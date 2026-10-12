'use client';
import { useCallback, useEffect } from 'react';
import { ApiError } from '@/lib/api';
import type { BulkRequest } from '@/lib/workflowContracts';
import { previewBulkDecisions } from '@/lib/workflowApi';
import { number } from '@/lib/pairReview';
import { useWorkflow } from './SessionWorkspace';
import { useSessionRead } from './useSessionRead';
import { ReadNotice } from './WorkflowReads';
import PairReviewDialog from './PairReviewDialog';
import styles from './DecisionWorkspace.module.css';
export default function PairBulkConfirmation({body,label,close,confirm,busy,error,queued=false}: {body:BulkRequest;label:string;close:()=>void;confirm:()=>void;busy:boolean;error:string;queued?:boolean}) {
  const {workflow:w,refresh}=useWorkflow();
  const load=useCallback((signal:AbortSignal)=>previewBulkDecisions(w.run_id,body,signal),[w.run_id,body]);
  const read=useSessionRead(w.run_id,['bulk-preview',body],{},load,true,false,0);
  useEffect(()=>{
    if(read.cause instanceof ApiError && read.cause.status===409) void refresh().catch(()=>undefined);
  },[read.cause,refresh]);
  const stale=queued?(w.assessment_id??null)!==(body.expected_assessment_id??null):w.workflow_revision!==body.expected_workflow_revision || w.decision_revision!==body.expected_decision_revision;
  const verb=body.decision==='merge'?'Accept':body.decision==='deny'?'Deny':'Hoãn';
  return <PairReviewDialog title={`Xác nhận ${verb}`} close={()=>{if(!busy)close();}}><p className={styles.scopeLabel}>Phạm vi: {label}</p><p className={styles.muted}>Bao gồm các trang chưa xem trong đúng phạm vi này. Các cặp đã có quyết định sẽ được giữ nguyên.</p>
    {queued&&<p>Sau xác nhận, yêu cầu vào hàng chờ và bạn có thể tiếp tục duyệt. Số cặp có thể áp dụng được tính lại lúc xử lý; các cặp đã được duyệt trước đó được bỏ qua.</p>}
    {!read.data?<ReadNotice error={read.error} retry={read.retry}/>:<div className={styles.preview}><strong>{number(read.data.applicable)} cặp có thể áp dụng</strong><p>{number(read.data.total)} cặp trong phạm vi · {number(read.data.blocked)} cặp bị chặn · {number(read.data.skipped)} cặp đã có quyết định được giữ lại.</p><p>{verb==='Deny'?'Deny ngăn hai bản ghi cùng nhóm, kể cả qua liên kết gián tiếp.':verb==='Accept'?'Accept dùng quyền duyệt thủ công; giữ mọi ràng buộc chặn. Nhóm chỉ được tạo khi hoàn tất duyệt và kiểm tra hợp lệ.':'Hoãn giữ cặp ở trạng thái cần xử lý và tiếp tục chặn hoàn tất duyệt.'}</p></div>}
    {error&&<p role="alert">{error}</p>}{stale&&<p role="alert">Phiên đã thay đổi. Đóng hộp thoại và xem trước lại phạm vi trước khi xác nhận.</p>}
    <footer><button disabled={busy} onClick={close}>Hủy</button><button className={verb==='Deny'?styles.deny:styles.accept} disabled={busy || stale || read.fetching || !!read.error || !read.data?.applicable} onClick={confirm}>Xác nhận {verb}</button></footer>
  </PairReviewDialog>;
}
