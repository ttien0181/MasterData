'use client';
import {useCallback,useEffect,useRef,useState} from 'react';
import {buildClusterQueue,getClusterQueue,getOperation,mutation} from '@/lib/workflowApi';
import {number} from '@/lib/pairReview';
import {useWorkflow} from './SessionWorkspace';
import {useWorkflowAction} from './useWorkflowAction';
import {useSessionRead} from './useSessionRead';
import {ReadNotice} from './WorkflowReads';
import type {OperationRead} from '@/lib/workflowContracts';
import Link from 'next/link';

export default function ClusterQueue(){
 const {workflow:w,refresh}=useWorkflow(),action=useWorkflowAction();
 const load=useCallback((signal:AbortSignal)=>getClusterQueue(w.run_id,signal),[w.run_id,w.decision_revision,w.cluster_revision]);
 const ready=['DECIDE_REVIEW','CLUSTER_QUEUED','CLUSTER_RUNNING','GOLDEN_REVIEW','FINALIZED'].includes(w.workflow_state);
 const queue=useSessionRead(w.run_id,['cluster-queue',w.decision_revision,w.cluster_revision],{},load,ready,true,Infinity);
 const [submitted,setSubmitted]=useState<OperationRead|null>(null),[pollError,setPollError]=useState('');
 const alive=useRef(true);
 useEffect(()=>{alive.current=true;return()=>{alive.current=false;};},[]);
 useEffect(()=>{
  if(!submitted||!['QUEUED','RUNNING'].includes(submitted.status))return;
  const timer=setTimeout(()=>{void getOperation(w.run_id,submitted.operation_id).then(async op=>{
   if(!alive.current)return;setSubmitted(op);setPollError('');
   if(!['QUEUED','RUNNING'].includes(op.status)){await refresh();queue.retry();}
  }).catch(()=>{if(alive.current)setPollError('Không đọc được tiến độ.');});},1800);
  return()=>clearTimeout(timer);
 },[submitted,pollError,w.run_id]);
 const active=w.active_operation||submitted&&['QUEUED','RUNNING'].includes(submitted.status)&&submitted;
 const count=queue.data?.pending_count ?? 0;
 const allowed=w.allowed_actions.includes('build-cluster-queue')&&!active&&!action.busy&&!queue.fetching&&!queue.previous&&!queue.error;
 async function build(mode:'pending'|'all'){
  const op=await action.run(()=>buildClusterQueue(w.run_id,{...mutation(w),expected_decision_revision:w.decision_revision,queue_mode:mode}));
  if(op)setSubmitted(op);
 }
 if(!ready)return null;
 return <section id="clusters-queue" className="workflow-panel" aria-label="Hàng đợi tạo nhóm">
  <div className="workflow-toolbar"><h3>Chờ tạo nhóm <strong>{queue.data?number(count):'—'}</strong></h3>
   <button title="Tạo nhóm từ Accept đang chờ; giữ các nhóm không bị ảnh hưởng" disabled={!allowed||!count} onClick={()=>void build('pending')}>Tạo nhóm mới</button>
   <button title="Tính lại toàn bộ Accept đã lưu và nhóm hiện có" disabled={!allowed||!queue.data} onClick={()=>void build('all')}>Cập nhật toàn bộ</button>
  </div>
  <p>Accept đã lưu được đưa vào hàng đợi. Cặp chưa duyệt tiếp tục giữ riêng.</p>
  {active&&<p role="status">Đang tạo nhóm…</p>}
  {submitted&&['FAILED','RETRYABLE_FAILED','CANCELLED'].includes(submitted.status)&&<p role="alert">Chưa tạo được nhóm: {submitted.error_code||submitted.status}. Các Accept vẫn được giữ.</p>}
  {submitted?.status==='COMPLETED'&&Number(submitted.result.blocker_count)>0&&<p role="alert">{number(Number(submitted.result.blocker_count))} cặp chưa tạo được nhóm. <Link href={`/app/sessions/${encodeURIComponent(w.run_id)}/decisions/pairs`}>Kiểm tra các cặp</Link></p>}
  {pollError&&<p role="alert">{pollError}<button onClick={()=>{setPollError('');setSubmitted(previous=>previous?{...previous}:previous);}}>Đọc lại</button></p>}
  {action.error&&<p role="alert">{action.error}</p>}{queue.error&&<ReadNotice error={queue.error} retry={queue.retry}/>}
 </section>;
}
