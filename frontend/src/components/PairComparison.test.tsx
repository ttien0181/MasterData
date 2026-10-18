import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { expect, test, vi } from 'vitest';
import PairComparison from './PairComparison';
import type { PairRead } from '@/lib/workflowContracts';

test('shows field cosine and unavailable evidence without enabling acceptance', () => {
  const pair: PairRead = { pair_id: 'hybrid', revision: 0, decision: 'UNDECIDED', score_unit: 'p119_rule_v1',
    eligibility: { manual_allowed: false, auto_allowed: false, blocked_reasons: ['INSUFFICIENT_EVIDENCE'] },
    baseline: { id: 'hybrid', left_id: 'a', right_id: 'b', decision: 'REVIEW', score: 7.25, field_evidence: [],
      coordinator_version: 'r1_v1', agent_disagreement: true, evidence_tags: ['AGENT_DISAGREEMENT_NAME'],
      embedding_evidence: { name: { status: 'OK', cosine: .9 }, address: { status: 'NOT_AVAILABLE', cosine: null } } } };
  const reviewGroup=vi.fn();
  render(<PairComparison pair={pair} close={() => undefined} reviewGroup={reviewGroup} />);
  expect(screen.getByText('Tên: 0,900')).toBeInTheDocument();
  expect(screen.getByText('Địa chỉ: Chưa có')).toBeInTheDocument();
  expect(screen.getByText('Chưa thống nhất')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Đối chiếu nhóm' }));
  expect(reviewGroup).toHaveBeenCalledTimes(1);
});

test('distinguishes literal equality from verified identity evidence', () => {
  cleanup();
  const record = { record_id: 'a', source_system: 'CRM', raw_fields: { government_id: '001208004954' }, normalized_fields: {}, extra_fields: { is_active: true } };
  const pair = { pair_id: 'p', revision: 0, score_unit: 'p119_rule_v1', decision: 'UNDECIDED', eligibility: { manual_allowed: false, auto_allowed: false, blocked_reasons: [] },
    left_record: record, right_record: { ...record, record_id: 'b' },
    baseline: { id: 'p', left_id: 'a', right_id: 'b', score: 0, decision: 'REVIEW', field_evidence: [{ field: 'government_id', state: 'NC', similarity: 0 }] } } as PairRead;
  render(<PairComparison pair={pair} close={() => undefined} />);
  const idRow = screen.getByRole('row', { name: /Giấy tờ định danh/ });
  expect(within(idRow).getByText('Trùng giá trị · chưa xác minh')).toBeVisible();
  expect(screen.queryByText('Chưa đối chiếu')).not.toBeInTheDocument();
  expect(screen.queryByText(/Quyết định: UNDECIDED/)).not.toBeInTheDocument();
  expect(screen.getAllByRole('checkbox', { name: 'Trường bổ sung' })).toHaveLength(1);
  expect(screen.queryByRole('row', { name: /is_active/ })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('checkbox', { name: 'Trường bổ sung' }));
  expect(screen.getByRole('row', { name: /is_active/ })).toHaveTextContent('Trùng giá trị');
});
