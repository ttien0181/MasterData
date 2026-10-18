import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import DecisionOverview from './DecisionOverview';
import { WorkflowContext } from './SessionWorkspace';
import { SessionCacheProvider } from './SessionCache';
import * as api from '@/lib/workflowApi';
import type { WorkflowRead } from '@/lib/workflowContracts';
vi.mock('next/navigation', () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock('@/lib/workflowApi', async original => ({ ...await original<typeof api>(), getPairSummary: vi.fn(), getPairs: vi.fn(), processData: vi.fn() }));
afterEach(() => { cleanup(); vi.clearAllMocks(); });
it('starts the full processing command from the decisions entry point', async () => {
  const workflow: WorkflowRead = { run_id: 'ready', workflow_version: 2, workflow_state: 'DATA_READY', workflow_revision: 3, decision_revision: 0, cluster_revision: 0, golden_revision: 0, counts: {}, allowed_actions: ['process-data'], blockers: [], active_operation: null, retryable_operation: null };
  vi.mocked(api.processData).mockResolvedValue({ operation_id: 'op', run_id: 'ready', operation_type: 'DECIDE', status: 'QUEUED', stage: 'queued', progress: {}, result: {}, error_code: null });
  render(<SessionCacheProvider userId="u"><WorkflowContext.Provider value={{ workflow, refresh: vi.fn() }}><DecisionOverview /></WorkflowContext.Provider></SessionCacheProvider>);
  fireEvent.click(screen.getByRole('button', { name: 'Tìm hồ sơ trùng lặp' }));
  await waitFor(() => expect(api.processData).toHaveBeenCalledWith('ready', expect.objectContaining({ expected_workflow_revision: 3 })));
  expect(api.processData).toHaveBeenCalledTimes(1);
});
it('renders numeric coverage and all four group totals without redundant review panels', async () => {
  vi.mocked(api.getPairSummary).mockResolvedValue({ total: 100, needs_review: 40, accepted: 20, denied: 10, kept_separate: 5, deferred: 5, blocked: 12, manual_available: 40, auto_available: 0, decision_revision: 1, assessment: { coordinator: { version: 'r1_v1' } }, groups: [{ key: 'A_CONTACT', total: 100, subgroups: [] }] });
  const workflow: WorkflowRead = { run_id: 'r', workflow_version: 2, workflow_state: 'DECIDE_REVIEW', workflow_revision: 2, decision_revision: 1, cluster_revision: 0, golden_revision: 0, counts: { records: { value: 100, unit: 'records' }, candidates: { value: 120, unit: 'pairs' } }, allowed_actions: [], blockers: [], active_operation: null, retryable_operation: null, matching_agents: { status: 'OK', warning: null, coverage: { rule_pairs: 120, embedding_pairs: 110, expected_pairs: 120 }, artifact_id: 'a', automation_allowed: false } };
  render(<SessionCacheProvider userId="u"><WorkflowContext.Provider value={{ workflow, refresh: vi.fn() }}><DecisionOverview /></WorkflowContext.Provider></SessionCacheProvider>);
  await screen.findByRole('group', { name: 'Tổng cặp hồ sơ' });
  expect(screen.getByRole('group', { name: 'Tổng cặp hồ sơ' })).toHaveTextContent('100');
  expect(screen.getByRole('group', { name: 'Cặp đã kiểm tra thông tin' })).toHaveTextContent('120');
  expect(screen.getByRole('group', { name: 'Cặp đã so sánh tên, địa chỉ' })).toHaveTextContent('110');
  expect(screen.queryByRole('group', { name: 'Giảm so sánh' })).not.toBeInTheDocument();
  expect(screen.queryByRole('heading', { name: 'Kết quả duyệt' })).not.toBeInTheDocument();
  expect(screen.queryByRole('heading', { name: 'Thông tin hỗ trợ đối chiếu' })).not.toBeInTheDocument();
  expect(screen.getByRole('group', { name: 'Nhóm' }).querySelectorAll('button')).toHaveLength(5);
  expect(api.getPairs).not.toHaveBeenCalled();
  expect(screen.queryByRole('table')).not.toBeInTheDocument();
});

it('keeps missing agent coverage distinct from zero', async () => {
  vi.mocked(api.getPairSummary).mockResolvedValue({ total: 0, needs_review: 0, accepted: 0, denied: 0, kept_separate: 0, deferred: 0, blocked: 0, manual_available: 0, auto_available: 0, decision_revision: 1, groups: [] });
  const workflow: WorkflowRead = { run_id: 'history', workflow_version: 2, workflow_state: 'DECIDE_REVIEW', workflow_revision: 1, decision_revision: 1, cluster_revision: 0, golden_revision: 0, counts: {}, allowed_actions: [], blockers: [], active_operation: null, retryable_operation: null };
  render(<SessionCacheProvider userId="u"><WorkflowContext.Provider value={{ workflow, refresh: vi.fn() }}><DecisionOverview /></WorkflowContext.Provider></SessionCacheProvider>);
  await screen.findByRole('group', { name: 'Cặp đã so sánh tên, địa chỉ' });
  for (const name of ['Cặp đã kiểm tra thông tin', 'Cặp đã so sánh tên, địa chỉ']) expect(screen.getByRole('group', { name })).toHaveTextContent('—');
  expect(screen.getByRole('group', { name: 'Tổng cặp hồ sơ' })).toHaveTextContent('0');
});
