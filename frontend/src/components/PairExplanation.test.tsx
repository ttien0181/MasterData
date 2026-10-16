import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import PairExplanation from './PairExplanation';
import { SessionCacheProvider } from './SessionCache';
import { WorkflowContext } from './SessionWorkspace';
import * as api from '@/lib/workflowApi';
import type { PairRead, WorkflowRead } from '@/lib/workflowContracts';
const state=vi.hoisted(()=>({capabilities:['explain']}));
vi.mock('./AppSession',()=>({useAppUser:()=>state}));
vi.mock('next/navigation',()=>({useRouter:()=>({push:vi.fn()})}));
vi.mock('@/lib/workflowApi',async original=>({...await original<typeof api>(),explainWorkflowPair:vi.fn()}));
const pair={pair_id:'p',revision:0} as PairRead;
const workflow={run_id:'r',workflow_revision:3,decision_revision:1,assessment_id:'a',active_operation:null,blockers:[]} as unknown as WorkflowRead;
const ready={status:'READY',explanation:{summary_vi:'Tên tương đồng.',attention_fields:['name'],limitations:[]}};
const refresh=vi.fn();
function host(w=workflow){return <SessionCacheProvider userId="u"><WorkflowContext.Provider value={{workflow:w,refresh}}><PairExplanation key={w.workflow_revision} pair={pair}/></WorkflowContext.Provider></SessionCacheProvider>;}
beforeEach(()=>{state.capabilities=['explain'];vi.clearAllMocks();});
afterEach(cleanup);
it('requests only on click and reuses a result for the same revision',async()=>{
  vi.mocked(api.explainWorkflowPair).mockResolvedValue(ready);
  const view=render(host());expect(api.explainWorkflowPair).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button'));
  await screen.findByText('Tên tương đồng.');
  expect(screen.getByText('Giải thích AI')).toBeVisible();
  expect(screen.getByText('Cần đối chiếu: Họ tên')).toBeVisible();
  view.rerender(host());expect(api.explainWorkflowPair).toHaveBeenCalledTimes(1);
  view.rerender(host({...workflow,workflow_revision:4}));
  expect(screen.queryByText('Tên tương đồng.')).not.toBeInTheDocument();
  expect(screen.getByRole('button')).toBeEnabled();
});
it('discards stale answers and asks for refreshed context',async()=>{
  vi.mocked(api.explainWorkflowPair).mockResolvedValue({...ready,status:'STALE'});
  render(host());fireEvent.click(screen.getByRole('button'));
  expect(await screen.findByRole('alert')).toHaveTextContent('Dữ liệu đã thay đổi');
  expect(screen.queryByText('Tên tương đồng.')).not.toBeInTheDocument();
  await waitFor(()=>expect(refresh).toHaveBeenCalledTimes(1));
});
it('keeps one request in flight and aborts when the comparison closes',async()=>{
  vi.mocked(api.explainWorkflowPair).mockReturnValue(new Promise(()=>undefined));
  const view=render(host());const button=screen.getByRole('button');fireEvent.click(button);fireEvent.click(button);
  expect(api.explainWorkflowPair).toHaveBeenCalledTimes(1);expect(button).toBeDisabled();
  const signal=vi.mocked(api.explainWorkflowPair).mock.calls[0][3];
  view.unmount();expect(signal?.aborted).toBe(true);
});
it('does not expose the action to a viewer',()=>{
  state.capabilities=['read'];render(host());expect(screen.queryByRole('button')).not.toBeInTheDocument();
  expect(api.explainWorkflowPair).not.toHaveBeenCalled();
});
it('allows an explicit retry after a network failure',async()=>{
  vi.mocked(api.explainWorkflowPair).mockRejectedValueOnce(new Error('network')).mockResolvedValueOnce(ready);
  render(host());fireEvent.click(screen.getByRole('button'));await screen.findByRole('alert');
  fireEvent.click(screen.getByRole('button'));await screen.findByText('Tên tương đồng.');
  expect(api.explainWorkflowPair).toHaveBeenCalledTimes(2);
});
