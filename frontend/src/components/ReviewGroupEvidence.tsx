'use client';
import {useCallback,useState} from 'react';
import {getReviewGroupEvidence} from '@/lib/workflowApi';
import {pairEvidenceSummary} from '@/lib/pairEvidenceSummary';
import {useSessionRead} from './useSessionRead';
import {ReadNotice} from './WorkflowReads';

export default function ReviewGroupEvidence({runId,operationId,fingerprint}:{runId:string;operationId:string;fingerprint:string}) {
 const [open,setOpen]=useState(false),[cursor,setCursor]=useState('0'),[history,setHistory]=useState<string[]>([]);
 const load=useCallback((signal:AbortSignal)=>getReviewGroupEvidence(runId,operationId,fingerprint,new URLSearchParams({cursor,limit:'25'}),signal),[runId,operationId,fingerprint,cursor]);
 const read=useSessionRead(runId,['review-group-evidence',operationId,fingerprint,cursor],{},load,open,false,0);
 const data=read.data;
 return <section aria-label="Thông tin đối chiếu nhóm đã đóng băng">
  <button onClick={()=>setOpen(value=>!value)}>{open?'Ẩn thông tin đối chiếu giữa các thành viên':'Xem thông tin đối chiếu giữa các thành viên'}</button>
  {open&&(!data?<ReadNotice error={read.error} retry={read.retry}/>:<>
   <p>Hiển thị {data.items.length} / {data.total} cặp</p>
   {data.items.map(pair=><p key={`${pair.left_id}:${pair.right_id}`}>{pair.left_id} / {pair.right_id}: {pairEvidenceSummary(pair)}{pair.computed_only?' · Thông tin đối chiếu bổ sung':''}</p>)}
   <nav aria-label="Trang thông tin đối chiếu"><button disabled={read.fetching||!history.length} onClick={()=>{setCursor(history[history.length-1]);setHistory(old=>old.slice(0,-1));}}>Trang thông tin đối chiếu trước</button><button disabled={read.fetching||!data.next_cursor} onClick={()=>{if(data.next_cursor){setHistory(old=>[...old,cursor]);setCursor(data.next_cursor);}}}>Trang thông tin đối chiếu tiếp</button></nav>
   {read.error&&<ReadNotice error={read.error} retry={read.retry}/>}
  </>)}
 </section>;
}
