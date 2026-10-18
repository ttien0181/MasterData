'use client';
import Link from 'next/link';
import {useEffect,useEffectEvent} from 'react';
import {number,valueText,fieldLabels} from '@/lib/pairReview';
import type {GoldenIndexItem} from '@/lib/workflowContracts';
import {useWorkflow} from './SessionWorkspace';
import {useGoldenIndex} from './useGoldenIndex';
import {useStoredState} from './SessionCache';
import {useTabView} from './useTabView';
import {CachedReadStatus,ReadNotice} from './WorkflowReads';
import GoldenReviewDialog from './GoldenReviewDialog';
import GoldenResolution from './GoldenResolution';
import styles from './GoldenPages.module.css';

type QueryState={status:string;q:string;index:number;pages:{cursor:string|null;offset:number}[]};
export default function GoldenReview({initialStatus,initialClusterId}:{initialStatus?:string;initialClusterId?:string}){
 const {workflow:w}=useWorkflow();
 const base=`/app/sessions/${encodeURIComponent(w.run_id)}/golden`;
 const [query,setQuery]=useStoredState<QueryState>(w.run_id,`golden:${w.cluster_revision}:review-query`,{status:initialStatus||(w.workflow_state==='FINALIZED'?'all':'review'),q:'',index:0,pages:[{cursor:null,offset:0}]});
 const [search,setSearch]=useStoredState(w.run_id,'golden:search-input',query.q);
 const [open,setOpen]=useStoredState(w.run_id,`golden:${w.cluster_revision}:open`,initialClusterId||'');
 const [reviewQueue,setReviewQueue]=useStoredState<GoldenIndexItem[]>(w.run_id,`golden:${w.cluster_revision}:review-queue`,[]);
 const [drafts]=useStoredState<Record<string,string>>(w.run_id,`clusters:${w.cluster_revision}:draft-groups`,{});
 const {cursor,offset}=query.pages[query.index];
 const read=useGoldenIndex(query.status,query.q,cursor);
 const view=useTabView(w.run_id,'golden:records',!!read.data);
 function filter(status:string,q=query.q){setQuery({status,q,index:0,pages:[{cursor:null,offset:0}]});}
 const applyStatus=useEffectEvent(()=>{if(initialStatus&&initialStatus!==query.status)filter(initialStatus);});
 useEffect(()=>{applyStatus();},[initialStatus]);
 const applyCluster=useEffectEvent(()=>{if(initialClusterId)setOpen(initialClusterId);});
 useEffect(()=>{applyCluster();},[initialClusterId]);
 function select(id:string,keepQueue=false){if(id&&!keepQueue&&read.data?.items.some(item=>item.cluster_id===id))setReviewQueue(read.data.items);setOpen(id);const url=new URL(window.location.href);if(id)url.searchParams.set('cluster',id);else url.searchParams.delete('cluster');window.history.replaceState(null,'',url);}
 const current=read.data?.items.find(item=>item.cluster_id===open)||reviewQueue.find(item=>item.cluster_id===open);
 const currentIndex=reviewQueue.findIndex(item=>item.cluster_id===open);
 const following=currentIndex>=0?reviewQueue[currentIndex+1]:undefined;
 const sum=read.data?.summary;
 return <section id="golden-records" ref={view} className={styles.page}>
  <header className={styles.heading}><div><h2>Duyệt hồ sơ đại diện</h2><p>Kiểm tra giá trị, xem nguồn và xác nhận từng hồ sơ đại diện.</p></div><Link className="workflow-button" href={`${base}/export`}>Đến bước chốt &amp; xuất →</Link></header>
  {!!Object.keys(drafts).length&&<aside className={styles.notice}><p>Bản nháp chưa lưu</p>{Object.entries(drafts).map(([id,name])=><button key={id} onClick={()=>select(id)}>Mở lại bản nháp nhóm {name}</button>)}</aside>}
  {!read.ready?<div className={styles.card}>hồ sơ đại diện chưa sẵn sàng. Hãy tạo nhóm ở bước 3.</div>:<>
   <section className={styles.list} aria-busy={read.fetching}>
    <div className={styles.toolbar}><div className={styles.filters} role="group" aria-label="Trạng thái hồ sơ đại diện">{[{key:'review',label:'Cần kiểm tra',count:sum?.needs_review},{key:'all',label:'Tất cả',count:sum?.total},{key:'confirmed',label:'Đã xác nhận',count:sum?.confirmed}].map(tab=><button key={tab.key} aria-pressed={query.status===tab.key} onClick={()=>filter(tab.key)}>{tab.label}{tab.count===undefined?'':` (${number(tab.count)})`}</button>)}</div>
     <form className={styles.search} onSubmit={event=>{event.preventDefault();filter(query.status,search.trim());}}><input type="search" aria-label="Tìm hồ sơ đại diện" placeholder="Tên, điện thoại hoặc email…" maxLength={200} value={search} onChange={event=>setSearch(event.target.value)}/><button type="submit">Tìm</button></form>
    </div><CachedReadStatus read={read}/>
    {!read.data?<ReadNotice error={read.error} retry={read.retry}/>:<>
     <div className={styles.tableScroll}><table className={`${styles.table} ${styles.reviewTable}`} aria-label="Danh sách hồ sơ đại diện"><thead><tr><th>hồ sơ đại diện</th><th>Liên hệ</th><th>Hồ sơ nguồn</th><th>Trạng thái</th><th>Thao tác</th></tr></thead><tbody>{read.data.items.map(item=><tr key={item.cluster_id}><th scope="row">{item.display_name}<span className={styles.mobileStatus}><span className={`${styles.badge} ${item.status==='DRAFT'?styles.draft:styles.confirmed}`}>{item.status==='DRAFT'?'Cần kiểm tra':item.status==='FINALIZED'?'Đã chốt':'Đã xác nhận'}</span></span>{item.review_fields.length>0&&<small>Cần chọn: {item.review_fields.map(field=>fieldLabels[field]||field).join(', ')}</small>}</th><td>{valueText(item.phone)}<small>{valueText(item.email)}</small></td><td>{number(item.member_count)}</td><td><span className={`${styles.badge} ${item.status==='DRAFT'?styles.draft:styles.confirmed}`}>{item.status==='DRAFT'?'Cần kiểm tra':item.status==='FINALIZED'?'Đã chốt':'Đã xác nhận'}</span></td><td><button disabled={read.previous||read.fetching} aria-label={`Xem và duyệt ${item.display_name}`} onClick={()=>select(item.cluster_id)}>Xem &amp; duyệt →</button></td></tr>)}</tbody></table></div>
     {!read.data.items.length&&<div className={styles.empty}>{query.q?'Không tìm thấy hồ sơ đại diện phù hợp.':query.status==='review'?'Không còn hồ sơ đại diện cần kiểm tra.':'Không có hồ sơ đại diện trong bộ lọc này.'}<p><button onClick={()=>{setSearch('');filter('all','');}}>Xem tất cả hồ sơ đại diện</button></p></div>}
     <footer className={styles.pager}><span aria-live="polite">{read.fetching?'Đang tải…':read.previous?'Chưa tải được trang đã chọn':`Hiển thị ${number(read.data.items.length?offset+1:0)}–${number(offset+read.data.items.length)} / ${number(read.data.total)} hồ sơ đại diện`}</span><nav aria-label="Phân trang hồ sơ đại diện"><button disabled={read.fetching||query.index===0} onClick={()=>setQuery(previous=>({...previous,index:previous.index-1}))}>Trang trước</button><strong>Trang {number(query.index+1)}</strong><button disabled={read.fetching||read.previous||!read.data.next_cursor} onClick={()=>{if(!read.data?.next_cursor)return;const next={cursor:read.data.next_cursor,offset:offset+read.data.items.length};setQuery(previous=>({...previous,index:previous.index+1,pages:[...previous.pages.slice(0,previous.index+1),next]}));}}>Trang tiếp</button></nav></footer>
    </>}
   </section>
   {w.allowed_actions.includes('resolve-golden')&&<details className={styles.advanced}><summary>Công cụ hỗ trợ xử lý hồ sơ đại diện</summary><GoldenResolution dirty={!!Object.keys(drafts).length} readOnly={!!w.active_operation||!!read.data?.generation?.stale||!!read.data?.generation?.read_only}/></details>}
  </>}
  {open&&<GoldenReviewDialog key={`${w.cluster_revision}:${open}`} id={open} name={current?.display_name||drafts[open]||open} close={()=>select('')} next={following?()=>select(following.cluster_id,true):undefined}/>}
 </section>;
}
