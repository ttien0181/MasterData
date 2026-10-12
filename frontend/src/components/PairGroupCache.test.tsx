import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { WorkflowContext } from './SessionWorkspace';
import { SessionCacheProvider } from './SessionCache';
import DecisionWorkspace from './DecisionWorkspace';
import * as api from '@/lib/workflowApi';
import type { PageRead, PairRead, WorkflowRead } from '@/lib/workflowContracts';
import { ApiError } from '@/lib/api';

vi.mock('next/navigation', () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock('@/lib/workflowApi', async original => ({ ...await original<typeof api>(), getPairs: vi.fn(), getPairSummary: vi.fn() }));
const workflow: WorkflowRead = { run_id: 'r', workflow_version: 2, workflow_state: 'DECIDE_REVIEW', workflow_revision: 4, decision_revision: 1, cluster_revision: 0, golden_revision: 0, counts: {}, allowed_actions: ['decide-pair', 'bulk-decisions'], blockers: [], active_operation: null, retryable_operation: null };
function page(group: string, offset: number, subgroup = ''): PageRead<PairRead> {
  const total = group === 'A' ? 34 : 25;
  return { total, next_cursor: offset + 10 < total ? `cursor-${offset + 10}` : null, items: Array.from({ length: Math.min(10, total - offset) }, (_, index) => {
    const id = `${group}-${subgroup || 'all'}-${offset + index}`;
    return { pair_id: id, decision: 'UNDECIDED', revision: 0, score_unit: 'p119_rule_v1', eligibility: { manual_allowed: true, auto_allowed: false, blocked_reasons: [] }, baseline: { id, left_id: `${id}-l`, right_id: `${id}-r`, primary_group: group, subgroup: subgroup || 'A1_NAME_PHONE', score: 5, decision: 'REVIEW', field_evidence: [] } };
  }) };
}
function host(state = workflow, shown = true) {
  return <SessionCacheProvider userId="u"><WorkflowContext.Provider value={{ workflow: state, refresh: vi.fn().mockResolvedValue(undefined) }}>{shown && <DecisionWorkspace />}</WorkflowContext.Provider></SessionCacheProvider>;
}
function reads(group: string, offset: number, subgroup = '') {
  return vi.mocked(api.getPairs).mock.calls.filter(([, query]) => (query?.get('group') || 'ALL') === group && Number(query?.get('offset') || 0) === offset && (query?.get('subgroup') || '') === subgroup).length;
}
beforeEach(() => {
  vi.resetAllMocks();
  vi.spyOn(window, 'scrollTo').mockImplementation(() => undefined);
  vi.mocked(api.getPairs).mockImplementation(async (_run, query) => page(query?.get('group') || 'ALL', Number(query?.get('offset') || 0), query?.get('subgroup') || ''));
  vi.mocked(api.getPairSummary).mockResolvedValue({ total: 59, needs_review: 59, accepted: 0, denied: 0, kept_separate: 0, deferred: 0, blocked: 0, manual_available: 59, auto_available: 0, decision_revision: 1, groups: [{ key: 'A', total: 34, subgroups: [{ key: 'A1_NAME_PHONE', total: 34 }] }, { key: 'D', total: 25, subgroups: [] }, { key: 'C', total: 0, subgroups: [] }] });
});
afterEach(() => { cleanup(); vi.useRealTimers(); vi.restoreAllMocks(); });
it('isolates reassessed pages while reusing Group pages within the same assessment',async()=>{
  const view=render(host());
  await screen.findByRole('checkbox',{name:'Chọn cặp ALL-all-0'});
  expect(reads('ALL',0)).toBe(1);
  view.rerender(host({...workflow,assessment_id:'assessment-2'}));
  await waitFor(()=>expect(reads('ALL',0)).toBe(2));
  fireEvent.change(screen.getByRole('combobox',{name:'Group'}),{target:{value:'A'}});
  await screen.findByRole('checkbox',{name:'Chọn cặp A-all-0'});
  fireEvent.change(screen.getByRole('combobox',{name:'Group'}),{target:{value:'D'}});
  await screen.findByRole('checkbox',{name:'Chọn cặp D-all-0'});
  fireEvent.change(screen.getByRole('combobox',{name:'Group'}),{target:{value:'A'}});
  expect(screen.getByRole('checkbox',{name:'Chọn cặp A-all-0'})).toBeInTheDocument();
  expect(reads('A',0)).toBe(1);
});
it('refreshes cached pages after advice publication even without a decision change',async()=>{
  const view=render(host());
  await screen.findByRole('checkbox',{name:'Chọn cặp ALL-all-0'});
  expect(reads('ALL',0)).toBe(1);
  view.rerender(host({...workflow,...({advice_revision:'advice-2'} as object)}));
  await waitFor(()=>expect(reads('ALL',0)).toBe(2));
});

it('restores each Group page, Subgroup and search immediately without rereading a fresh page', async () => {
  render(host());
  await screen.findByRole('checkbox', { name: 'Chọn cặp ALL-all-0' });
  fireEvent.change(screen.getByRole('combobox', { name: 'Group' }), { target: { value: 'A' } });
  await screen.findByRole('checkbox', { name: 'Chọn cặp A-all-0' });
  fireEvent.change(screen.getByRole('combobox', { name: 'SubGroup' }), { target: { value: 'A1_NAME_PHONE' } });
  await screen.findByRole('checkbox', { name: 'Chọn cặp A-A1_NAME_PHONE-0' });
  fireEvent.click(screen.getByRole('button', { name: 'Trang tiếp' }));
  await screen.findByRole('checkbox', { name: 'Chọn cặp A-A1_NAME_PHONE-10' });
  fireEvent.click(screen.getByRole('button', { name: 'Trang tiếp' }));
  await screen.findByRole('checkbox', { name: 'Chọn cặp A-A1_NAME_PHONE-20' });
  fireEvent.change(screen.getByRole('textbox', { name: 'Tìm trong trang' }), { target: { value: 'A-A1_NAME_PHONE-20' } });
  fireEvent.click(screen.getByRole('checkbox', { name: 'Chọn cặp A-A1_NAME_PHONE-20' }));
  fireEvent.change(screen.getByRole('combobox', { name: 'Group' }), { target: { value: 'D' } });
  await screen.findByRole('checkbox', { name: 'Chọn cặp D-all-0' });
  fireEvent.change(screen.getByRole('combobox', { name: 'Group' }), { target: { value: 'A' } });
  expect(screen.getByRole('checkbox', { name: 'Chọn cặp A-A1_NAME_PHONE-20' })).not.toBeChecked();
  expect(screen.getByRole('combobox', { name: 'SubGroup' })).toHaveValue('A1_NAME_PHONE');
  expect(screen.getByRole('textbox', { name: 'Tìm trong trang' })).toHaveValue('A-A1_NAME_PHONE-20');
  expect(screen.getByText(/Trang 3 \/ 4/)).toBeInTheDocument();
  expect(reads('A', 20, 'A1_NAME_PHONE')).toBe(1);
});

it('reuses the same page after navigating forward and backward despite different server cursors', async () => {
  render(host()); await screen.findByRole('checkbox', { name: 'Chọn cặp ALL-all-0' });
  fireEvent.click(screen.getByRole('button', { name: 'Trang tiếp' }));
  await screen.findByRole('checkbox', { name: 'Chọn cặp ALL-all-10' });
  fireEvent.click(screen.getByRole('button', { name: 'Trang trước' }));
  await screen.findByRole('checkbox', { name: 'Chọn cặp ALL-all-0' });
  fireEvent.click(screen.getByRole('button', { name: 'Trang tiếp' }));
  expect(screen.getByRole('checkbox', { name: 'Chọn cặp ALL-all-10' })).toBeInTheDocument();
  expect(reads('ALL', 10)).toBe(1);
});

it('keeps cached rows and review intentions available while a revised page is updating', async () => {
  const view = render(host()); await screen.findByRole('checkbox', { name: 'Chọn cặp ALL-all-0' });
  let resolve!: (value: PageRead<PairRead>) => void;
  vi.mocked(api.getPairs).mockImplementation(() => new Promise(done => { resolve = done; }));
  view.rerender(host({ ...workflow, workflow_revision: 5, decision_revision: 2 }));
  expect(screen.getByRole('checkbox', { name: 'Chọn cặp ALL-all-0' })).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Accept cặp ALL-all-0' })).toBeEnabled();
  await waitFor(() => expect(resolve).toBeTypeOf('function'));
  resolve(page('ALL', 0));
  await waitFor(() => expect(screen.getByRole('button', { name: 'Accept cặp ALL-all-0' })).toBeEnabled());
});

it('does not reuse another Group rows while an uncached Group is loading or fails', async () => {
  render(host()); await screen.findByRole('checkbox', { name: 'Chọn cặp ALL-all-0' });
  vi.mocked(api.getPairs).mockRejectedValue(new Error('Group unavailable'));
  fireEvent.change(screen.getByRole('combobox', { name: 'Group' }), { target: { value: 'C' } });
  expect(screen.queryByRole('checkbox', { name: 'Chọn cặp ALL-all-0' })).toBeNull();
  await screen.findByText('Group unavailable');
  expect(screen.queryByRole('checkbox', { name: /Chọn cặp/ })).toBeNull();
});

it('prefetches only nonempty Group first pages and one next page, each with ten pairs', async () => {
  render(host()); await screen.findByRole('checkbox', { name: 'Chọn cặp ALL-all-0' });
  await waitFor(() => expect(reads('A', 0)).toBe(1));
  await waitFor(() => expect(reads('D', 0)).toBe(1));
  await waitFor(() => expect(reads('ALL', 10)).toBe(1));
  expect(reads('C', 0)).toBe(0);
  expect(vi.mocked(api.getPairs).mock.calls.every(([, query]) => query?.get('limit') === '10')).toBe(true);
  fireEvent.change(screen.getByRole('combobox', { name: 'Group' }), { target: { value: 'A' } });
  expect(screen.getByRole('checkbox', { name: 'Chọn cặp A-all-0' })).toBeInTheDocument();
  expect(reads('A', 0)).toBe(1);
});

it('shows the previously cached Group on remount and refreshes revisions changed while unmounted', async () => {
  const view = render(host()); await screen.findByRole('checkbox', { name: 'Chọn cặp ALL-all-0' });
  view.rerender(host(workflow, false));
  const next = { ...workflow, workflow_revision: 5, decision_revision: 2 };
  view.rerender(host(next, false));
  vi.mocked(api.getPairs).mockImplementation(() => new Promise(() => {}));
  view.rerender(host(next));
  expect(screen.getByRole('checkbox', { name: 'Chọn cặp ALL-all-0' })).toBeInTheDocument();
  await waitFor(() => expect(reads('ALL', 0)).toBe(2));
  expect(screen.getByRole('button', { name: 'Accept cặp ALL-all-0' })).toBeEnabled();
});

it('throttles revision bursts during bulk processing and refreshes immediately when it finishes', async () => {
  vi.useFakeTimers();
  const operation = { operation_id: 'op', run_id: 'r', operation_type: 'BULK_DECISIONS', status: 'RUNNING', stage: 'running', progress: {}, error_code: null, result: {} };
  const view = render(host({ ...workflow, active_operation: operation }));
  await act(async () => { await vi.advanceTimersByTimeAsync(50); });
  expect(reads('ALL', 0)).toBe(1);
  for (let revision = 2; revision <= 5; revision++) {
    view.rerender(host({ ...workflow, decision_revision: revision, workflow_revision: revision + 3, active_operation: operation }));
    await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
    expect(screen.getByRole('checkbox', { name: 'Chọn cặp ALL-all-0' })).toBeInTheDocument();
  }
  expect(reads('ALL', 0)).toBe(1);
  view.rerender(host({ ...workflow, decision_revision: 6, workflow_revision: 9, active_operation: operation }));
  await act(async () => { await vi.advanceTimersByTimeAsync(1100); });
  expect(reads('ALL', 0)).toBe(2);
  view.rerender(host({ ...workflow, decision_revision: 7, workflow_revision: 10 }));
  await act(async () => { await vi.advanceTimersByTimeAsync(50); });
  expect(reads('ALL', 0)).toBe(3);
  expect(screen.getByRole('checkbox', { name: 'Chọn cặp ALL-all-0' })).toBeInTheDocument();
});

it('marks inactive cached Groups stale without prefetching them again after a decision revision', async () => {
  const view = render(host()); await screen.findByRole('checkbox', { name: 'Chọn cặp ALL-all-0' });
  await waitFor(() => expect(reads('A', 0)).toBe(1));
  await waitFor(() => expect(reads('D', 0)).toBe(1));
  vi.mocked(api.getPairSummary).mockResolvedValue({ ...(await api.getPairSummary('r')), decision_revision: 2 });
  view.rerender(host({ ...workflow, workflow_revision: 5, decision_revision: 2 }));
  await waitFor(() => expect(reads('ALL', 0)).toBe(2));
  await new Promise(resolve => setTimeout(resolve, 400));
  expect(reads('A', 0)).toBe(1);
  expect(reads('D', 0)).toBe(1);
  fireEvent.change(screen.getByRole('combobox', { name: 'Group' }), { target: { value: 'A' } });
  expect(screen.getByRole('checkbox', { name: 'Chọn cặp A-all-0' })).toBeInTheDocument();
  await waitFor(() => expect(reads('A', 0)).toBe(2));
});

it('an old in-flight result stays visible and allows revision-checked review intentions', async () => {
  const pending: ((value: PageRead<PairRead>) => void)[] = [];
  vi.mocked(api.getPairs).mockImplementation(() => new Promise(resolve => pending.push(resolve)));
  const view = render(host());
  await waitFor(() => expect(pending).toHaveLength(1));
  view.rerender(host({ ...workflow, decision_revision: 2, workflow_revision: 5 }));
  pending[0](page('ALL', 0));
  await screen.findByRole('checkbox', { name: 'Chọn cặp ALL-all-0' });
  expect(screen.getByRole('button', { name: 'Accept cặp ALL-all-0' })).toBeEnabled();
  await waitFor(() => expect(pending).toHaveLength(2));
  pending[1](page('ALL', 0));
  await waitFor(() => expect(screen.getByRole('button', { name: 'Accept cặp ALL-all-0' })).toBeEnabled());
});

it.each([401, 403])('stops queued prefetches and hides cached rows after a prefetch receives %s', async status => {
  vi.mocked(api.getPairs).mockImplementation(async (_run, query) => {
    const group = query?.get('group') || 'ALL';
    if (group === 'A') throw new ApiError('Read access lost', status);
    return page(group, Number(query?.get('offset') || 0));
  });
  render(host()); await screen.findByRole('checkbox', { name: 'Chọn cặp ALL-all-0' });
  await waitFor(() => expect(reads('A', 0)).toBe(1));
  await waitFor(() => expect(screen.queryByRole('checkbox', { name: 'Chọn cặp ALL-all-0' })).toBeNull());
  expect(reads('D', 0)).toBe(0);
});

it('refreshes the newest revision after bulk completes while an older refresh is still in flight', async () => {
  vi.useFakeTimers();
  const operation = { operation_id: 'op', run_id: 'r', operation_type: 'BULK_DECISIONS', status: 'RUNNING', stage: 'running', progress: {}, error_code: null, result: {} };
  const view = render(host({ ...workflow, active_operation: operation }));
  await act(async () => { await vi.advanceTimersByTimeAsync(50); });
  const pending: ((value: PageRead<PairRead>) => void)[] = [];
  vi.mocked(api.getPairs).mockImplementation(() => new Promise(resolve => pending.push(resolve)));
  view.rerender(host({ ...workflow, decision_revision: 2, workflow_revision: 5, active_operation: operation }));
  await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
  expect(pending).toHaveLength(1);
  view.rerender(host({ ...workflow, decision_revision: 3, workflow_revision: 6, active_operation: operation }));
  view.rerender(host({ ...workflow, decision_revision: 3, workflow_revision: 7 }));
  await act(async () => { pending[0](page('ALL', 0)); await vi.advanceTimersByTimeAsync(50); });
  expect(pending).toHaveLength(2);
  expect(screen.getByRole('button', { name: 'Accept cặp ALL-all-0' })).toBeEnabled();
  await act(async () => { pending[1](page('ALL', 0)); await vi.advanceTimersByTimeAsync(50); });
  expect(screen.getByRole('button', { name: 'Accept cặp ALL-all-0' })).toBeEnabled();
  expect(reads('ALL', 0)).toBe(3);
});
