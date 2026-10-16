import { fireEvent, render, screen } from '@testing-library/react';
import { expect, test, vi } from 'vitest';
import PairReviewRow from './PairReviewRow';
import type { PairRead } from '@/lib/workflowContracts';

test('shows a mergeable conclusion while leaving acceptance to an explicit reviewer action', () => {
  const decide = vi.fn();
  const pair: PairRead = {
    pair_id: 'clear', revision: 0, decision: 'UNDECIDED', score_unit: 'p119_rule_v1',
    baseline: { id: 'clear', left_id: 'a', right_id: 'b', primary_group: 'A',
      subgroup: 'A3_NAME_PHONE_DOB', decision: 'MERGEABLE', triage_status: 'REVIEW',
      score: 12.3, reason: 'CLEAR_PATTERN_REVIEW', field_evidence: [] },
    eligibility: { manual_allowed: true, auto_allowed: false, blocked_reasons: [] },
  };
  const noop = () => undefined;
  render(<table><tbody><PairReviewRow pair={pair} selected={false} toggle={noop}
    canWrite busy={false} decide={decide} compare={noop}/></tbody></table>);
  expect(screen.getByText('Đủ bằng chứng gộp')).toBeInTheDocument();
  expect(screen.queryByText('Đủ điều kiện tự động')).not.toBeInTheDocument();
  expect(decide).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Accept cặp clear' }));
  expect(decide).toHaveBeenCalledWith('merge');
});
