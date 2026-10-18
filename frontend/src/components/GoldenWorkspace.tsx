'use client';
import {useCallback,useState} from 'react';
import {exportSelectedGolden,getClusterGolden,goldenRevision} from '@/lib/workflowApi';
import {fieldLabels,number,valueText} from '@/lib/pairReview';
import {useAppUser} from './AppSession';
import {useWorkflow} from './SessionWorkspace';
import {useGoldenIndex} from './useGoldenIndex';
import {usePageSelection} from './usePageSelection';
import {useSessionRead} from './useSessionRead';
import {useStoredState} from './SessionCache';
import {useWorkflowAction} from './useWorkflowAction';
import {useTabView} from './useTabView';
import {CachedReadStatus,ReadNotice} from './WorkflowReads';
import PageCheckbox from './PageCheckbox';
import PairReviewDialog from './PairReviewDialog';
import MetricHelp from './MetricHelp';
import {metricNumber,metricPercent,metricExplanation as explain} from '@/lib/metricInsights';
import styles from './GoldenPages.module.css';

type Pages={index:number;pages:{cursor:string|null;offset:number}[]};
export default function GoldenWorkspace({initialClusterId=''}:{initialClusterId?:string}) {
 const {workflow:w}=useWorkflow(),user=useAppUser(),action=useWorkflowAction();
 const identity=JSON.stringify([w.run_id,w.cluster_revision,w.golden_revision,w.decision_revision]);
 const [page,setPage]=useStoredState<Pages>(w.run_id,`golden:export-pages:${identity}`,{index:0,pages:[{cursor:null,offset:0}]});
 const {cursor,offset}=page.pages[page.index];
 const read=useGoldenIndex('all','',cursor),selection=usePageSelection({},identity,'golden-export');
 const [open,setOpen]=useState(initialClusterId),[downloaded,setDownloaded]=useState(false);
 const view=useTabView(w.run_id,'golden:workspace',!!read.data);
 const generation=read.data?.generation,total=read.data?.total??0;
 const count=selection.all?Math.max(0,total-selection.count):selection.count;
 const locked=action.busy||!!w.active_operation||!read.data||read.fetching||read.previous||!!read.error||!!generation?.stale;
 const canExport=user.capabilities.includes('export_data');
 const items=read.data?.items??[];
 async function download(){
  if(locked||!canExport||!count)return;
  setDownloaded(false);
  const result=await action.run(()=>exportSelectedGolden(w.run_id,{...goldenRevision(w),...(selection.all?{excluded_cluster_ids:selection.ids}:{cluster_ids:selection.ids})}));
  if(result){setDownloaded(true);window.dispatchEvent(new CustomEvent('demo:completed',{detail:'golden-export'}));}
 }
 return <section id="golden-overview" ref={view} className={styles.page}>
  <header className={styles.heading}><div><h1>Hồ sơ đại diện</h1><p>Xem lại hồ sơ đại diện của khách hàng và chọn dữ liệu để xuất CSV.</p></div></header>
  {!read.ready?<div className={styles.card}>Chưa có hồ sơ đại diện. Hãy hoàn tất nhóm khách hàng ở bước 3.</div>:!read.data?<ReadNotice error={read.error} retry={read.retry}/>:<>
   <CachedReadStatus read={read}/>
   <div className={`${styles.metrics} ${styles.overviewMetrics}`}>
    {[
     ['Hồ sơ đại diện',number(total),'Số hồ sơ đại diện trong kết quả nhóm hiện tại. Mỗi hồ sơ đại diện giữ các giá trị đã lưu ở bước 3; số này chưa bảo đảm toàn bộ dữ liệu nguồn đã được xử lý.'],
     ['Hồ sơ nguồn đã tổng hợp',metricNumber(generation?.covered_records),'Số hồ sơ nguồn đóng góp vào các nhóm đang công bố. Các hồ sơ chưa có nhóm chưa được tính vào chỉ số này.'],
     ['Tỷ lệ giảm số hồ sơ',metricPercent(generation?generation.covered_records-total:undefined,generation?.covered_records),'Lấy số hồ sơ nguồn đã tổng hợp trừ số hồ sơ đại diện, rồi chia cho số hồ sơ nguồn đã tổng hợp. Tỷ lệ phản ánh mức gọn của dữ liệu sau gộp, không phải tỷ lệ lỗi hay số tiền tiết kiệm.'],
     ['Đã chọn để xuất',number(count),'Số hồ sơ đại diện sẽ có trong tệp CSV nếu bạn bấm xuất lúc này. Chọn tất cả áp dụng cho toàn bộ danh sách qua các trang và trừ những bản bạn đã bỏ chọn.'],
    ].map(([label,value,meaning])=><div data-bot-metric key={label} className={styles.metric}><span>{label}</span><strong>{value}</strong><MetricHelp label={label} message={explain(label,value,meaning)}/></div>)}
   </div>
   {generation?.stale&&<p role="status" className={styles.notice}>Nhóm khách hàng đang cần cập nhật. Bạn có thể xem lại dữ liệu và xuất CSV sau khi cập nhật xong.</p>}
   {action.error&&<p role="alert" className={styles.error}>{action.error}</p>}
   {downloaded&&<p role="status" className={styles.success}>Đã gửi tệp CSV đến trình duyệt.</p>}
   <section className={styles.list} aria-label="Danh sách Hồ sơ đại diện" aria-busy={read.fetching}>
    <div className={styles.toolbar}><div className={styles.selectionActions}>
     <button data-demo="golden-select" disabled={locked||!total} onClick={()=>{selection.selectAll();setDownloaded(false);}}>Chọn tất cả ({number(total)})</button>
     <button disabled={locked||!count} onClick={()=>{selection.clear();setDownloaded(false);}}>Bỏ chọn</button>
     <strong className={styles.selectionCount} aria-live="polite">Đã chọn {number(count)} / {number(total)}</strong>
    </div><button data-demo="golden-export" className={styles.primary} disabled={locked||!canExport||!count} onClick={()=>void download()}>Xuất CSV ({number(count)})</button></div>
    <div className={styles.tableScroll}><table className={`${styles.table} ${styles.exportTable}`} aria-label="Hồ sơ đại diện để xuất CSV"><thead><tr><th><fieldset className={styles.selectionField} disabled={locked}><PageCheckbox ids={items.map(item=>item.cluster_id)} selected={selection.selected} onSelect={selection.selectPage} onDeselect={selection.deselectPage}/></fieldset></th><th>Khách hàng</th><th>Điện thoại</th><th>Email</th><th>Hồ sơ nguồn</th><th>Chi tiết</th></tr></thead><tbody>{items.map(item=><tr key={item.cluster_id} data-selected={selection.selected(item.cluster_id)}>
     <td><input type="checkbox" aria-label={`Chọn ${item.display_name}`} checked={selection.selected(item.cluster_id)} disabled={locked} onChange={()=>{selection.toggle(item.cluster_id);setDownloaded(false);}}/></td>
     <th scope="row">{item.display_name}</th><td>{valueText(item.phone)}</td><td>{valueText(item.email)}</td><td>{number(item.member_count)}</td>
     <td><button data-demo="golden-details" aria-label={`Xem chi tiết ${item.display_name}`} disabled={read.previous||read.fetching} onClick={()=>setOpen(item.cluster_id)}>Xem chi tiết</button></td>
    </tr>)}</tbody></table></div>
    {!items.length&&<p className={styles.empty}>Chưa có hồ sơ đại diện.</p>}
    <footer className={styles.pager}><span>{read.fetching?'Đang tải…':`Hiển thị ${number(items.length?offset+1:0)}–${number(offset+items.length)} / ${number(total)} Hồ sơ đại diện`}</span>
     <nav aria-label="Phân trang Hồ sơ đại diện"><button disabled={read.fetching||page.index===0} onClick={()=>setPage(previous=>({...previous,index:previous.index-1}))}>Trang trước</button><strong>Trang {number(page.index+1)}</strong><button disabled={read.fetching||read.previous||!!read.error||!read.data.next_cursor} onClick={()=>{if(!read.data?.next_cursor)return;setPage(previous=>({...previous,index:previous.index+1,pages:[...previous.pages.slice(0,previous.index+1),{cursor:read.data!.next_cursor,offset:offset+items.length}]}));}}>Trang tiếp</button></nav>
    </footer>
   </section>
  </>}
  {open&&<GoldenDetails key={`${identity}:${open}`} id={open} name={items.find(item=>item.cluster_id===open)?.display_name??'Khách hàng'} close={()=>setOpen('')}/>}
 </section>;
}
function GoldenDetails({id,name,close}:{id:string;name:string;close:()=>void}) {
 const {workflow:w}=useWorkflow();
 const load=useCallback((signal:AbortSignal)=>getClusterGolden(w.run_id,id,signal),[w.run_id,id,w.cluster_revision,w.golden_revision]);
 const read=useSessionRead(w.run_id,['golden-export-detail',id,w.cluster_revision,w.golden_revision],{},load,true,false,Infinity);
 return <PairReviewDialog title={`Hồ sơ đại diện · ${name}`} close={close}>
  {!read.data?<ReadNotice error={read.error} retry={read.retry}/>:<><CachedReadStatus read={read}/><dl className={styles.recordDetails}>{Object.entries(read.data.fields).map(([key,field])=><div key={key}><dt>{fieldLabels[key]??key}</dt><dd>{valueText(field.value)}</dd></div>)}</dl></>}
  <footer><button onClick={close}>Đóng</button></footer>
 </PairReviewDialog>;
}
