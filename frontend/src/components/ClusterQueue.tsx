'use client';
import {useCallback} from 'react';
import {getClusterQueue} from '@/lib/workflowApi';
import {number} from '@/lib/pairReview';
import {useWorkflow} from './SessionWorkspace';
import {useSessionRead} from './useSessionRead';
import {ReadNotice} from './WorkflowReads';
import Link from 'next/link';
import MetricHelp from './MetricHelp';
import styles from './ClusterQueue.module.css';

export default function ClusterQueue(){
 const {workflow:w}=useWorkflow();
 const load=useCallback((signal:AbortSignal)=>getClusterQueue(w.run_id,signal),[w.run_id,w.decision_revision,w.cluster_revision]);
 const ready=['DECIDE_REVIEW','CLUSTER_QUEUED','CLUSTER_RUNNING','GOLDEN_REVIEW','FINALIZED'].includes(w.workflow_state);
 const queue=useSessionRead(w.run_id,['cluster-queue',w.decision_revision,w.cluster_revision],{},load,ready,true,Infinity);
 const active=w.active_operation?.operation_type==='CLUSTER'?w.active_operation:null;
 const latest=w.latest_cluster_operation;
 const count=queue.data?.pending_count ?? 0;
 if(!ready)return null;
 return <section id="clusters-queue" className={`workflow-panel ${styles.queue}`} aria-label="Các cặp hồ sơ chờ tạo nhóm">
  <div className={styles.toolbar}><h3 data-bot-metric>Cặp đã xác nhận trùng chờ tạo nhóm <strong>{queue.data?number(count):'—'}</strong><MetricHelp label="Cặp đã xác nhận trùng chờ tạo nhóm" message={`Có ${queue.data?number(count):'chưa có số liệu về'} cặp đã đồng ý gộp đang chờ cập nhật vào nhóm khách hàng. Hệ thống sẽ tự xử lý ngầm; số này không phải số hồ sơ hoặc số khách hàng. Khi cập nhật xong, các cặp đó sẽ không còn nằm trong hàng chờ.`}/></h3>
  </div>
  {active&&<p role="status">{active.status==='QUEUED'?'Đang chờ tự động cập nhật nhóm hồ sơ…':'Đang tự động cập nhật nhóm hồ sơ…'}</p>}
  {latest&&['FAILED','RETRYABLE_FAILED','CANCELLED'].includes(latest.status)&&<p role="alert">Chưa cập nhật được nhóm hồ sơ. Các cặp đã xác nhận trùng vẫn được giữ. Bạn có thể thử lại tác vụ.</p>}
  {latest?.status==='COMPLETED'&&Number(latest.result.blocker_count)>0&&<p role="alert">{number(Number(latest.result.blocker_count))} cặp chưa tạo được nhóm. <Link href={`/app/sessions/${encodeURIComponent(w.run_id)}/decisions`}>Kiểm tra các cặp</Link></p>}
  {queue.error&&<ReadNotice error={queue.error} retry={queue.retry}/>}
 </section>;
}
