'use client';
import Link from 'next/link';
import { number } from '@/lib/pairReview';
import { useWorkflow } from './SessionWorkspace';
import ClusterQueue from './ClusterQueue';
import ClusterWorkspace from './ClusterWorkspace';
import GenerationStatus from './GenerationStatus';
import MetricHelp from './MetricHelp';
import {metricNumber,metricPercent,metricExplanation as explain} from '@/lib/metricInsights';
import styles from './ClusterOverview.module.css';

export default function ClusterOverview({ initialClusterId = '' }: { initialClusterId?: string }) {
  const { workflow: w } = useWorkflow();
  const generation = w.cluster_result;
  return <section id="clusters-overview" className={styles.overview}>
    <header className={styles.heading}><h1>Các nhóm hồ sơ khách hàng</h1><Link className={styles.history} href="/app">Lịch sử phiên</Link></header>
    <ClusterQueue />
    {generation && <>
      <div className={styles.metrics}>{[
        [number(generation.total_records), 'Tổng hồ sơ','Số hồ sơ đầu vào thuộc phiên tổng hợp hiện tại, gồm cả hồ sơ chưa có nhóm.'],
        [number(generation.multi_record_groups), 'Nhóm có hồ sơ trùng','Số nhóm chứa từ hai hồ sơ trở lên. Một nhóm đại diện cho một khách hàng theo các quyết định hiện tại; số nhóm khác với số cặp đã gộp.'],
        [number(generation.singletons), 'Hồ sơ riêng lẻ','Số nhóm chỉ có một hồ sơ. Hồ sơ này vẫn được giữ làm khách hàng riêng theo kết quả xử lý hiện tại.'],
        [number(generation.unresolved_records), 'Hồ sơ chưa có nhóm','Số hồ sơ chưa nằm trong kết quả nhóm đang công bố. Bạn cần xem lại các cặp hoặc tác vụ xử lý trước khi coi danh sách là đầy đủ.'],
        [metricPercent(generation.covered_records,generation.total_records),'Tỷ lệ hồ sơ đã có nhóm','Số hồ sơ đã có nhóm chia cho tổng hồ sơ đầu vào. Chỉ số phản ánh độ phủ xử lý, không phải độ chính xác của việc gộp.'],
        [metricNumber(generation.covered_records-generation.clusters),'Số hồ sơ giảm sau tổng hợp','Số hồ sơ đã có nhóm trừ số nhóm khách hàng. Chỉ số cho biết số dòng giảm khi chuyển từ hồ sơ nguồn sang hồ sơ đại diện, chưa quy đổi thành chi phí tiết kiệm.'],
      ].map(([value,label,meaning]) => <div data-bot-metric key={label} role="group" aria-label={label}><span>{label}</span><strong>{value}</strong><MetricHelp label={label} message={explain(label,value,meaning)}/></div>)}</div>
    </>}
    <ClusterWorkspace initialClusterId={initialClusterId} />
    {(generation || w.latest_cluster_operation) && <details className={styles.processing}><summary>Chi tiết xử lý</summary><GenerationStatus runId={w.run_id} generation={generation} operation={w.latest_cluster_operation} compact /></details>}
  </section>;
}
