'use client';
import {useCallback,useState} from 'react';
import {getConflicts} from '@/lib/workflowApi';
import type {PairRead} from '@/lib/workflowContracts';
import {reasonLabel,valueText,decisionLabels,reasons} from '@/lib/pairReview';
import {useWorkflow} from './SessionWorkspace';
import {useSessionRead} from './useSessionRead';
import {Pager,ReadNotice} from './WorkflowReads';
import PairReviewDialog from './PairReviewDialog';
import GroupApprovalDialog from './GroupApprovalDialog';
export default function PairConflicts({close,compare,amend,pending=0,attention=0}:{close:()=>void;compare:(id:string)=>void;amend:(pair:PairRead)=>void;pending?:number;attention?:number}) {
 const {workflow:w}=useWorkflow();const [cursor,setCursor]=useState<string|null>(null);
 const load=useCallback((signal:AbortSignal)=>getConflicts(w.run_id,new URLSearchParams({limit:'10',...(cursor?{cursor}:{})}),signal),[w.run_id,w.workflow_revision,w.decision_revision,cursor]);
 const read=useSessionRead(w.run_id,['pair-conflicts',w.workflow_revision,w.decision_revision],{cursor},load);
 const [approval,setApproval]=useState<string[]|null>(null);
 if(approval)return <GroupApprovalDialog recordIds={approval} close={()=>setApproval(null)} disabled={!!pending||!!attention} approved={()=>{setApproval(null);read.retry();}}/>;
 return <PairReviewDialog title="Xung đột cần đối chiếu" wide close={close}>{!read.data?<ReadNotice error={read.error} retry={read.retry}/>:<><p>{read.data.total} xung đột trong toàn phiên.</p>{read.data.items.map(c=><article className="workflow-panel" key={c.conflict_id}><h4>{reasons[c.reason] || c.reason || reasonLabel(c.reason)}</h4><p>{c.record_ids.join(' / ')} · {c.related_pair_count} cặp liên quan</p>{c.records.map(r=><p key={r.record_id}>{valueText(r.raw_fields.name)} · {r.source_system} · {r.record_id}</p>)}{c.reason==='CLUSTER_BRIDGE_MISSING_EVIDENCE'&&<button disabled={!!pending||!!attention||c.record_ids.length<2||c.record_ids.length>20||!w.allowed_actions.includes('approve-group')} onClick={()=>setApproval(c.record_ids)}>Duyệt ngoại lệ nhóm</button>}{c.pairs.map(p=><div key={p.pair_id}><span>{valueText(c.records.find(r=>r.record_id===p.baseline.left_id)?.raw_fields.name || p.baseline.left_id)} / {valueText(c.records.find(r=>r.record_id===p.baseline.right_id)?.raw_fields.name || p.baseline.right_id)} · {p.pair_id} · {decisionLabels[p.decision] || p.decision} </span><button aria-label={`Đối chiếu cặp ${p.pair_id}`} onClick={()=>compare(p.pair_id)}>Đối chiếu hai hồ sơ</button>{!['UNDECIDED','DEFERRED'].includes(p.decision)&&<button disabled={!w.allowed_actions.includes('amend-pair')} onClick={()=>amend(p)}>Sửa quyết định</button>}</div>)}</article>)}{!read.data.total&&<p>Không có xung đột.</p>}<Pager cursor={cursor} next={read.data.next_cursor} onPage={setCursor} busy={read.fetching}/></>}<footer><button onClick={close}>Đóng</button></footer></PairReviewDialog>;
}
