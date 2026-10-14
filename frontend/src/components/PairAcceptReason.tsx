'use client';
import {useState} from 'react';
import type {PairRead} from '@/lib/workflowContracts';
import PairReviewDialog from './PairReviewDialog';
import {useWorkflow} from './SessionWorkspace';
export default function PairAcceptReason({pair,close,confirm,disabled=false}:{pair:PairRead;close:()=>void;confirm:(reason:string)=>void;disabled?:boolean}){
 const [reason,setReason]=useState('');
 const {workflow:w}=useWorkflow();const [source]=useState({decision:w.decision_revision,assessment:w.assessment_id??null});
 const stale=source.decision!==w.decision_revision||source.assessment!==(w.assessment_id??null);
 return <PairReviewDialog title="Xác nhận Accept có mâu thuẫn" close={close}><p>{pair.left_record?.raw_fields.name?String(pair.left_record.raw_fields.name):pair.baseline.left_id} / {pair.right_record?.raw_fields.name?String(pair.right_record.raw_fields.name):pair.baseline.right_id}</p><p>Cặp có mâu thuẫn mềm. Ghi rõ bằng chứng đã đối chiếu trước khi xác nhận cùng thực thể. Các ràng buộc cứng vẫn được giữ.</p><label>Lý do Accept<textarea aria-label="Lý do Accept" maxLength={500} value={reason} onChange={event=>setReason(event.target.value)}/></label>{stale&&<p role="alert">Quyết định hoặc đánh giá đã thay đổi. Đóng hộp thoại và đối chiếu lại cặp.</p>}<footer><button onClick={close}>Hủy</button><button disabled={disabled||stale||!reason.trim()||reason.length>500} onClick={()=>confirm(reason.trim())}>Xác nhận Accept</button></footer></PairReviewDialog>;
}
