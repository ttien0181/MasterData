import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import DataOverview from './DataOverview';
import { WorkflowContext } from './SessionWorkspace';
import type { DataRead, WorkflowRead } from '@/lib/workflowContracts';
import * as api from '@/lib/workflowApi';
import { SessionCacheProvider } from './SessionCache';
const push = vi.hoisted(() => vi.fn());
vi.mock('@/lib/workflowApi', async orig => ({ ...await orig<typeof api>(), getData: vi.fn(), processData: vi.fn() }));
vi.mock('next/navigation', () => ({ useRouter: () => ({ push }) }));
afterEach(() => { cleanup(); vi.clearAllMocks(); });
const w: WorkflowRead = { run_id: 'r', workflow_version: 2, workflow_state: 'DATA_READY', workflow_revision: 1, decision_revision: 0, cluster_revision: 0, golden_revision: 0, counts: {}, allowed_actions: [], blockers: [], active_operation: null, retryable_operation: null };
const data = (offset = 0): DataRead => ({ files: [{ filename: 'customers.csv', row_count: 25, sha256: 'a' }], schema: ['name', 'email'], profile: { record_count: 25, unit: 'records', source_counts: { CRM: 25 }, quality_flags: { INVALID_EMAIL_FORMAT: 2 }, field_quality: {}, records_with_quality_flags: 2, field_completeness: { name: { present: 25, missing: 0 }, email: { present: 20, missing: 5 } } }, samples: { items: Array.from({ length: Math.min(10, 25 - offset) }, (_, i) => ({ record_id: `r${offset+i}`, external_id: `${offset+i}`, source_system: 'CRM', raw_fields: { name: `Person ${offset+i}`, email: 'bad' }, normalized_fields: { name: `Normalized ${offset+i}` }, quality_flags: ['INVALID_EMAIL_FORMAT'], extra_fields: { custom: 'Extra value' } })), total: 25, next_cursor: offset < 20 ? 'next' : null, offset } });
function show(workflow = w) { return render(<SessionCacheProvider userId="test"><WorkflowContext.Provider value={{ workflow, refresh: vi.fn() }}><DataOverview /></WorkflowContext.Provider></SessionCacheProvider>); }
it('keeps analysis while paginating ten readable records without fetching the global profile again', async () => {
  vi.mocked(api.getData).mockImplementation(async (_run, query) => data(Number(query?.get('offset') || 0)));
  show();
  const table = await screen.findByRole('table', { name: 'Danh sách hồ sơ khách hàng' });
  expect(within(table).getAllByRole('row')).toHaveLength(11);
  expect(await screen.findByText('Tổng hồ sơ khách hàng')).toBeVisible();
  expect(screen.queryByRole('button', { name: 'Tìm hồ sơ trùng lặp' })).not.toBeInTheDocument();
  expect(screen.getByText('Person 0', { selector: 'td' })).toBeInTheDocument();
  expect(document.querySelector('pre')).toBeNull();
  expect(screen.queryByRole('button', { name: 'Thông tin đã làm sạch' })).not.toBeInTheDocument();
  expect(screen.queryByText('Chi tiết')).not.toBeInTheDocument();
  expect(screen.queryByText(/loại thông tin/)).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Trang cuối' }));
  await waitFor(() => expect(within(table).getAllByRole('row')).toHaveLength(6));
  expect(screen.getByText('Trang 3 / 3')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Trang sau' })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: 'Trang trước' }));
  expect(await screen.findByText('Person 10', { selector: 'td' })).toBeInTheDocument();
  expect(vi.mocked(api.getData).mock.calls.filter(([, query]) => query?.get('include_profile') === 'true')).toHaveLength(1);
  expect(vi.mocked(api.getData).mock.calls.filter(([, query]) => query?.get('offset') !== '0').every(([, query]) => query?.get('include_profile') === 'false')).toBe(true);
});
it('leaves clean notes blank and shows every issue directly in the notes column', async () => {
  const page = data();
  page.samples.items[0].quality_flags = [];
  page.samples.items[1].quality_flags = ['INVALID_GOV_ID_FORMAT', 'INVALID_EMAIL_FORMAT'];
  vi.mocked(api.getData).mockResolvedValue(page);
  show();
  const table = await screen.findByRole('table', { name: 'Danh sách hồ sơ khách hàng' });
  expect(within(table).getByRole('columnheader', { name: 'Ghi chú' })).toBeVisible();
  const rows = within(table).getAllByRole('row');
  expect(within(rows[1]).getAllByRole('cell').at(-1)).toBeEmptyDOMElement();
  const notes = within(rows[2]).getAllByRole('cell').at(-1)!;
  expect(notes).toHaveTextContent('Số giấy tờ chưa đúng định dạng');
  expect(notes).toHaveTextContent('Email sai định dạng');
  expect(notes.querySelector('details')).toBeNull();
});
it('allows an explicit retry only if automatic processing could not start', async () => {
  vi.mocked(api.getData).mockResolvedValue(data());
  vi.mocked(api.processData).mockResolvedValue({ operation_id: 'op' } as Awaited<ReturnType<typeof api.processData>>);
  show({ ...w, allowed_actions: ['process-data'] });
  fireEvent.click(screen.getByRole('button', { name: 'thử lại' }));
  await waitFor(() => expect(api.processData).toHaveBeenCalledTimes(1));
  expect(push).not.toHaveBeenCalled();
  expect(api.processData).toHaveBeenCalledTimes(1);
});
it('keeps the record table usable when the analysis read fails', async () => {
  vi.mocked(api.getData).mockImplementation(async (_run, query) => {
    if (query?.get('include_profile') === 'true') throw new Error('Analysis unavailable');
    return data();
  });
  show();
  expect(await screen.findByRole('alert')).toHaveTextContent('Analysis unavailable');
  expect(await screen.findByRole('table', { name: 'Danh sách hồ sơ khách hàng' })).toBeVisible();
});
it('shows global analysis and uploaded records together with independently cached reads', async () => {
  vi.mocked(api.getData).mockResolvedValue(data());
  show();
  expect(await screen.findByRole('heading', { name: 'Tổng quan hồ sơ' })).toBeVisible();
  await screen.findByText('Tổng hồ sơ khách hàng');
  expect(screen.getByLabelText('Thống kê toàn bộ dữ liệu')).toHaveTextContent('25');
  expect(screen.getByLabelText('Tỷ lệ nguồn CRM')).toHaveAttribute('max', '25');
  expect(await screen.findByRole('table', { name: 'Danh sách hồ sơ khách hàng' })).toBeVisible();
  expect(screen.getByRole('heading', { name: 'Danh sách hồ sơ khách hàng' })).toBeVisible();
  expect(screen.getByText('Nguồn tải lên')).toBeVisible();
  expect(screen.getByText('Hồ sơ cần kiểm tra')).toBeVisible();
  expect(document.querySelectorAll('.workflow-panel')).toHaveLength(1);
  expect(screen.getByRole('button',{name:'Giải thích chỉ số Tỷ lệ hồ sơ cần kiểm tra'})).toBeVisible();
  expect(vi.mocked(api.getData).mock.calls[0][1]?.get('include_records')).toBe('false');
});
it('keeps the loaded page and retries when the same navigation button is clicked after an error', async () => {
  let failed = false;
  vi.mocked(api.getData).mockImplementation(async (_run, query) => {
    const offset = Number(query?.get('offset') || 0);
    if (offset === 10 && !failed) { failed = true; throw new Error('Connection lost'); }
    return data(offset);
  });
  show();
  await screen.findByText('Person 0', { selector: 'td' });
  fireEvent.click(screen.getByRole('button', { name: 'Trang sau' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('Connection lost');
  expect(screen.getByText('Person 0', { selector: 'td' })).toBeInTheDocument();
  expect(screen.getByText('Trang 1 / 3')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Trang sau' }));
  expect(await screen.findByText('Person 10', { selector: 'td' })).toBeInTheDocument();
  expect(screen.getByText('Trang 2 / 3')).toBeInTheDocument();
});
