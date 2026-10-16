import { render, screen } from '@testing-library/react';
import { expect, test, vi } from 'vitest';
import MatchingProfilePanel from './MatchingProfilePanel';

vi.mock('@/lib/api', () => ({ request: vi.fn(async (path: string) => {
  if (path.endsWith('/binding')) return { profile_id: 'p1', profile_digest: 'digest-p1', revision: 3 };
  if (path.endsWith('/profiles')) return [{ id: 'p1', version: 1, digest: 'digest-p1', lifecycle: 'ACTIVE',
    score_unit: 'p119_rule_v1', config: { thresholds: { mode: 'EXISTING_RULE_POLICY' } } }];
  if (path.endsWith('/feedback')) return [];
  if (path.endsWith('/evaluations')) return [];
  throw new Error('Unsupported test path');
}) }));

test('shows bound profile and uncalibrated rule units without invented metrics', async () => {
  render(<MatchingProfilePanel runId="run1" />);
  expect(await screen.findByText(/p119_rule_v1/)).toBeInTheDocument();
  expect(screen.getByText(/EXISTING_RULE_POLICY/)).toBeInTheDocument();
  expect(screen.getByText(/Chưa có report đánh giá/)).toBeInTheDocument();
  expect(screen.queryByText(/100%/)).not.toBeInTheDocument();
});
