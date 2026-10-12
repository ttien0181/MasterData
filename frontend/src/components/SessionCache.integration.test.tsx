import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { useState } from 'react';
import AppSession from './AppSession';
import SessionWorkspace, { useWorkflow } from './SessionWorkspace';
import DataOverview from './DataOverview';
import DecisionWorkspace from './DecisionWorkspace';
import ClusterWorkspace from './ClusterWorkspace';
import GoldenRecords from './GoldenRecords';
import type { DataRead, GoldenRead, WorkflowRead } from '@/lib/workflowContracts';
import * as api from '@/lib/workflowApi';
import { ApiError } from '@/lib/api';
import { SessionCacheProvider } from './SessionCache';
vi.mock('next/navigation', () => {
  const router = { push: vi.fn(), replace: vi.fn() };
  return { useRouter: () => router, usePathname: () => '/app/sessions/r/data' };
});
vi.mock('@/lib/api', async orig => ({ ...await orig<typeof import('@/lib/api')>(),
  getMe: vi.fn().mockResolvedValue({ id: 'owner', email: 'owner@example.test', csrf_token: 'test', role: 'STEWARD', capabilities: ['read'] }), logout: vi.fn().mockResolvedValue(undefined),
}));
vi.mock('@/lib/workflowApi', async orig => ({ ...await orig<typeof api>(), getWorkflow: vi.fn(), getData: vi.fn(), getPairSummary: vi.fn().mockResolvedValue({total:2,needs_review:2,accepted:0,denied:0,kept_separate:0,deferred:0,blocked:0,manual_available:2,auto_available:0,groups:[]}), getPairs: vi.fn(), getClusters: vi.fn(), getCluster: vi.fn(), getClusterGolden: vi.fn(), getGoldenRecords: vi.fn(), getGoldenResolution: vi.fn(), saveGolden: vi.fn() }));
let workflow: WorkflowRead;
let golden: GoldenRead;
const data = (offset: number): DataRead => ({ files: [], schema: ['name'], profile: { record_count: 12, unit: 'records', source_counts: { CRM: 12 }, quality_flags: {}, field_quality: {} }, samples: { total: 12, offset, next_cursor: offset ? null : 'next', items: Array.from({ length: offset ? 2 : 10 }, (_, index) => ({ record_id: `r${index+offset}`, external_id: `${index+offset}`, source_system: 'CRM', raw_fields: { name: `Raw ${index+offset}` }, normalized_fields: { name: `Normalized ${index+offset}` }, quality_flags: [], extra_fields: {} })) } });
beforeEach(() => {
  vi.clearAllMocks();
  workflow = { run_id: 'r', workflow_version: 2, workflow_state: 'GOLDEN_REVIEW', workflow_revision: 3, decision_revision: 1, cluster_revision: 1, golden_revision: 1, counts: {}, allowed_actions: ['save-golden'], blockers: [], active_operation: null, retryable_operation: null };
  golden = { cluster_id: 'c', cluster_revision: 1, golden_revision: 1, revision: 1, status: 'DRAFT', fields: { name: { value: 'Original' } }, alternatives: {}, selections: {} };
  vi.mocked(api.getWorkflow).mockImplementation(async () => ({ ...workflow }));
  vi.mocked(api.getData).mockImplementation(async (_run, query) => data(Number(query?.get('offset') || 0)));
  vi.mocked(api.getPairs).mockImplementation(async (_run, query) => ({ items: [{ pair_id: Number(query?.get('offset') || 0) ? 'p2' : 'p1', revision: 1, decision: 'UNDECIDED', score_unit: 'rule', eligibility: { auto_allowed: false, manual_allowed: true, blocked_reasons: [] }, baseline: { id: 'p1', left_id: 'a', right_id: 'b', primary_group: 'D', decision: 'REVIEW', score: 1, field_evidence: [] } }], total: 20, next_cursor: Number(query?.get('offset') || 0) ? null : 'p2' }));
  vi.mocked(api.getClusters).mockResolvedValue({ items: [{ cluster_id: 'c', member_record_ids: [], member_count: 1, singleton: true, status: 'READY', evidence: { items: [], total: 0, next_cursor: null } }], total: 1, next_cursor: null });
  vi.mocked(api.getCluster).mockResolvedValue({ cluster_id: 'c', member_record_ids: [], member_count: 1, singleton: true, status: 'READY', members: { items: [], total: 0, next_cursor: null }, pair_evidence: { items: [], total: 0, next_cursor: null }, evidence: { items: [], total: 0, next_cursor: null }, llm_stats: {}, workflow_context: {} });
  vi.mocked(api.getClusterGolden).mockImplementation(async () => ({ ...golden }));
  vi.mocked(api.getGoldenRecords).mockImplementation(async () => ({ items: [{ ...golden }], total: 1, next_cursor: null }));
  vi.mocked(api.getGoldenResolution).mockResolvedValue({ resolution: null, undo_operation_id: null, stale: false });
  vi.spyOn(window, 'scrollTo').mockImplementation(() => undefined);
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });
function Refresh() { const { refresh } = useWorkflow(); return <button onClick={() => void refresh().catch(() => undefined)}>Refresh workflow</button>; }
function Tabs() {
  const [tab, setTab] = useState('data');
  return <><Refresh /><div>{['data', 'decisions', 'clusters', 'golden'].map(name => <button key={name} onClick={() => setTab(name)}>Tab {name}</button>)}</div>{tab === 'data' ? <DataOverview /> : tab === 'decisions' ? <DecisionWorkspace /> : tab === 'clusters' ? <ClusterWorkspace initialClusterId="c" /> : <GoldenRecords />}</>;
}
function show() { return render(<AppSession><SessionWorkspace runId="r"><Tabs /></SessionWorkspace></AppSession>); }
it('returns to the loaded data page and normalized view without fetching that page again', async () => {
  show(); await screen.findByText('Raw 0', { selector: 'td' });
  fireEvent.click(screen.getByRole('button', { name: 'Trang sau' }));
  await screen.findByText('Raw 10', { selector: 'td' });
  fireEvent.click(screen.getByRole('button', { name: 'Chuẩn hóa' }));
  const detail = screen.getAllByText('Chi tiết', { selector: 'summary' })[0].parentElement as HTMLDetailsElement;
  detail.open = true;
  fireEvent(detail, new Event('toggle'));
  fireEvent.click(screen.getByRole('button', { name: 'Tab decisions' }));
  await screen.findByRole('checkbox', { name: 'Chọn cặp p1' });
  fireEvent.click(screen.getByRole('button', { name: 'Tab data' }));
  expect(screen.getByText('Normalized 10', { selector: 'td' })).toBeInTheDocument();
  expect(screen.getByText('Trang 2 / 2')).toBeInTheDocument();
  expect(api.getData).toHaveBeenCalledTimes(2);
  await waitFor(() => expect((screen.getAllByText('Chi tiết', { selector: 'summary' })[0].parentElement as HTMLDetailsElement).open).toBe(true));
});
it('restores pair filters, pagination and page selection when returning from another tab', async () => {
  show(); await screen.findByText('Raw 0', { selector: 'td' });
  fireEvent.click(screen.getByRole('button', { name: 'Tab decisions' }));
  await screen.findByRole('checkbox', { name: 'Chọn cặp p1' });
  fireEvent.change(screen.getByRole('combobox', { name: 'Group' }), { target: { value: 'D' } });
  fireEvent.click(await screen.findByRole('button', { name: 'Trang tiếp' }));
  const pair = await screen.findByRole('checkbox', { name: 'Chọn cặp p2' });
  fireEvent.click(pair);
  fireEvent.click(screen.getByRole('button', { name: 'Tab data' }));
  fireEvent.click(screen.getByRole('button', { name: 'Tab decisions' }));
  expect(screen.getByRole('combobox', { name: 'Group' })).toHaveValue('D');
  expect(screen.getByRole('checkbox', { name: 'Chọn cặp p2' })).toBeChecked();
  expect(api.getPairs).toHaveBeenCalledTimes(3);
});
it('retains Golden draft and original revision across tabs and rejects saving after server revision changes', async () => {
  show(); await screen.findByText('Raw 0', { selector: 'td' });
  fireEvent.click(screen.getByRole('button', { name: 'Tab clusters' }));
  const name = await screen.findByRole('textbox', { name: 'Chỉnh tay name' });
  fireEvent.change(name, { target: { value: 'My unsaved choice' } });
  fireEvent.click(screen.getByRole('button', { name: 'Tab golden' }));
  await screen.findByText('Original', { selector: 'strong' });
  fireEvent.click(screen.getByRole('button', { name: 'Tab clusters' }));
  expect(screen.getByRole('textbox', { name: 'Chỉnh tay name' })).toHaveValue('My unsaved choice');
  expect(api.getClusterGolden).toHaveBeenCalledTimes(1);
  workflow = { ...workflow, workflow_revision: 4, golden_revision: 2 };
  golden = { ...golden, golden_revision: 2, revision: 2 };
  fireEvent.click(screen.getByRole('button', { name: 'Đọc lại từ server' }));
  await screen.findByText(/Server đã có revision mới/);
  expect(screen.getByRole('textbox', { name: 'Chỉnh tay name' })).toHaveValue('My unsaved choice');
  expect(screen.getByRole('button', { name: 'Lưu lựa chọn Golden' })).toBeDisabled();
});
it('retains the Golden records page without another read on returning to that tab', async () => {
  vi.mocked(api.getGoldenRecords).mockImplementation(async (_run, params) => ({ items: [{ ...golden, cluster_id: params?.get('cursor') ? 'c2' : 'c', fields: { name: { value: params?.get('cursor') ? 'Second Golden' : 'First Golden' } } }], total: 2, next_cursor: params?.get('cursor') ? null : 'next' }));
  show(); await screen.findByText('Raw 0', { selector: 'td' });
  fireEvent.click(screen.getByRole('button', { name: 'Tab golden' }));
  await screen.findByText('First Golden', { selector: 'strong' });
  fireEvent.click(screen.getByRole('button', { name: 'Trang tiếp' }));
  await screen.findByText('Second Golden', { selector: 'strong' });
  fireEvent.click(screen.getByRole('button', { name: 'Tab data' }));
  fireEvent.click(screen.getByRole('button', { name: 'Tab golden' }));
  expect(screen.getByText('Second Golden', { selector: 'strong' })).toBeInTheDocument();
  expect(api.getGoldenRecords).toHaveBeenCalledTimes(2);
});
it('does not share raw records or UI state across runs or authenticated users', async () => {
  vi.mocked(api.getWorkflow).mockImplementation(async run => ({ ...workflow, run_id: run }));
  vi.mocked(api.getData).mockImplementation(async (run, query) => {
    const page = data(Number(query?.get('offset') || 0));
    return { ...page, samples: { ...page.samples, items: page.samples.items.map(item => ({ ...item, raw_fields: { name: `Raw ${run}` }, normalized_fields: { name: `Normalized ${run}` } })) } };
  });
  const host = (user: string, run: string) => <SessionCacheProvider userId={user}><SessionWorkspace key={run} runId={run}><DataOverview /></SessionWorkspace></SessionCacheProvider>;
  const view = render(host('u1', 'a'));
  await screen.findAllByText('Raw a', { selector: 'td' });
  fireEvent.click(screen.getByRole('button', { name: 'Chuẩn hóa' }));
  view.rerender(host('u1', 'b'));
  await screen.findAllByText('Raw b', { selector: 'td' });
  expect(screen.queryByText('Normalized a', { selector: 'td' })).toBeNull();
  view.rerender(host('u1', 'a'));
  expect(screen.getAllByText('Normalized a', { selector: 'td' })).toHaveLength(10);
  expect(api.getData).toHaveBeenCalledTimes(2);
  view.rerender(host('u2', 'a'));
  expect(screen.queryByText('Normalized a', { selector: 'td' })).toBeNull();
  await screen.findAllByText('Raw a', { selector: 'td' });
  expect(api.getData).toHaveBeenCalledTimes(3);
});
it('uses the new decision revision without evicting prepared input data', async () => {
  show(); await screen.findByText('Raw 0', { selector: 'td' });
  fireEvent.click(screen.getByRole('button', { name: 'Tab decisions' }));
  await screen.findByRole('checkbox', { name: 'Chọn cặp p1' });
  workflow = { ...workflow, workflow_revision: 4, decision_revision: 2 };
  const page = await api.getPairs('r');
  vi.mocked(api.getPairs).mockResolvedValue({ ...page, items: page.items.map(item => ({ ...item, decision: 'REJECTED' })) });
  fireEvent.click(screen.getByRole('button', { name: 'Refresh workflow' }));
  await screen.findByText('Giữ riêng liên kết');
  fireEvent.click(screen.getByRole('button', { name: 'Tab data' }));
  expect(screen.getByText('Raw 0', { selector: 'td' })).toBeInTheDocument();
  expect(api.getData).toHaveBeenCalledTimes(1);
});
it('hides and evicts cached records on loss of read access and fetches again after access is restored', async () => {
  show(); await screen.findByText('Raw 0', { selector: 'td' });
  vi.mocked(api.getWorkflow).mockRejectedValueOnce(new ApiError('Forbidden', 403));
  fireEvent.click(screen.getByRole('button', { name: 'Refresh workflow' }));
  await screen.findByText(/Không còn quyền đọc phiên/);
  expect(screen.queryByText('Raw 0', { selector: 'td' })).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Thử lại' }));
  await screen.findByText('Raw 0', { selector: 'td' });
  expect(api.getData).toHaveBeenCalledTimes(2);
});
it('shows a failed pair page read while retaining the last loaded rows and offers retry', async () => {
  show(); await screen.findByText('Raw 0', { selector: 'td' });
  fireEvent.click(screen.getByRole('button', { name: 'Tab decisions' }));
  await screen.findByRole('checkbox', { name: 'Chọn cặp p1' });
  vi.mocked(api.getPairs).mockRejectedValueOnce(new Error('Page unavailable'));
  fireEvent.click(screen.getByRole('button', { name: 'Trang tiếp' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('Page unavailable');
  expect(screen.getByRole('checkbox', { name: 'Chọn cặp p1' })).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Thử đọc lại' }));
  await screen.findByRole('checkbox', { name: 'Chọn cặp p2' });
});
it('clears visible cached data and unsaved drafts when a mutation reports an expired login', async () => {
  show(); await screen.findByText('Raw 0', { selector: 'td' });
  fireEvent.click(screen.getByRole('button', { name: 'Tab clusters' }));
  const name = await screen.findByRole('textbox', { name: 'Chỉnh tay name' });
  fireEvent.change(name, { target: { value: 'Private draft' } });
  vi.mocked(api.saveGolden).mockRejectedValue(new ApiError('Expired', 401));
  fireEvent.click(screen.getByRole('button', { name: 'Lưu lựa chọn Golden' }));
  await waitFor(() => expect(screen.queryByRole('textbox', { name: 'Chỉnh tay name' })).toBeNull());
  expect(screen.queryByText('Original', { selector: 'strong' })).toBeNull();
});
it('captures detail state when a pending cluster tab becomes ready without being remounted', async () => {
  workflow = { ...workflow, workflow_state: 'DECIDE_REVIEW', cluster_revision: 0, golden_revision: 0 };
  show(); await screen.findByText('Raw 0', { selector: 'td' });
  fireEvent.click(screen.getByRole('button', { name: 'Tab clusters' }));
  await screen.findByText(/Chưa có nhóm chính thức/);
  workflow = { ...workflow, workflow_state: 'GOLDEN_REVIEW', workflow_revision: 4, cluster_revision: 1, golden_revision: 1 };
  fireEvent.click(screen.getByRole('button', { name: 'Refresh workflow' }));
  const context = (await screen.findByText('Ngữ cảnh / semantic annotations', { selector: 'summary' })).parentElement as HTMLDetailsElement;
  context.open = true;
  fireEvent(context, new Event('toggle'));
  fireEvent.click(screen.getByRole('button', { name: 'Tab golden' }));
  await screen.findByText('Original', { selector: 'strong' });
  fireEvent.click(screen.getByRole('button', { name: 'Tab clusters' }));
  expect((screen.getByText('Ngữ cảnh / semantic annotations', { selector: 'summary' }).parentElement as HTMLDetailsElement).open).toBe(true);
});
