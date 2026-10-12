import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { GoldenRead, WorkflowRead } from '@/lib/workflowContracts';
import SessionWorkspace, { WorkflowContext } from './SessionWorkspace';
import { ApiError } from '@/lib/api';
import DecisionWorkspace from './DecisionWorkspace';
import ClusterWorkspace from './ClusterWorkspace';
import GoldenFieldEditor from './GoldenFieldEditor';
import GoldenRecords from './GoldenRecords';
import SessionHub from './SessionHub';
import * as api from '@/lib/workflowApi';
import { SessionCacheProvider } from './SessionCache';
vi.mock('next/navigation', () => ({ useRouter: () => ({ push: vi.fn(), replace: vi.fn() }), usePathname: () => '/app' }));
vi.mock('./AppSession', () => ({ useAppUser: () => ({ id: 'u', email: 'u@example.test', capabilities: ['read','create_run'] }) }));
vi.mock('@/lib/workflowApi', async importOriginal => ({ ...await importOriginal<typeof api>(), getWorkflow: vi.fn(), getPairSummary: vi.fn().mockResolvedValue({total:2,needs_review:2,accepted:0,denied:0,kept_separate:0,deferred:0,blocked:0,manual_available:2,auto_available:0,groups:[]}), getPairs: vi.fn(), getClusters: vi.fn(), getGoldenRecords: vi.fn(), decidePair: vi.fn(), finalizeDecisions: vi.fn(), saveGolden: vi.fn() }));
vi.mock('@/lib/api', async importOriginal => ({ ...await importOriginal<typeof import('@/lib/api')>(), getResolutionRuns: vi.fn().mockResolvedValue({ items: [], total: 0 }), getUploadSessions: vi.fn().mockResolvedValue({ items: [] }) }));
afterEach(() => { cleanup(); vi.useRealTimers(); });
const w: WorkflowRead = { run_id: 'r', workflow_version: 2, workflow_state: 'DECIDE_REVIEW', workflow_revision: 3, decision_revision: 2, cluster_revision: 0, golden_revision: 0, counts: { records: { value: 3, unit: 'records' } }, allowed_actions: ['decide-pair', 'bulk-decisions', 'finalize-decisions'], blockers: [], active_operation: null, retryable_operation: null };
const golden: GoldenRead = { cluster_id: 'c', cluster_revision: 1, golden_revision: 2, revision: 0, status: 'DRAFT', fields: { name: { value: 'Server', provenance: 'SURVIVORSHIP' }, phone: { value: '090', values: [{ value: '090' }, { value: '091' }] } }, alternatives: { name: [{ source_record_id: 'member', source_system: 'CRM', value: 'Source', auto_rule_available: true }] }, selections: {} };
function workspace(ui: React.ReactNode, workflow = w) { return render(<SessionCacheProvider userId="test"><WorkflowContext.Provider value={{ workflow, refresh: vi.fn().mockResolvedValue(undefined) }}>{ui}</WorkflowContext.Provider></SessionCacheProvider>); }
beforeEach(() => { vi.clearAllMocks(); vi.mocked(api.getPairs).mockResolvedValue({ items: [{ pair_id: 'p', baseline: { id: 'p', left_id: 'a', right_id: 'b', score: 10, decision: 'INSUFFICIENT_EVIDENCE', primary_group: 'D', field_evidence: [] }, decision: 'UNDECIDED', revision: 1, score_unit: 'p119_rule_v1', eligibility: { manual_allowed: false, auto_allowed: false, blocked_reasons: ['NO_EVIDENCE'] } }], total: 1, next_cursor: null }); });
it('hub starts at overview and performs no workflow computation', async () => { render(<SessionHub />); expect(await screen.findByRole('heading', { name: 'Tổng quan' })).toBeInTheDocument(); expect(api.finalizeDecisions).not.toHaveBeenCalled(); });
it('shows insufficient evidence and server permissions, only explicit finalize creates groups', async () => {
  workspace(<DecisionWorkspace />);
  expect(await screen.findByText('Thiếu bằng chứng')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Accept cặp p' })).toBeDisabled();
  expect(api.finalizeDecisions).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Hoàn tất phân loại và tạo nhóm' }));
  await waitFor(() => expect(api.finalizeDecisions).toHaveBeenCalledTimes(1));
});
it('pending/defer blockers disable finalize, queued cluster never reads membership', () => {
  const view = workspace(<DecisionWorkspace />, { ...w, allowed_actions: ['decide-pair'], blockers: [{ code: 'PENDING_DECISIONS', count: 1 }] });
  expect(screen.getByRole('button', { name: 'Hoàn tất phân loại và tạo nhóm' })).toBeDisabled(); view.unmount();
  workspace(<ClusterWorkspace />, { ...w, workflow_state: 'CLUSTER_RUNNING' });
  expect(api.getClusters).not.toHaveBeenCalled();
});
it('edits server golden while preserving multivalue and explicit source choice', async () => {
  const save = vi.fn().mockResolvedValue(undefined);
  render(<GoldenFieldEditor golden={golden} editable busy={false} onSave={save} />);
  expect(screen.getByText('091')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Chọn name từ CRM: Source' }));
  fireEvent.click(screen.getByRole('button', { name: 'Lưu lựa chọn Golden' }));
  await waitFor(() => expect(save).toHaveBeenCalledWith({ name: { selection_mode: 'SOURCE_CHOICE', source_record_id: 'member' } }, 1, 2));
});
it('unconfirmed drafts cannot finalize or export', async () => {
  vi.mocked(api.getGoldenRecords).mockResolvedValue({ items: [golden], total: 1, next_cursor: null });
  workspace(<GoldenRecords />, { ...w, workflow_state: 'GOLDEN_REVIEW', cluster_revision: 1, allowed_actions: ['save-golden'], blockers: [{ code: 'UNCONFIRMED_GOLDEN', count: 1 }] });
  expect(await screen.findByText('DRAFT')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Chốt phiên' })).toBeDisabled();
  expect(screen.queryByRole('button', { name: 'Xuất CSV' })).not.toBeInTheDocument();
});
it('preserves unsaved Golden intent across server revision and blocks silent rebase', () => {
  const save = vi.fn();
  const view = render(<GoldenFieldEditor golden={golden} editable busy={false} onSave={save} />);
  fireEvent.change(screen.getByRole('textbox', { name: 'Chỉnh tay name' }), { target: { value: 'My draft' } });
  view.rerender(<GoldenFieldEditor golden={{ ...golden, golden_revision: 3, fields: { name: { value: 'Other writer' } } }} editable busy={false} onSave={save} />);
  expect(screen.getByRole('textbox', { name: 'Chỉnh tay name' })).toHaveValue('My draft');
  expect(screen.getByRole('button', { name: 'Lưu lựa chọn Golden' })).toBeDisabled();
  expect(save).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Bỏ bản nháp' }));
  expect(screen.getByRole('textbox', { name: 'Chỉnh tay name' })).toHaveValue('');
});
it('zero candidate still exposes explicit finalize without guessing an empty result', async () => {
  vi.mocked(api.getPairs).mockResolvedValue({ items: [], total: 0, next_cursor: null });
  workspace(<DecisionWorkspace />);
  expect(await screen.findByText(/Phiên không có candidate vẫn cần/)).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Hoàn tất phân loại và tạo nhóm' })).toBeEnabled();
  expect(api.finalizeDecisions).not.toHaveBeenCalled();
});
it('extra fields expose source choices and no manual/auto ranking invented in FE', () => {
  render(<GoldenFieldEditor golden={{ ...golden, alternatives: { custom: [{ source_record_id: 'member', source_system: 'CRM', value: 'Extra', auto_rule_available: false }] } }} editable busy={false} onSave={vi.fn()} />);
  expect(screen.getByText(/Trường bổ sung chỉ chọn nguồn/)).toBeInTheDocument();
  expect(screen.queryByRole('textbox', { name: 'Chỉnh tay custom' })).not.toBeInTheDocument();
});
it('manual eligibility comes from server even for Group D, stale request preserves intent', async () => {
  const original = await api.getPairs('r');
  vi.mocked(api.getPairs).mockResolvedValue({ ...original, items: original.items.map(p => ({ ...p, eligibility: { ...p.eligibility, manual_allowed: true } })) });
  vi.mocked(api.getWorkflow).mockResolvedValue(w);
  vi.mocked(api.decidePair).mockRejectedValue(new ApiError('stale', 409, { code: 'STALE_PAIR_REVISION' }));
  workspace(<DecisionWorkspace />);
  const merge = await screen.findByRole('button', { name: 'Accept cặp p' }); expect(merge).toBeEnabled(); fireEvent.click(merge);
  expect(await screen.findByText(/Cặp hoặc phiên đã thay đổi/)).toBeInTheDocument();
  expect(screen.getByText('Accept · p')).toBeInTheDocument();
  expect(api.decidePair).toHaveBeenCalledTimes(1);
});
it('run change/unmount cancels initial and active-operation polling reads', async () => {
  vi.useFakeTimers(); const signals: AbortSignal[] = [];
  vi.mocked(api.getWorkflow).mockImplementation((_run, signal) => {
    if (signal) signals.push(signal);
    if (signals.length > 1) return new Promise((_resolve, reject) => signal?.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError'))));
    return Promise.resolve({ ...w, active_operation: { operation_id: 'op', run_id: 'r', operation_type: 'CLUSTER', status: 'RUNNING', stage: 'compute', progress: {}, error_code: null, result: {} } });
  });
  const view = render(<SessionCacheProvider userId="test"><SessionWorkspace key="r" runId="r"><span>Workspace</span></SessionWorkspace></SessionCacheProvider>);
  await act(async () => { await vi.advanceTimersByTimeAsync(0); });
  await act(async () => { await vi.advanceTimersByTimeAsync(1800); });
  view.rerender(<SessionCacheProvider userId="test"><SessionWorkspace key="second" runId="second"><span>Workspace</span></SessionWorkspace></SessionCacheProvider>);
  expect(signals[1].aborted).toBe(true); view.unmount(); expect(signals.slice(1).every(s => s.aborted)).toBe(true);
  expect(api.finalizeDecisions).not.toHaveBeenCalled();
});
