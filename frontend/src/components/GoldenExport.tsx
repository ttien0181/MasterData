'use client';
import Link from 'next/link';
import {useCallback} from 'react';
import {downloadResolutionExport,executeResolutionPreview,getResolutionExports} from '@/lib/api';
import {finalizeGolden,mutation,prepareWorkflowExport,reopenDecisions} from '@/lib/workflowApi';
import {number} from '@/lib/pairReview';
import {useWorkflow,blockerLabel} from './SessionWorkspace';
import {useWorkflowAction} from './useWorkflowAction';
import {useGoldenIndex} from './useGoldenIndex';
import {useSessionRead} from './useSessionRead';
import {useStoredState} from './SessionCache';
import {ReadNotice,CachedReadStatus} from './WorkflowReads';
import {useTabView} from './useTabView';
import styles from './GoldenPages.module.css';

type ExportState={preview:Awaited<ReturnType<typeof prepareWorkflowExport>>|null;requestId:string;status:'idle'|'failed'|'success'};
export default function GoldenExport(){
 const {workflow:w}=useWorkflow(),action=useWorkflowAction(),read=useGoldenIndex('all','',null,1);
 const [draftGroups]=useStoredState<Record<string,string>>(w.run_id,`clusters:${w.cluster_revision}:draft-groups`,{});
 const [state,setState]=useStoredState<ExportState>(w.run_id,`golden:export:${w.cluster_revision}:${w.golden_revision}:${w.decision_revision}:${w.workflow_revision}:${w.workflow_state}`,{preview:null,requestId:'',status:'idle'});
 const [reason,setReason]=useStoredState(w.run_id,'golden:reopen-reason','');
 const historyLoad=useCallback(()=>getResolutionExports(w.run_id),[w.run_id]);
 const history=useSessionRead(w.run_id,['golden-export-history',w.golden_revision,w.workflow_state],{},historyLoad,read.ready);
 const view=useTabView(w.run_id,'golden:export',!!read.data);
 const base=`/app/sessions/${encodeURIComponent(w.run_id)}/golden`;
 const generation=read.data?.generation||w.cluster_result;
 const valid=!!generation&&!generation.stale&&generation.publication_status==='COMPLETE'&&generation.covered_records===generation.total_records;
 const hasDrafts=Object.keys(draftGroups).length>0;
 const locked=!!w.active_operation||!read.data||read.fetching||read.previous||!!read.error||!valid||hasDrafts;
 const finalized=w.workflow_state==='FINALIZED';
 const canFinalize=!locked&&!generation?.read_only&&!read.data?.summary.needs_review&&w.allowed_actions.includes('finalize-golden');
 const canExport=finalized&&!locked&&w.allowed_actions.includes('export');
 const expired=!!state.preview?.expires_at&&Date.parse(state.preview.expires_at)<=Date.now();
 async function preview(){const result=await action.run(()=>prepareWorkflowExport(w.run_id));if(result)setState({preview:result,requestId:crypto.randomUUID(),status:'idle'});}
 async function execute(){
  if(!state.preview||expired)return;
  const done=await action.run(async()=>{await executeResolutionPreview(w.run_id,state.preview!.preview_id,state.requestId);return true;});
  setState(previous=>({...previous,status:done?'success':'failed'}));history.retry();
 }
 return <section id="golden-export" ref={view} className={styles.page}>
  <header className={styles.heading}><div><h2>Chốt &amp; xuất dữ liệu</h2><p>Hoàn tất phiên và tải CSV kết quả.</p></div><Link className="workflow-button" href={`${base}/records`}>Quay lại duyệt</Link></header>
  {!read.ready?<div className={styles.card}>Chưa có Golden để chốt.</div>:<>
   <div className={styles.exportStatus}><span className={`${styles.badge} ${finalized?styles.confirmed:styles.draft}`}>{finalized?'Phiên đã chốt':'Phiên đang duyệt'}</span><span className={`${styles.badge} ${state.status==='success'?styles.confirmed:styles.draft}`}>{state.status==='success'?'CSV đã tạo':state.status==='failed'?'CSV xuất thất bại':'Chưa xuất CSV trong lượt này'}</span></div>
   {!read.data?<ReadNotice error={read.error} retry={read.retry}/>:<CachedReadStatus read={read}/>}
   {hasDrafts&&<div className={styles.notice}>Còn bản nháp chưa lưu. <Link href={`${base}/records`}>Mở lại để hoàn tất</Link></div>}
   {action.error&&<div role="alert" className={styles.error}>{state.status==='failed'&&<strong>Xuất CSV thất bại</strong>}<p>{action.error}</p></div>}
   {state.status==='failed'&&!action.error&&<div className={styles.error} role="alert">Xuất CSV thất bại</div>}
   {state.status==='success'&&<div className={styles.success} role="status"><strong>Đã tạo CSV thành công</strong><p>Yêu cầu tải file đã được gửi đến trình duyệt.</p></div>}
   <div className={styles.exportGrid}>
    <section className={styles.card}><div className={styles.stageHeading}><span className={styles.stepNumber}>1</span><h3>Kiểm tra trước khi chốt</h3></div><ul className={styles.checklist}>
     <li><span>Golden đã xác nhận</span><strong className={read.data?.summary.needs_review?styles.pending:undefined}>{read.data?`${number(read.data.summary.confirmed)} / ${number(read.data.summary.total)}`:'Đang kiểm tra…'}</strong></li>
     <li><span>Bản ghi đã có kết quả</span><strong className={!valid?styles.pending:undefined}>{generation?`${number(generation.covered_records)} / ${number(generation.total_records)}`:'Chưa có kết quả'}</strong></li>
     <li><span>Phiên bản dữ liệu</span><strong className={!generation||generation.stale?styles.pending:undefined}>{!generation?'Chưa có':generation.stale?'Cần cập nhật':'Mới nhất'}</strong></li>
     <li><span>Bản nháp chưa lưu</span><strong className={hasDrafts?styles.pending:undefined}>{number(Object.keys(draftGroups).length)}</strong></li>
    </ul>{w.blockers.map(b=><p className={styles.muted} key={b.code}>{blockerLabel(b.code)}{b.count===undefined?'':`: ${number(b.count)}`}</p>)}{!!read.data?.summary.needs_review&&<Link href={`${base}/records?status=review`}>Xử lý Golden còn lại →</Link>}</section>
    <section className={styles.card}>
     <div className={styles.stageHeading}><span className={styles.stepNumber}>2</span><h3>Chốt phiên</h3></div><p className={styles.muted}>{finalized?'Dữ liệu Golden đã được khóa cho phiên này.':'Chốt sau khi hoàn tất các điều kiện bên trái.'}</p><p><button className={styles.primary} disabled={finalized||action.busy||!canFinalize} onClick={()=>{if(window.confirm('Chốt phiên và khóa dữ liệu Golden hiện tại?'))void action.run(()=>finalizeGolden(w.run_id,{...mutation(w),expected_golden_revision:w.golden_revision}));}}>{finalized?'Đã chốt phiên':'Chốt phiên'}</button></p>
     {!finalized&&!canFinalize&&!w.blockers.length&&<p className={styles.muted}>{locked?'Hoàn tất kiểm tra dữ liệu và bản nháp trước khi chốt.':'Tài khoản hoặc trạng thái phiên chưa cho phép chốt.'}</p>}
     <div className={styles.exportStage}><div className={styles.stageHeading}><span className={styles.stepNumber}>3</span><h3>Xuất CSV</h3></div>
      {!state.preview?<><p>Kiểm tra số dòng trước khi tạo file.</p><button disabled={action.busy||!canExport} onClick={()=>void preview()}>Xem trước CSV</button></>:<><p><strong>{number(state.preview.estimated_output_rows)} dòng</strong> sẽ được xuất.</p><button className={styles.primary} disabled={action.busy||!canExport||expired} onClick={()=>void execute()}>{action.busy?'Đang xử lý…':state.status==='failed'?'Thử xuất lại':'Xuất CSV'}</button> <button disabled={action.busy||!canExport} onClick={()=>void preview()}>Xem trước lại</button>{expired&&<p>Bản xem trước đã hết hạn. Hãy xem trước lại.</p>}</>}
      {!finalized&&<p className={styles.muted}>Chốt phiên để mở chức năng xuất.</p>}
     </div>
    </section>
   </div>
   <section className={styles.card}><h3>File đã xuất</h3>{!history.data?<ReadNotice error={history.error} retry={history.retry}/>:<><CachedReadStatus read={history}/>{!history.data.items.length?<p className={styles.muted}>Chưa có file CSV được lưu thành công.</p>:<div className={styles.tableScroll}><table className={`${styles.table} ${styles.historyTable}`} aria-label="File CSV đã xuất"><thead><tr><th>Thời gian</th><th>Số dòng</th><th>Thao tác</th></tr></thead><tbody>{history.data.items.map(item=><tr key={item.id}><td>{new Date(item.created_at).toLocaleString('vi-VN',{timeZone:'Asia/Bangkok'})}</td><td>{number(item.row_count)}</td><td><button disabled={action.busy||!w.allowed_actions.includes('export')} onClick={()=>void action.run(async()=>{await downloadResolutionExport(w.run_id,item.id);return true;})}>Tải CSV</button></td></tr>)}</tbody></table></div>}</>}</section>
   {w.allowed_actions.includes('reopen-decisions')&&<details className={styles.advanced}><summary>Thao tác nâng cao</summary><p>Mở lại phân loại để sửa quyết định. File đã xuất vẫn được giữ.</p><label>Lý do mở lại<input aria-label="Lý do mở lại" value={reason} onChange={event=>setReason(event.target.value)}/></label> <button disabled={action.busy||hasDrafts||!reason.trim()} onClick={()=>void action.run(()=>reopenDecisions(w.run_id,{...mutation(w),reason:reason.trim()}))}>Mở lại phân loại</button></details>}
  </>}
 </section>;
}
