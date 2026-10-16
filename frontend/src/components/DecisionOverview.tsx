'use client';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { mutation, processData } from '@/lib/workflowApi';
import { sessionPath, tabReadiness } from '@/lib/workflowNavigation';
import { groupLabel, number } from '@/lib/pairReview';
import { useWorkflow } from './SessionWorkspace';
import { useWorkflowAction } from './useWorkflowAction';
import { usePairSummary } from './usePairReviewCache';
import { usePairGroupView } from './usePairGroupView';
import { CachedReadStatus, ReadNotice } from './WorkflowReads';
import { useTabView } from './useTabView';
import styles from './DecisionOverview.module.css';
import PairDistribution, { groupColor } from './PairDistribution';

const statusLabel = (status?: string) => ({ OK: 'Đã đánh giá', EVALUATED: 'Đã đánh giá', COMPLETED: 'Đã hoàn tất', COMPLETE: 'Đã hoàn tất', DEGRADED: 'Bằng chứng chưa đầy đủ', OUT_OF_SCOPE: 'Ngoài phạm vi kiểm định', NOT_CONFIGURED: 'Chưa cấu hình', BLOCKED: 'Không đủ điều kiện', RUNNING: 'Đang đánh giá' }[status || ''] || 'Chưa có kết quả');
export default function DecisionOverview() {
  const { workflow: w } = useWorkflow(), action = useWorkflowAction(), router = useRouter();
  const ready = tabReadiness(w.workflow_state, 'decisions', w.cluster_revision).ready;
  const summary = usePairSummary(w, ready), groupView = usePairGroupView(w.run_id);
  const view = useTabView(w.run_id, 'decisions:overview', !!summary.data);
  const data = summary.data;
  const reviewPath = `${sessionPath(w.run_id, 'decisions')}/pairs`;
  function openGroup(group: string) { groupView.showGroup(group); router.push(reviewPath, { scroll: false }); }
  const reviewed = data ? data.accepted + data.denied + data.kept_separate : 0;
  return <section id="decisions-summary" ref={view} className={`workflow-panel ${styles.overview}`}>
    <header><h2>Tổng quan phân loại</h2>{ready ? <Link className="workflow-button" scroll={false} href={reviewPath}>Duyệt các cặp bản ghi</Link> : w.allowed_actions.includes('process-data') && <button disabled={action.busy} onClick={() => void action.run(() => processData(w.run_id, mutation(w)))}>Xử lý dữ liệu</button>}</header>
    {action.error && <p role="alert">{action.error}</p>}
    {!ready ? <p role="status">{w.workflow_state === 'DECIDE_RUNNING' ? 'Đang xử lý các cặp bản ghi…' : 'Chưa có kết quả phân loại.'}</p> : !data ? <div className={styles.loading} aria-busy={!summary.error}><strong>{number(w.counts.pairs?.value ?? w.counts.candidates?.value ?? 0)} cặp</strong><ReadNotice error={summary.error} retry={summary.retry} /><div className={styles.skeleton} aria-hidden="true" /></div> : <>
      <CachedReadStatus read={summary} />
      {summary.error && <ReadNotice error={summary.error} retry={summary.retry} />}
      <div className={styles.metrics}>{[[data.total, 'Tổng cặp'], [data.needs_review, 'Cần xử lý'], [reviewed, 'Đã duyệt'], [data.insufficient || 0, 'Thiếu bằng chứng']].map(([value, label]) => <div key={label} role="group" aria-label={String(label)}><span>{label}</span><strong>{number(Number(value))}</strong></div>)}</div>
      <div className={styles.charts}>
        <section><h3>Phân bố nhóm</h3><PairDistribution groups={data.groups} total={data.total}/>{data.groups.map(group => <button key={group.key} onClick={() => openGroup(group.key)} style={{'--group-color':groupColor(group.key)} as React.CSSProperties}><span>{['A','B','C','D'].includes(group.key)?`Group ${group.key} · `:''}{groupLabel(group.key)}</span><strong>{number(group.total)} · {data.total?(100*group.total/data.total).toFixed(1).replace('.',','):0}%</strong><progress aria-label={`Nhóm ${group.key}`} value={group.total} max={data.total || 1} /></button>)}{!data.groups.length && <p>Không có cặp ứng viên.</p>}</section>
        <section><h3>Quyết định duyệt</h3>{[[data.accepted, 'Accept'], [data.denied, 'Deny'], ...(data.kept_separate?[[data.kept_separate, 'Giữ riêng trước đây']]:[]), ...(data.deferred?[[data.deferred, 'Đang hoãn trước đây']]:[]), [Math.max(0, data.total - reviewed - data.deferred), 'Chưa duyệt']].map(([value, label]) => <div className={styles.bar} key={label}><span>{label}</span><strong>{number(Number(value))}</strong><progress aria-label={String(label)} value={Number(value)} max={data.total || 1} /></div>)}</section>
      </div>
      <section className={styles.agents}><h3>Bằng chứng AI</h3><dl>
        <div><dt>Rule</dt><dd>Đã đánh giá</dd></div>
        <div><dt>Splink</dt><dd>{statusLabel(data.assessment?.model?.status)}</dd></div>
        <div><dt>Embedding</dt><dd>{statusLabel(w.matching_agents?.status)}</dd></div>
        <div><dt>Gemini</dt><dd>{w.active_operation?.operation_type === 'PAIR_ADVICE' ? 'Đang đánh giá' : w.advice_revision ? 'Đã chạy tư vấn' : 'Chưa có tư vấn'}</dd></div>
      </dl></section>
    </>}
  </section>;
}
