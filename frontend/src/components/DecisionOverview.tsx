'use client';
import { mutation, processData } from '@/lib/workflowApi';
import { tabReadiness } from '@/lib/workflowNavigation';
import { number } from '@/lib/pairReview';
import { useWorkflow } from './SessionWorkspace';
import { useWorkflowAction } from './useWorkflowAction';
import { usePairSummary } from './usePairReviewCache';
import { usePairGroupView } from './usePairGroupView';
import { CachedReadStatus, ReadNotice } from './WorkflowReads';
import PairGroupMenu from './PairGroupMenu';
import MetricHelp from './MetricHelp';
import {metricPercent,metricExplanation as explain} from '@/lib/metricInsights';
import styles from './DecisionOverview.module.css';
import PairDistribution from './PairDistribution';
import { mvpGroups } from '@/lib/mvpReview';

export default function DecisionOverview({ onGroup, prefetchGroup = () => undefined }: { onGroup?: (key: string) => void; prefetchGroup?: (key: string) => void }) {
  const { workflow: w } = useWorkflow(), action = useWorkflowAction();
  const ready = tabReadiness(w.workflow_state, 'decisions', w.cluster_revision).ready;
  const summary = usePairSummary(w, ready), groupView = usePairGroupView(w.run_id);
  const data = summary.data;
  const distribution = Object.keys(mvpGroups).map(key => ({ key, total: data?.groups.find(group => group.key === key)?.total ?? 0 }));
  const coverage = w.matching_agents?.coverage;
  const count = (value?: number) => value != null && Number.isFinite(value) && value >= 0 ? number(value) : '—';
  function selectGroup(key: string) { if (onGroup) onGroup(key); else groupView.change('group', key); }
  return <section id="decisions-summary" className={styles.overview}>
    <header><h2>Tổng quan phân loại</h2>{!ready && w.allowed_actions.includes('process-data') && <button disabled={action.busy} onClick={() => void action.run(() => processData(w.run_id, mutation(w)))}>Tìm hồ sơ trùng lặp</button>}</header>
    {action.error && <p role="alert">{action.error}</p>}
    {!ready ? <p role="status">{w.workflow_state === 'DECIDE_RUNNING' ? 'Đang phân loại…' : 'Chưa có kết quả phân loại.'}</p> : !data ? <div className={styles.loading} aria-busy={!summary.error}><ReadNotice error={summary.error} retry={summary.retry} /><div className={styles.skeleton} aria-hidden="true" /></div> : <>
      <CachedReadStatus read={summary} />
      {summary.error && <ReadNotice error={summary.error} retry={summary.retry} />}
      <div className={styles.metrics}>
        {[{ label: 'Tổng cặp hồ sơ', value: number(data.total), hint: 'Các cặp thuộc 4 nhóm đối chiếu' },
          { label: 'Cần duyệt', value: number(data.needs_review), hint: 'Các cặp cần quyết định của người dùng' },
          { label: 'Cặp đã kiểm tra thông tin', value: count(w.counts.pairs?.value ?? coverage?.rule_pairs), hint: 'Số cặp đã được kiểm tra bằng quy tắc đối chiếu thông tin như điện thoại, giấy tờ và ngày sinh. Dấu — nghĩa là chưa có số liệu.' },
          { label: 'Tỷ lệ cặp đã có quyết định', value: metricPercent(data.accepted+data.denied+data.kept_separate,data.total), hint: 'Số cặp đã đồng ý gộp, không gộp hoặc giữ riêng chia cho tổng cặp trong danh sách. Cặp hoãn chưa được tính là đã có quyết định.' },
          { label: 'Cặp đã đồng ý gộp', value: number(data.accepted), hint: 'Số cặp đã được đồng ý gộp. Nhiều cặp có thể thuộc cùng một nhóm khách hàng nên không dùng số này làm số khách hàng.' },
          { label: 'Cặp đã so sánh tên, địa chỉ', value: count(coverage?.embedding_pairs), hint: 'Số cặp đã được so sánh cách viết tên và địa chỉ. Đây chưa phải số cặp đã xác nhận cùng khách hàng.' },
        ].map(metric => <div key={metric.label} data-bot-metric role="group" aria-label={metric.label}><span>{metric.label}</span><strong>{metric.value}</strong><MetricHelp label={metric.label} message={explain(metric.label,metric.value,metric.hint)}/></div>)}
      </div>
      <div className={styles.distributionLayout}>
        <PairDistribution groups={distribution} total={data.total} />
        <PairGroupMenu summary={data} filter={groupView.filter} onGroup={selectGroup} onSubgroup={() => undefined} prefetchGroup={prefetchGroup} />
      </div>
    </>}
  </section>;
}
