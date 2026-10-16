import type { WorkflowRead } from '@/lib/workflowContracts';

export default function DataLoadProgress({ workflow: w }: { workflow: WorkflowRead }) {
  if (!['DATA_PREPARING', 'DATA_READY'].includes(w.workflow_state)) return null;
  const ready = w.workflow_state === 'DATA_READY';
  const operation = w.active_operation?.operation_type === 'PREPARE' ? w.active_operation : w.retryable_operation?.operation_type === 'PREPARE' ? w.retryable_operation : null;
  const done = Number(operation?.progress.processed), total = Number(operation?.progress.total);
  const measured = Number.isFinite(done) && Number.isFinite(total) && total > 0 ? Math.floor(100 * done / total) : 0;
  const percent = ready ? 100 : Math.min(99, Math.max(0, measured));
  const queued = !ready && operation?.status === 'QUEUED';
  return <section className="workflow-data-progress" aria-label="Tiến trình tải dữ liệu">
    <div><strong>{ready ? 'Đã tải dữ liệu' : 'Đang tải dữ liệu'}</strong><span role="status" aria-live="polite">{percent}%{queued ? ' · Đang chờ xử lý' : ''}</span></div>
    <progress aria-label="Đang tải dữ liệu" max={100} value={percent} />
  </section>;
}
