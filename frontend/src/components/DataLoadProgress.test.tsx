import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, expect, it } from 'vitest';
import type { WorkflowRead } from '@/lib/workflowContracts';
import DataLoadProgress from './DataLoadProgress';

afterEach(cleanup);
const workflow: WorkflowRead = { run_id: 'r', workflow_version: 2, workflow_state: 'DATA_PREPARING', workflow_revision: 1, decision_revision: 0, cluster_revision: 0, golden_revision: 0, counts: {}, allowed_actions: [], blockers: [], active_operation: { operation_id: 'prepare', run_id: 'r', operation_type: 'PREPARE', status: 'RUNNING', stage: 'prepare-read', progress: { processed: 250, total: 1000 }, error_code: null, result: {} }, retryable_operation: null };

it('shows measured progress, reserving 100 percent until DATA_READY', () => {
  const view = render(<DataLoadProgress workflow={workflow} />);
  expect(screen.getByRole('progressbar')).toHaveAttribute('value', '25');
  view.rerender(<DataLoadProgress workflow={{ ...workflow, active_operation: { ...workflow.active_operation!, progress: { processed: 1000, total: 1000 } } }} />);
  expect(screen.getByRole('progressbar')).toHaveAttribute('value', '99');
  view.rerender(<DataLoadProgress workflow={{ ...workflow, workflow_state: 'DATA_READY', active_operation: null }} />);
  expect(screen.getByRole('progressbar')).toHaveAttribute('value', '100');
  expect(screen.getByText('Đã tải dữ liệu')).toBeVisible();
});

it('handles unknown totals and invalid progress without inventing a percentage', () => {
  render(<DataLoadProgress workflow={{ ...workflow, active_operation: { ...workflow.active_operation!, status: 'QUEUED', progress: { processed: -1, total: 0 } } }} />);
  expect(screen.getByRole('progressbar')).toHaveAttribute('value', '0');
  expect(screen.getByRole('status')).toHaveTextContent('Đang chờ xử lý');
});
