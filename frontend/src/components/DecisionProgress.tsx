import type { WorkflowRead } from '@/lib/workflowContracts';
const number = (value: unknown) => Math.max(0, Number(value) || 0).toLocaleString('vi-VN');
export default function DecisionProgress({ workflow: w }: { workflow: WorkflowRead }) {
  const operation = w.active_operation?.operation_type === 'DECIDE' ? w.active_operation : null;
  const completed = w.workflow_state === 'DECIDE_REVIEW' && !operation;
  if (!operation && !completed) return null;
  const progress = operation?.progress || {};
  const evaluating = completed || !!progress.classification_started;
  const total = Number(progress.candidate_count ?? w.counts.candidates?.value ?? w.counts.pairs?.value ?? 0);
  const done = completed ? total : Math.max(0, Math.min(total, Number(progress.classified_count) || 0));
  const percent = completed ? 100 : total > 0 ? Math.min(99, Math.floor(100 * done / total)) : 0;
  const saving = operation && ['seal-decisions','seal-assessment','publish-decisions'].includes(operation.stage);
  return <section className="workflow-data-progress" aria-label="Tiến trình phân loại cặp">
    {!evaluating ? <div><strong>Đang tạo các cặp bản ghi</strong><span role="status" aria-live="polite">{number(progress.candidate_count)} cặp</span></div> : <>
      <div><strong>{completed ? 'Đã phân loại các cặp bản ghi' : saving ? 'Đang lưu kết quả phân loại' : 'Đang đánh giá phân loại các cặp'}</strong><span role="status" aria-live="polite">{number(done)} / {number(total)} cặp · {percent}%</span></div>
      <progress aria-label="Tiến độ đánh giá phân loại cặp" value={percent} max={100} />
    </>}
  </section>;
}
