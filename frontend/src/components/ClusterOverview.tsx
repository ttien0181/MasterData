'use client';
import Link from 'next/link';
import { useEffect } from 'react';
import { useRouter } from 'next/navigation';
import { sessionPath } from '@/lib/workflowNavigation';
import { number } from '@/lib/pairReview';
import { useWorkflow } from './SessionWorkspace';
import { useTabView } from './useTabView';
import ClusterQueue from './ClusterQueue';
import GenerationStatus from './GenerationStatus';
import styles from './ClusterOverview.module.css';

export default function ClusterOverview() {
  const { workflow: w } = useWorkflow(), router = useRouter();
  const generation = w.cluster_result;
  const base = sessionPath(w.run_id, 'clusters');
  const view = useTabView(w.run_id, 'clusters:overview', true);
  useEffect(() => {
    if (['#clusters-list', '#clusters-structure'].includes(window.location.hash)) router.replace(`${base}/records`, { scroll: false });
  }, [base, router]);
  return <section id="clusters-overview" ref={view} className={styles.overview}>
    <header className={styles.heading}><h2>Tổng quan nhóm</h2><Link className="workflow-button" scroll={false} href={`${base}/records`}>Danh sách nhóm</Link></header>
    <ClusterQueue />
    {generation ? <>
      <div className={styles.metrics}>{[
        [generation.total_records, 'Tổng bản ghi'], [generation.multi_record_groups, 'Nhóm nhiều bản ghi'],
        [generation.singletons, 'Nhóm một bản ghi'], [generation.unresolved_records, 'Chưa giải quyết'],
      ].map(([value, label]) => <section key={label} className="workflow-panel" aria-label={String(label)}><span>{label}</span><strong>{number(Number(value))}</strong></section>)}</div>
      <section className={`workflow-panel ${styles.coverage}`} aria-label="Biểu đồ độ phủ"><h3>Độ phủ kết quả</h3><strong>{number(generation.covered_records)} / {number(generation.total_records)} bản ghi</strong><progress aria-label="Bản ghi đã có kết quả" value={generation.covered_records} max={Math.max(1, generation.total_records)} /><div className={styles.distribution}>
        <div><span>Nhóm nhiều bản ghi</span><strong>{number(generation.multi_record_groups)}</strong><progress aria-label="Nhóm nhiều bản ghi" value={generation.multi_record_groups} max={Math.max(1, generation.clusters)} /></div>
        <div><span>Nhóm một bản ghi</span><strong>{number(generation.singletons)}</strong><progress aria-label="Nhóm một bản ghi" value={generation.singletons} max={Math.max(1, generation.clusters)} /></div>
      </div></section>
    </> : <section className="workflow-panel"><p>Chưa có nhóm được tạo.</p></section>}
    <GenerationStatus runId={w.run_id} generation={generation} operation={w.latest_cluster_operation} compact />
  </section>;
}
