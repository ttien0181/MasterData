import { render, screen } from '@testing-library/react';
import { expect, test, vi } from 'vitest';
import StructureControls from './StructureControls';

vi.mock('@/lib/api', () => ({ request: vi.fn(async () => ({ enabled: false, can_manage: false, active_constraints: [] })) }));

test('does not offer structure mutations when backend flag is off', async () => {
  render(<StructureControls workflow={{ run_id: 'r', workflow_state: 'GOLDEN_REVIEW', workflow_revision: 3,
    decision_revision: 1, cluster_revision: 1, golden_revision: 1 }} clusters={[]} refresh={async () => undefined} />);
  expect(await screen.findByText(/Tính năng tách và gộp lại nhóm chưa được bật/)).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: /Xác nhận thay đổi/ })).not.toBeInTheDocument();
});
