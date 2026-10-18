import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import PairGroupMenu from './PairGroupMenu';
import PairAgentEvidence from './PairAgentEvidence';
import type { PairBaseline, PairSummary } from '@/lib/workflowContracts';

afterEach(cleanup);
it('shows disabled embedding as a choice rather than a pending result',()=>{
 render(<PairAgentEvidence baseline={{embedding_enabled:false,score:6.9,decision:'REVIEW',field_evidence:[]} as unknown as PairBaseline}/>);
 expect(screen.getByText('Đã tắt trong phiên này')).toBeInTheDocument();
 expect(screen.queryByText('Chưa có kết quả')).not.toBeInTheDocument();
});
it('shows four MVP groups and selects a server-side scope without legacy C or subgroup menus', () => {
  const onGroup = vi.fn();
  const summary = { total: 4, needs_review: 4, groups: ['A_STRONG', 'A_CONTACT', 'B_SUPPORT', 'B_CONFLICT'].map(key => ({ key, total: 1, unresolved: 0, subgroups: [] })) } as unknown as PairSummary;
  render(<PairGroupMenu summary={summary} filter={{ taxonomy: 'mvp_v1' }} onGroup={onGroup} onSubgroup={vi.fn()} prefetchGroup={vi.fn()} />);
  fireEvent.click(screen.getByRole('button', { name: /Có thông tin mâu thuẫn/ }));
  expect(onGroup).toHaveBeenCalledWith('B_CONFLICT');
  expect(screen.getByRole('button', { name: /Có thông tin mâu thuẫn/ }).querySelector('strong')).toHaveTextContent('1');
  expect(screen.queryByText('Nhóm C')).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: /Nhóm chi tiết/ })).not.toBeInTheDocument();
  expect(screen.getAllByRole('button')).toHaveLength(5);
});

it('separates rule, real embedding and coordination, and does not equate cosine with match probability', () => {
  const baseline = { id:'p', left_id:'a', right_id:'b', field_evidence:[], score: 5, decision: 'REVIEW', primary_group: 'A', agent_disagreement: true, coordinator_version: 'r1_v1',
    embedding_evidence: { name: { status: 'OK', cosine: .9 }, address: { status: 'NOT_AVAILABLE', cosine: null } } } as PairBaseline;
  render(<PairAgentEvidence baseline={baseline} />);
  expect(screen.getByRole('heading', { name: 'Theo quy tắc' })).toBeVisible();
  expect(screen.getByRole('heading', { name: 'So sánh tên và địa chỉ' })).toBeVisible();
  expect(screen.getByRole('heading', { name: 'Tổng hợp' })).toBeVisible();
  expect(screen.getByText('Tên: 0,900')).toBeVisible();
  expect(screen.getByText('Chưa thống nhất')).toBeVisible();
  expect(screen.queryByText('90%')).not.toBeInTheDocument();
});

it('reports absent embedding and coordinator honestly for historical or failed evaluations', () => {
  render(<PairAgentEvidence baseline={{ score: 2, decision: 'REVIEW' } as PairBaseline} />);
  expect(screen.getAllByText('Chưa có kết quả')).toHaveLength(2);
  expect(screen.getByText('Điểm: 2 · Cần duyệt')).toBeVisible();
  expect(screen.queryByText(/Kiểm tra thông tin liên hệ/)).not.toBeInTheDocument();
});
