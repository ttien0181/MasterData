'use client';
import {useCallback,useState} from 'react';
import type {PairRead,ReviewCluster} from '@/lib/workflowContracts';
import {getReviewCluster} from '@/lib/workflowApi';
import {fieldLabels,decisionLabels,valueText} from '@/lib/pairReview';
import {pairEvidenceSummary} from '@/lib/pairEvidenceSummary';
import {useWorkflow} from './SessionWorkspace';
import {useSessionRead} from './useSessionRead';
import {ReadNotice} from './WorkflowReads';
import PairReviewDialog from './PairReviewDialog';
import PairComparison from './PairComparison';
import styles from './DecisionWorkspace.module.css';

const reasons:Record<string,string>={
 CLUSTER_REVIEW_TOO_LARGE:'Cụm vượt giới hạn đối chiếu cùng lúc; tiếp tục duyệt từng cặp.',
 CLUSTER_REVIEW_MISSING_RECORD:'Thiếu hồ sơ nguồn để đối chiếu đầy đủ.',
 CLUSTER_REVIEW_MISSING_PAIR:'Thiếu thông tin đối chiếu trực tiếp giữa một số thành viên; duyệt từng cặp.',
 CLUSTER_REVIEW_VETO:'Có liên kết bị từ chối hoặc bị quy định gộp chặn; duyệt từng cặp.',
 CLUSTER_REVIEW_WEAK_PAIR:'Có cặp chưa đủ thông tin đối chiếu nhận dạng; duyệt từng cặp.',
};
export default function PairClusterReview({pairId,close,accept,canWrite,reviewLargeGroup,acceptPair}:{pairId:string;close:()=>void;accept:(cluster:ReviewCluster)=>void;canWrite:boolean;reviewLargeGroup?:()=>void;acceptPair?:(pair:PairRead)=>void}) {
 const {workflow:w}=useWorkflow();
 const [comparison,setComparison]=useState<PairRead|null>(null),[evidenceOpen,setEvidenceOpen]=useState<boolean|null>(null);
 const load=useCallback((signal:AbortSignal)=>getReviewCluster(w.run_id,pairId,signal),[w.run_id,pairId]);
 const read=useSessionRead(w.run_id,['review-cluster',pairId,w.decision_revision,w.assessment_id],{},load,true,false,0);
 const data=read.data,stale=!!data&&(data.decision_revision!==w.decision_revision||data.assessment_id!==(w.assessment_id??null));
 const fields=['name','phone','email','dob','government_id'];
 const records=new Map(data?.records.map(r=>[r.record_id,r]));
 const names=new Map(data?.records.map(r=>{
  const identity=`${r.source_system} · ${r.external_id || r.record_id}`;
  return [r.record_id,r.raw_fields.name?`${valueText(r.raw_fields.name)} (${identity})`:identity];
 }));
 const sensitive=data?.pairs.some(p=>p.eligibility.reason_required||p.decision==='DEFERRED'||p.baseline.field_evidence.some(e=>e.state==='CF'));
 if(comparison)return <PairComparison pair={comparison} runId={w.run_id} close={()=>setComparison(null)}/>;
 return <PairReviewDialog title="Duyệt theo cụm" wide close={close}>
  <p>Đối chiếu mỗi hồ sơ một lần, rồi xác nhận các cặp còn chờ trong đúng cụm này.</p>
  {!data?<ReadNotice error={read.error} retry={read.retry}/>:<>
   <p>{data.records.length} hồ sơ · {data.pending_pair_ids.length} cặp cần duyệt. Các quyết định đã lưu được giữ nguyên.</p>
   <div className={styles.tableScroll}><table aria-label="Thành viên cụm"><thead><tr><th>Nguồn</th>{fields.map(f=><th key={f}>{fieldLabels[f]}</th>)}</tr></thead><tbody>{data.records.map(r=><tr key={r.record_id}><td>{r.source_system}<small>{r.external_id}</small></td>{fields.map(f=><td key={f}>{valueText(r.raw_fields[f]) || '—'}</td>)}</tr>)}</tbody></table></div>
   <details open={evidenceOpen??sensitive} onToggle={event=>setEvidenceOpen(event.currentTarget.open)}><summary>Thông tin đối chiếu {data.pairs.length} cặp</summary>{data.pairs.map(p=><div key={p.pair_id}><p>{names.get(p.baseline.left_id) || p.baseline.left_id} / {names.get(p.baseline.right_id) || p.baseline.right_id}: {pairEvidenceSummary(p.baseline)} · {decisionLabels[p.decision] || p.decision}</p><button aria-label={`Đối chiếu đầy đủ cặp ${p.pair_id}`} onClick={()=>setComparison({...p,left_record:records.get(p.baseline.left_id),right_record:records.get(p.baseline.right_id)})}>Đối chiếu đầy đủ</button>{!data.batch_allowed&&acceptPair&&['UNDECIDED','DEFERRED'].includes(p.decision)&&<button aria-label={`Đồng ý gộp riêng cặp ${p.pair_id}`} disabled={!canWrite||stale||read.fetching||!!read.error||!p.eligibility.manual_allowed} onClick={()=>acceptPair({...p,left_record:records.get(p.baseline.left_id),right_record:records.get(p.baseline.right_id)})}>Đồng ý gộp riêng cặp này</button>}</div>)}</details>
   {!!data.additional_evidence?.length&&<details><summary>Thông tin đối chiếu bổ sung đã tính từ hồ sơ nguồn</summary><p>Các cặp này được đối chiếu bổ sung, không tạo quyết định duyệt mới.</p>{data.additional_evidence.map(p=><p key={p.pair_id}>{names.get(p.baseline.left_id)} / {names.get(p.baseline.right_id)}: {pairEvidenceSummary(p.baseline)}</p>)}</details>}
   {data.blocked_reasons.map(reason=><p key={reason}>{reasons[reason] || reason}</p>)}
   {data.blocked_reasons.includes('CLUSTER_REVIEW_TOO_LARGE')&&reviewLargeGroup&&<button disabled={!canWrite||stale} onClick={reviewLargeGroup}>Xem trước để duyệt nhóm 21–100 thành viên</button>}
   {!data.batch_allowed&&acceptPair&&<p>Đối chiếu và Đồng ý gộp từng liên kết phù hợp trước. Khi các thành viên đã có liên kết được duyệt, tính lại xem trước để duyệt ngoại lệ cho toàn nhóm.</p>}
   {data.pairs.some(p=>p.eligibility.reason_required)&&<p>Có mâu thuẫn mềm; cần lý do đối chiếu khi Đồng ý gộp.</p>}
   {data.pairs.some(p=>p.decision==='DEFERRED')&&<p>Có cặp người dùng đã Hoãn; xác nhận sẽ xử lý cả các cặp đó.</p>}
   <p>Xác nhận áp dụng cho từng cặp được liệt kê. Nhóm cuối cùng vẫn được kiểm tra quy định gộp khi hoàn tất.</p>
  </>}
  {read.error&&data&&<ReadNotice error={read.error} retry={read.retry}/>}
  {stale&&<p role="alert">Quyết định hoặc đánh giá đã thay đổi. Đóng và mở lại cụm để đối chiếu.</p>}
  <footer><button onClick={close}>Đóng</button><button disabled={!canWrite||!data?.batch_allowed||stale||read.fetching||!!read.error} onClick={()=>{if(data)accept(data);}}>Đồng ý gộp {data?.pending_pair_ids.length ?? 0} cặp trong cụm</button></footer>
 </PairReviewDialog>;
}
