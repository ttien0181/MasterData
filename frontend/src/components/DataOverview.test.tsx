import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import DataOverview from './DataOverview';
import { WorkflowContext } from './SessionWorkspace';
import type { DataRead, WorkflowRead } from '@/lib/workflowContracts';
import * as api from '@/lib/workflowApi';
import { SessionCacheProvider } from './SessionCache';
vi.mock('@/lib/workflowApi', async orig => ({ ...await orig<typeof api>(), getData: vi.fn() }));
afterEach(() => { cleanup(); vi.clearAllMocks(); });
const w: WorkflowRead = { run_id: 'r', workflow_version: 2, workflow_state: 'DATA_READY', workflow_revision: 1, decision_revision: 0, cluster_revision: 0, golden_revision: 0, counts: {}, allowed_actions: [], blockers: [], active_operation: null, retryable_operation: null };
const data = (offset = 0): DataRead => ({ files: [{ filename: 'customers.csv', row_count: 25, sha256: 'a' }], schema: ['name', 'email'], profile: { record_count: 25, unit: 'records', source_counts: { CRM: 25 }, quality_flags: { INVALID_EMAIL_FORMAT: 2 }, field_quality: {}, records_with_quality_flags: 2, field_completeness: { name: { present: 25, missing: 0 }, email: { present: 20, missing: 5 } } }, samples: { items: Array.from({ length: Math.min(10, 25 - offset) }, (_, i) => ({ record_id: `r${offset+i}`, external_id: `${offset+i}`, source_system: 'CRM', raw_fields: { name: `Person ${offset+i}`, email: 'bad' }, normalized_fields: { name: `Normalized ${offset+i}` }, quality_flags: ['INVALID_EMAIL_FORMAT'], extra_fields: { custom: 'Extra value' } })), total: 25, next_cursor: offset < 20 ? 'next' : null, offset } });
function show() { render(<SessionCacheProvider userId="test"><WorkflowContext.Provider value={{ workflow: w, refresh: vi.fn() }}><DataOverview /></WorkflowContext.Provider></SessionCacheProvider>); }
it('renders global metrics, readable cells, normalization and ten records per page', async () => {
  vi.mocked(api.getData).mockImplementation(async (_run, query) => data(Number(query?.get('offset') || 0)));
  show();
  const table = await screen.findByRole('table', { name: 'Bản ghi dữ liệu' });
  expect(within(table).getAllByRole('row')).toHaveLength(11);
  expect(screen.getByText('Tổng bản ghi')).toBeInTheDocument();
  expect(screen.getByText('Person 0', { selector: 'td' })).toBeInTheDocument();
  expect(document.querySelector('pre')).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Chuẩn hóa' }));
  expect(screen.getByText('Normalized 0', { selector: 'td' })).toBeInTheDocument();
  fireEvent.click(screen.getAllByText('Chi tiết', { selector: 'summary' })[0]);
  expect(screen.getAllByText('Extra value').length).toBeGreaterThan(0);
  fireEvent.click(screen.getByRole('button', { name: 'Trang cuối' }));
  await waitFor(() => expect(within(table).getAllByRole('row')).toHaveLength(6));
  expect(screen.getByText('Trang 3 / 3')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Trang sau' })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: 'Trang trước' }));
  expect(await screen.findByText('Normalized 10', { selector: 'td' })).toBeInTheDocument();
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
