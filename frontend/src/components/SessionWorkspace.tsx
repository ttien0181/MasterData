"use client";
import Link from 'next/link';
import { createContext, useCallback, useContext, useState, type ReactNode } from 'react';
import { useQuery } from '@tanstack/react-query';
import { getWorkflow, mutation, retryOperation } from '@/lib/workflowApi';
import type { WorkflowRead } from '@/lib/workflowContracts';
import SessionTabs from './SessionTabs';
import { PageSelectionContext } from './usePageSelection';
import { sessionKey, useSessionCache, useStoredState } from './SessionCache';
export const WorkflowContext = createContext<{ workflow: WorkflowRead; refresh: () => Promise<void> } | null>(null);
export function useWorkflow() { const value = useContext(WorkflowContext); if (!value) throw new Error('Workflow context required'); return value; }
export const blockerLabel = (code: string) => ({ DECIDE_NOT_READY: 'Phân loại chưa sẵn sàng', ACTIVE_OPERATION: 'Đang có tác vụ xử lý', NO_VALID_RECORDS: 'Không có bản ghi hợp lệ', PENDING_DECISIONS: 'Còn cặp cần duyệt hoặc hoãn', CLUSTER_CONFLICT: 'Có lựa chọn bị guard từ chối; xem lại quyết định', UNCONFIRMED_GOLDEN: 'Còn Golden chưa xác nhận', CLUSTERS_NOT_READY: 'Chưa có nhóm chính thức' }[code] || code);
const labels: Record<string, string> = { DATA_PREPARING: 'Đang chuẩn bị dữ liệu', DATA_READY: 'Dữ liệu sẵn sàng', DECIDE_RUNNING: 'Đang phân loại cặp', DECIDE_REVIEW: 'Duyệt phân loại', CLUSTER_QUEUED: 'Chờ tạo nhóm', CLUSTER_RUNNING: 'Đang tạo nhóm', GOLDEN_REVIEW: 'Duyệt Golden', FINALIZED: 'Phiên đã chốt' };
const operationLabels: Record<string, string> = { PREPARE: 'Đang chuẩn bị dữ liệu', DECIDE: 'Đang đánh giá các cặp', BULK_DECISIONS: 'Đang duyệt hàng loạt', CLUSTER: 'Đang tạo nhóm bản ghi', GOLDEN_RESOLVE: 'Đang xử lý nhóm và Golden record' };
export default function SessionWorkspace({ runId, children }: { runId: string; children: ReactNode }) {
  const cache = useSessionCache();
  const [selectionCache] = useStoredState(runId, 'selections', () => new Map());
  const [retryBusy, setRetryBusy] = useState(false);
  const [actionError, setActionError] = useState('');
  const queryKey = sessionKey(cache.userId, runId, 'workflow');
  const load = useCallback(async ({ signal }: { signal: AbortSignal }) => {
    const found = await getWorkflow(runId, signal);
    const previous = cache.client.getQueryData<WorkflowRead>(sessionKey(cache.userId, runId, 'workflow'));
    return previous && previous.workflow_revision > found.workflow_revision ? previous : found;
  }, [cache.client, cache.userId, runId]);
  const query = useQuery({ queryKey, queryFn: load, enabled: !cache.blocked[runId], staleTime: 15_000,
    refetchInterval: current => current.state.data?.active_operation ? 1800 : false });
  const workflow = query.data;
  const error = query.error?.message || actionError;
  const refresh = useCallback(async () => { await cache.client.fetchQuery({ queryKey: sessionKey(cache.userId, runId, 'workflow'), queryFn: load, staleTime: 0 }); }, [cache.client, cache.userId, runId, load]);
  if (cache.blocked[runId]) return <main className="workflow-main"><p role="alert">{cache.blocked[runId]}</p><button onClick={() => cache.retryAccess(runId)}>Thử lại</button></main>;
  if (!workflow) return <main className="workflow-main"><p role={error ? 'alert' : 'status'}>{error || 'Đang đọc phiên…'}</p>{error && <button onClick={() => void refresh().catch(() => undefined)}>Thử lại</button>}</main>;
  if (workflow.workflow_version === 1) return <main className="workflow-main"><h1>Phiên lịch sử</h1><Link href={`/app/legacy?run=${encodeURIComponent(runId)}`}>Mở kết quả lịch sử</Link></main>;
  const units: Record<string, string> = { records: 'bản ghi', pairs: 'cặp' };
  const counterLabels: Record<string, string> = { records: 'Dữ liệu đầu vào', candidates: 'Cặp ứng viên', pairs: 'Cặp đã phân loại', insufficient: 'Cặp thiếu bằng chứng', clusters: 'Nhóm bản ghi', golden: 'Golden record' };
  return <WorkflowContext.Provider value={{ workflow, refresh }}><PageSelectionContext.Provider value={selectionCache}><main className="workflow-main">
    <header className="workflow-session-header"><div><p className="workflow-eyebrow">Phiên phân giải</p><h1>{labels[workflow.workflow_state]}</h1><span className="workflow-run-id">{runId}</span></div><Link href="/app">Lịch sử phiên</Link></header>
    <div className="workflow-counters">{Object.entries(workflow.counts).map(([key, counter]) => <span key={key}>{counterLabels[key] || key}: {counter.value.toLocaleString('vi-VN')} {units[counter.unit] || counter.unit}</span>)}</div>
    <SessionTabs runId={runId} state={workflow.workflow_state} clusterRevision={workflow.cluster_revision} />
    {error && <p role="alert">{error} <button onClick={() => void refresh().catch(() => undefined)}>Đọc lại phiên</button></p>}
    {workflow.active_operation && <p role="status" className="workflow-operation">
      {operationLabels[workflow.active_operation.operation_type] || 'Đang xử lý phiên'} · {Number(workflow.active_operation.progress.processed || 0).toLocaleString('vi-VN')} / {Number(workflow.active_operation.progress.total || 0).toLocaleString('vi-VN')}
      {workflow.active_operation.operation_type === 'BULK_DECISIONS' && <> cặp · Đã áp dụng {Number(workflow.active_operation.progress.applied ?? workflow.active_operation.progress.accepted ?? 0).toLocaleString('vi-VN')} · Bị chặn {Number(workflow.active_operation.progress.blocked || 0).toLocaleString('vi-VN')} · Giữ quyết định cũ {Number(workflow.active_operation.progress.skipped || 0).toLocaleString('vi-VN')}</>}
    </p>}
    {workflow.retryable_operation && <section className="workflow-panel"><p role="alert">Tác vụ {workflow.retryable_operation.operation_type}: {workflow.retryable_operation.error_code || workflow.retryable_operation.status}</p><button disabled={retryBusy || !workflow.allowed_actions.includes('retry-operation')} onClick={async () => { setRetryBusy(true); setActionError(''); try { await retryOperation(runId, workflow.retryable_operation!.operation_id, mutation(workflow)); await refresh(); } catch (cause) { cache.handleError(cause, runId); setActionError(cause instanceof Error ? cause.message : 'Chưa retry được.'); } finally { setRetryBusy(false); } }}>Thử lại tác vụ</button></section>}
    {children}<nav className="workflow-utilities" aria-label="Tiện ích phiên"><Link href={`${'/app/sessions/' + encodeURIComponent(runId)}/utilities`}>Lịch sử xuất / Audit / Chính sách</Link></nav>
  </main></PageSelectionContext.Provider></WorkflowContext.Provider>;
}
