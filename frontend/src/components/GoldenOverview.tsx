'use client';
import Link from 'next/link';
import {useEffect} from 'react';
import {useRouter} from 'next/navigation';
import {number} from '@/lib/pairReview';
import {useWorkflow,blockerLabel} from './SessionWorkspace';
import {useGoldenIndex} from './useGoldenIndex';
import {CachedReadStatus,ReadNotice} from './WorkflowReads';
import {useTabView} from './useTabView';
import styles from './GoldenPages.module.css';

export default function GoldenOverview(){
 const {workflow:w}=useWorkflow(),router=useRouter();
 const base=`/app/sessions/${encodeURIComponent(w.run_id)}/golden`;
 const read=useGoldenIndex('all','',null,1);
 const view=useTabView(w.run_id,'golden:overview',!!read.data);
 useEffect(()=>{const hash=window.location.hash;if(hash==='#golden-records')router.replace(`${base}/records`);else if(hash==='#golden-export')router.replace(`${base}/export`);},[base,router]);
 const summary=read.data?.summary,generation=read.data?.generation;
 return <section id="golden-overview" ref={view} className={styles.page}>
  <header className={styles.heading}><div><h2>Tổng quan &amp; tiến độ</h2><p>Kiểm tra tiến độ trước khi chốt dữ liệu.</p></div></header>
  {!read.ready?<div className={styles.card}>hồ sơ đại diện chưa sẵn sàng. Hãy tạo nhóm ở bước 3.</div>:!read.data?<ReadNotice error={read.error} retry={read.retry}/>:<>
   <CachedReadStatus read={read}/>
   <div className={styles.metrics}>
    {[{label:'Tổng hồ sơ đại diện',value:summary!.total,href:`${base}/records?status=all`,tone:''},{label:'Đã xác nhận',value:summary!.confirmed,href:`${base}/records?status=confirmed`,tone:styles.successMetric},{label:'Cần kiểm tra',value:summary!.needs_review,href:`${base}/records?status=review`,tone:styles.reviewMetric},{label:'Hồ sơ đầu vào',value:generation?.total_records??w.counts.records?.value,href:`/app/sessions/${w.run_id}/data/records`,tone:''}].map(item=><Link key={item.label} className={`${styles.metric} ${item.tone}`} href={item.href} aria-label={`${item.label}: ${item.value===undefined?'chưa có dữ liệu':number(item.value)}`}><span>{item.label}</span><strong>{item.value===undefined?'—':number(item.value)}</strong></Link>)}
   </div>
   <section className={styles.card}><div className={styles.progressHeading}><h3>Tiến độ xác nhận</h3><strong>{number(summary!.confirmed)} / {number(summary!.total)}</strong></div><progress className={styles.progress} aria-label="hồ sơ đại diện đã xác nhận" value={summary!.confirmed} max={Math.max(1,summary!.total)}/><div className={styles.nextAction}><p>{summary!.needs_review?`${number(summary!.needs_review)} hồ sơ đại diện đang chờ bạn kiểm tra.`:'Tất cả hồ sơ đại diện đã được xác nhận.'}</p><Link className={styles.primary} href={summary!.needs_review?`${base}/records?status=review`:`${base}/export`}>{summary!.needs_review?'Tiếp tục duyệt →':'Kiểm tra & xuất dữ liệu →'}</Link></div></section>
   {!!w.blockers.filter(b=>b.code!=='UNCONFIRMED_GOLDEN').length&&<div className={styles.notice}>{w.blockers.filter(b=>b.code!=='UNCONFIRMED_GOLDEN').map(b=><p key={b.code}>{blockerLabel(b.code)}{b.count===undefined?'':` (${number(b.count)})`}</p>)}</div>}
   {generation?.stale&&<div className={styles.notice}>Kết quả nhóm đã cũ. <Link href={`/app/sessions/${w.run_id}/clusters`}>Cập nhật nhóm</Link></div>}
   <div className={styles.steps}><Link className={styles.step} href={`${base}/records`}><span>1</span><div><strong>Duyệt giá trị</strong><small>So sánh với hồ sơ nguồn</small></div></Link><Link className={styles.step} href={`${base}/records?status=review`}><span>2</span><div><strong>Xác nhận hồ sơ đại diện</strong><small>Hoàn tất các mục cần kiểm tra</small></div></Link><Link className={styles.step} href={`${base}/export`}><span>3</span><div><strong>Chốt &amp; xuất CSV</strong><small>Kiểm tra và tải kết quả</small></div></Link></div>
  </>}
 </section>;
}
