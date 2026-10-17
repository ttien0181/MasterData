'use client';
import {useCallback} from 'react';
import {confirmGolden,getCluster,getClusterGolden,goldenRevision,saveGolden} from '@/lib/workflowApi';
import {useWorkflow} from './SessionWorkspace';
import {useSessionRead} from './useSessionRead';
import {useWorkflowAction} from './useWorkflowAction';
import {useStoredState} from './SessionCache';
import {CachedReadStatus,ReadNotice,Pager} from './WorkflowReads';
import ClusterDetailDialog from './ClusterDetailDialog';
import GoldenFieldEditor from './GoldenFieldEditor';
import GroupMembersTable from './GroupMembersTable';
import styles from './GoldenPages.module.css';

export default function GoldenReviewDialog({id,name,close,next}:{id:string;name:string;close:()=>void;next?:()=>void}){
 const {workflow:w}=useWorkflow(),action=useWorkflowAction();
 const [dirty,setDirty]=useStoredState(w.run_id,`clusters:${id}:dirty`,false);
 const [,setDraftGroups]=useStoredState<Record<string,string>>(w.run_id,`clusters:${w.cluster_revision}:draft-groups`,{});
 const [cursor,page]=useStoredState<string|null>(w.run_id,`golden:${w.cluster_revision}:${id}:members`,null);
 const detailLoad=useCallback((signal:AbortSignal)=>getCluster(w.run_id,id,new URLSearchParams(cursor?{cursor}:{}),signal),[w.run_id,id,cursor,w.cluster_revision,w.golden_revision,w.decision_revision]);
 const detail=useSessionRead(w.run_id,['golden-members',id,w.cluster_revision,w.golden_revision,w.decision_revision],{cursor},detailLoad,true,true,Infinity);
 const goldenLoad=useCallback((signal:AbortSignal)=>getClusterGolden(w.run_id,id,signal),[w.run_id,id,w.cluster_revision,w.golden_revision,w.decision_revision,w.workflow_state]);
 const golden=useSessionRead(w.run_id,['cluster-golden',id,w.cluster_revision,w.golden_revision,w.decision_revision,w.workflow_state],{},goldenLoad,true,false,Infinity);
 const updateDirty=useCallback((value:boolean)=>{
  setDirty(value);setDraftGroups(previous=>{if(value&&previous[id]===name||!value&&!(id in previous))return previous;const result={...previous};if(value)result[id]=name;else delete result[id];return result;});
 },[id,name,setDirty,setDraftGroups]);
 const generations=[w.cluster_result,golden.data?.generation,detail.data?.generation].filter(Boolean);
 const locked=!!w.active_operation||golden.fetching||golden.previous||!!golden.error||detail.previous||!!detail.error||generations.some(g=>g?.stale||g?.read_only)||!!golden.data?.read_only;
 const editable=!locked&&w.allowed_actions.includes('save-golden');
 const confirmed=golden.data?.status==='CONFIRMED'||golden.data?.status==='FINALIZED';
 function requestClose(){if(action.busy)return;if(dirty&&!window.confirm('Đóng popup khi chưa lưu? Bản nháp được giữ để tiếp tục sau.'))return;close();}
 return <ClusterDetailDialog title={`Duyệt Golden · ${name}`} close={requestClose} busy={action.busy} dirty={dirty}>
  {action.error&&<p role="alert" className={styles.error}>{action.error}</p>}
  <section className="workflow-panel"><div className={styles.heading}><h3>Đối chiếu với nguồn</h3><span className={`${styles.badge} ${confirmed?styles.confirmed:styles.draft}`}>{confirmed?'Đã xác nhận':'Cần kiểm tra'}</span></div>
   {!detail.data?<ReadNotice error={detail.error} retry={detail.retry}/>:<><CachedReadStatus read={detail}/><GroupMembersTable key={`${id}:${cursor||'0'}`} members={detail.data.members.items.map(record=>({record_id:record.record_id,source_system:record.source_system,external_id:record.external_id,name:record.raw_fields.name,fields:record.raw_fields}))} golden={golden.data?.fields} goldenLabel="Golden đang chọn"/>{(cursor||detail.data.members.next_cursor)&&<Pager cursor={cursor} next={detail.data.members.next_cursor} onPage={page} busy={detail.fetching}/>}</>}
  </section>
  {golden.data?<><CachedReadStatus read={golden}/><GoldenFieldEditor compact runId={w.run_id} golden={golden.data} busy={action.busy} editable={editable} onDirtyChange={updateDirty} onSave={(choices,clusterRevision,goldenRevisionValue)=>action.run(()=>saveGolden(w.run_id,id,{...goldenRevision(w),expected_cluster_revision:clusterRevision,expected_golden_revision:goldenRevisionValue,selections:choices}))}/></>:<ReadNotice error={golden.error} retry={golden.retry}/>}
  <footer className={styles.popupActions}><span className={styles.muted}>{dirty?'Có thay đổi chưa lưu':confirmed?'Golden đã xác nhận':'Lưu thay đổi trước khi xác nhận'}</span><div><button disabled={action.busy||dirty||locked||!golden.data||confirmed||!w.allowed_actions.includes('confirm-golden')} onClick={()=>void action.run(()=>confirmGolden(w.run_id,id,goldenRevision(w)))}>Xác nhận Golden</button>{next&&<button disabled={action.busy||dirty} onClick={next}>Nhóm tiếp theo →</button>}</div></footer>
 </ClusterDetailDialog>;
}
