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
  const verb=body.decision==='merge'?'Đồng ý gộp':body.decision==='deny'?'Không gộp':'Hoãn';
  return <PairReviewDialog title={`Xác nhận ${verb}`} close={()=>{if(!busy)close();}}><p className={styles.scopeLabel}>Phạm vi: {label}</p>
    {!read.data?read.error?<ReadNotice error={read.error} retry={read.retry}/>:<p role="status">Đang kiểm tra các cặp đã chọn…</p>:<div className={styles.preview}><strong>{read.data.applicable?`${number(read.data.applicable)} cặp có thể ${verb}`:`Không có cặp đủ điều kiện ${verb}`}</strong><p>{number(read.data.blocked)} bị chặn · {number(read.data.skipped)} đã duyệt, giữ nguyên</p>{queued&&read.data.applicable>0&&<p>Xác nhận để xử lý nền. Bạn có thể tiếp tục duyệt.</p>}{verb==='Không gộp'&&<p>Không gộp ngăn hai hồ sơ vào cùng nhóm, kể cả qua liên kết gián tiếp.</p>}</div>}
    {body.decision==='merge'&&!!read.data?.applicable&&<label>Lý do Đồng ý gộp{read.data?.reason_required?` (bắt buộc cho ${read.data.reason_required} cặp có mâu thuẫn mềm)`:' (không bắt buộc)'}<textarea aria-label="Lý do Đồng ý gộp" maxLength={500} value={reason} onChange={event=>setReason(event.target.value)}/></label>}
    {read.data?.group_review_required&&<p role="alert">Các cặp có thông tin mâu thuẫn chỉ được đồng ý gộp hàng loạt trong một nhóm đã đối chiếu, tối đa 20 hồ sơ. Mở Đối chiếu của cặp cần kiểm tra rồi chọn Đối chiếu nhóm.</p>}{error&&<p role="alert">{error}</p>}{stale&&<p role="alert">Phiên đã thay đổi. Đóng hộp thoại và xem trước lại phạm vi trước khi xác nhận.</p>}
    <footer><button disabled={busy} onClick={close}>{read.data?.applicable===0?'Đóng':'Hủy'}</button>{read.data?.applicable!==0&&<button aria-label={`Xác nhận ${verb}`} className={verb==='Không gộp'?styles.deny:styles.accept} disabled={busy || stale || !!read.data?.group_review_required || read.fetching || !!read.error || !read.data?.applicable || !!read.data?.reason_required && !reason.trim() || reason.length>500} onClick={()=>reason.trim()?confirm(read.data ?? undefined,reason.trim()):confirm(read.data ?? undefined)}>{read.data?`${verb} ${number(read.data.applicable)} cặp`:verb}</button>}</footer>
  </PairReviewDialog>;
}
