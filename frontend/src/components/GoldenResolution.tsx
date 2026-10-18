'use client';
import { useCallback } from 'react';
import { applyGoldenRepair, getGoldenResolution, goldenRevision, resolveGolden, type ClusterScope } from '@/lib/workflowApi';
import { request } from '@/lib/api';
import type { PageRead } from '@/lib/workflowContracts';
import { useWorkflow } from './SessionWorkspace';
import { useWorkflowAction } from './useWorkflowAction';
import { CachedReadStatus, Pager, ReadNotice } from './WorkflowReads';
import { useSessionRead } from './useSessionRead';
import { useStoredState } from './SessionCache';
export default function GoldenResolution({ scope = {}, dirty = false, readOnly=false }: { scope?: ClusterScope; dirty?: boolean;readOnly?:boolean }) {
  const { workflow: w } = useWorkflow(); const action = useWorkflowAction(); const [offset, page] = useStoredState<string | null>(w.run_id, `resolution:offset:${w.workflow_revision}`, null);
  const load = useCallback((signal: AbortSignal) => getGoldenResolution(w.run_id, signal), [w.run_id, w.workflow_revision]);
  const read = useSessionRead(w.run_id, ['resolution', w.workflow_revision], {}, load); const resolution = read.data?.resolution; const proposal = resolution?.result.proposal;
  const changesLoad = useCallback((signal: AbortSignal) => request<PageRead<{pair_id:string;left_id:string;right_id:string;before:string;after:string}>>(`/runs/${encodeURIComponent(w.run_id)}/workflow/golden/resolution/changes?operation_id=${resolution?.operation_id}&offset=${offset || '0'}`, {signal}), [w.run_id, resolution?.operation_id, offset]);
  const changes = useSessionRead(w.run_id, ['resolution-changes', resolution?.operation_id], { offset }, changesLoad, !!proposal);
  const locked=readOnly||!!w.cluster_result?.read_only||!!w.cluster_result?.stale;
  const body = () => ({...goldenRevision(w),expected_decision_revision:w.decision_revision});
  return <section className="workflow-panel"><h3>Xử lý nhóm và hồ sơ đại diện</h3>
    <button disabled={locked || dirty || action.busy || !w.allowed_actions.includes('resolve-golden')} onClick={() => void action.run(() => resolveGolden(w.run_id, {...body(),...scope}))}>Tự xử lý &amp; tạo hồ sơ đại diện</button>
    {dirty && <p>Lưu lựa chọn hồ sơ đại diện trước khi xử lý.</p>}{action.error && <p role="alert">{action.error}</p>}
    {resolution && <><p role="status">Đã xác nhận {resolution.result.confirmed} hồ sơ đại diện; {resolution.result.blocked} nhóm cần chọn trường.</p>
      {proposal && <section><h4>Đề xuất xử lý xung đột: {proposal.status}</h4><p>{proposal.explanation_vi}</p><p>{proposal.reason_codes.join(', ')} · {proposal.change_count} liên kết thay đổi</p>
        <details data-view-key={`proposal:${resolution.operation_id}`}><summary>Thành viên trước / các nhóm sau đề xuất (tối đa 100)</summary><p>Trước: {proposal.before_record_ids.join(', ')}</p>{proposal.after_groups.map((ids,i) => <p key={i}>Nhóm {i+1}: {ids.join(', ')}</p>)}</details>
        {changes.data ? <><CachedReadStatus read={changes} /><div className="workflow-scroll" data-scroll-key="resolution-changes"><table><thead><tr><th>Liên kết</th><th>Trước</th><th>Sau</th></tr></thead><tbody>{changes.data.items.map(c => <tr key={c.pair_id}><td>{c.left_id} / {c.right_id}</td><td>{c.before}</td><td>{c.after}</td></tr>)}</tbody></table></div><Pager cursor={offset} next={changes.data.next_cursor} onPage={page} busy={changes.fetching && changes.previous} /></> : <ReadNotice error={changes.error} retry={changes.retry} />}
        {read.data?.stale && <p>Đề xuất đã cũ. Hãy xử lý lại để kiểm tra phiên bản hiện tại.</p>}
        <button disabled={locked || dirty || action.busy || !!w.active_operation || proposal.status !== 'READY' || read.data?.stale} onClick={() => void action.run(() => applyGoldenRepair(w.run_id,{...body(),operation_id:resolution.operation_id}))}>Áp dụng đề xuất và tạo lại nhóm</button>
      </section>}</>}
    {read.data?.undo_operation_id && <details data-view-key={`undo:${read.data.undo_operation_id}`}><summary>Hoàn tác đề xuất vừa áp dụng</summary><p>Khôi phục quyết định trước thay đổi và kiểm tra lại các nhóm. Hồ sơ đại diện sẽ được xác nhận lại nếu hợp lệ.</p><button disabled={locked || dirty || action.busy || !!w.active_operation} onClick={() => void action.run(() => applyGoldenRepair(w.run_id,{...body(),operation_id:read.data!.undo_operation_id!},true))}>Xác nhận hoàn tác đề xuất</button></details>}
    {read.error && <ReadNotice error={read.error} retry={read.retry} />}
  </section>;
}
