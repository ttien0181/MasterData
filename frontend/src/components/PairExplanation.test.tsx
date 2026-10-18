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
vi.mock('@/lib/workflowApi',async original=>({...await original<typeof api>(),streamWorkflowPairExplanation:vi.fn()}));
const pair={pair_id:'p',revision:0} as PairRead;
const workflow={run_id:'r',workflow_revision:3,decision_revision:1,assessment_id:'a',active_operation:null,blockers:[]} as unknown as WorkflowRead;
const ready={status:'READY',explanation:{summary_vi:'Tên tương đồng.',attention_fields:['name'],limitations:[]}};
const refresh=vi.fn().mockResolvedValue(undefined);
function host(w=workflow){return <SessionCacheProvider userId="u"><WorkflowContext.Provider value={{workflow:w,refresh}}><PairExplanation pair={pair}/></WorkflowContext.Provider></SessionCacheProvider>;}
beforeEach(()=>{state.capabilities=['explain'];vi.clearAllMocks();});
afterEach(cleanup);
it('requests only on click and reuses a result for the same revision',async()=>{
  vi.mocked(api.streamWorkflowPairExplanation).mockResolvedValue(ready);
  const view=render(host());expect(api.streamWorkflowPairExplanation).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button'));
  await screen.findByText('Tên tương đồng.');
  expect(screen.getByRole('heading', {name:'Master Bot'})).toBeVisible();
  expect(screen.getByText('Bạn nhớ kiểm tra lại họ tên trước khi quyết định gộp nhé.')).toBeVisible();
  view.rerender(host());expect(api.streamWorkflowPairExplanation).toHaveBeenCalledTimes(1);
  view.rerender(host({...workflow,workflow_revision:4}));
  expect(screen.queryByText('Tên tương đồng.')).not.toBeInTheDocument();
  expect(screen.getByRole('button')).toBeEnabled();
});
it('discards stale answers and asks for refreshed context',async()=>{
  vi.mocked(api.streamWorkflowPairExplanation).mockResolvedValue({...ready,status:'STALE'});
  render(host());fireEvent.click(screen.getByRole('button'));
  expect(await screen.findByRole('alert')).toHaveTextContent('Dữ liệu đã thay đổi');
  expect(screen.queryByText('Tên tương đồng.')).not.toBeInTheDocument();
  await waitFor(()=>expect(refresh).toHaveBeenCalledTimes(1));
});
it('keeps one request in flight and aborts when the comparison closes',async()=>{
  vi.mocked(api.streamWorkflowPairExplanation).mockReturnValue(new Promise(()=>undefined));
  const view=render(host());const button=screen.getByRole('button');fireEvent.click(button);fireEvent.click(button);
  expect(api.streamWorkflowPairExplanation).toHaveBeenCalledTimes(1);expect(button).toBeDisabled();
  const signal=vi.mocked(api.streamWorkflowPairExplanation).mock.calls[0][4];
  view.unmount();expect(signal?.aborted).toBe(true);
});
it('does not expose the action to a viewer',()=>{
  state.capabilities=['read'];render(host());expect(screen.queryByRole('button')).not.toBeInTheDocument();
  expect(api.streamWorkflowPairExplanation).not.toHaveBeenCalled();
});
it('shows received text before the final result and clears partial text after failure',async()=>{
 let reject!:(reason:Error)=>void;
 vi.mocked(api.streamWorkflowPairExplanation).mockImplementation((_run,_pair,_body,delta)=>{
  delta('Đang đối chiếu tên');return new Promise((_resolve,fail)=>{reject=fail;});
 });
 render(host());fireEvent.click(screen.getByRole('button'));
 await screen.findByText('Đang đối chiếu tên');
 expect(screen.queryByRole('heading',{name:'Master Bot'})).not.toBeInTheDocument();
 await act(async()=>reject(new Error('truncated')));
 expect(screen.queryByText('Đang đối chiếu tên')).not.toBeInTheDocument();
 expect(screen.getByRole('button')).toBeEnabled();
});
it('aborts and resets on revision change without needing parent remount',async()=>{
 vi.mocked(api.streamWorkflowPairExplanation).mockReturnValue(new Promise(()=>undefined));
 const view=render(host());fireEvent.click(screen.getByRole('button'));
 const signal=vi.mocked(api.streamWorkflowPairExplanation).mock.calls[0][4];
 view.rerender(<SessionCacheProvider userId="u"><WorkflowContext.Provider value={{workflow:{...workflow,decision_revision:2},refresh}}><PairExplanation pair={pair}/></WorkflowContext.Provider></SessionCacheProvider>);
 expect(signal?.aborted).toBe(true);expect(screen.getByRole('button')).toBeEnabled();
});
it('allows an explicit retry after a network failure',async()=>{
  vi.mocked(api.streamWorkflowPairExplanation).mockRejectedValueOnce(new Error('network')).mockResolvedValueOnce(ready);
  render(host());fireEvent.click(screen.getByRole('button'));await screen.findByRole('alert');
  fireEvent.click(screen.getByRole('button'));await screen.findByText('Tên tương đồng.');
  expect(api.streamWorkflowPairExplanation).toHaveBeenCalledTimes(2);
});

it.each(['READY','TEMPLATE','STALE'])('only caches READY when reopening (%s)',async status=>{
  vi.mocked(api.streamWorkflowPairExplanation).mockResolvedValue({...ready,status});
  function view(open:boolean){return <SessionCacheProvider userId="u"><WorkflowContext.Provider value={{workflow,refresh}}>{open&&<PairExplanation pair={pair}/>}</WorkflowContext.Provider></SessionCacheProvider>;}
  const host=render(view(true));fireEvent.click(screen.getByRole('button'));
  await waitFor(()=>expect(screen.getByRole('button')).toHaveTextContent(status==='READY'?'Đã có':'Thử lại'));
  host.rerender(view(false));host.rerender(view(true));
  if(status==='READY') expect(screen.getByText('Tên tương đồng.')).toBeVisible();
  else {expect(screen.queryByText('Tên tương đồng.')).not.toBeInTheDocument();expect(screen.getByRole('button')).toHaveTextContent('Hỏi Master Bot');}
});

it('appends real deltas and replaces them with the validated final text and a reminder without limitations',async()=>{
  let delta!:(text:string)=>void,finish!:(value:api.ExplanationRead)=>void;
  vi.mocked(api.streamWorkflowPairExplanation).mockImplementation((_run,_pair,_body,onDelta)=>{
    delta=onDelta;return new Promise(resolve=>{finish=resolve;});
  });
  render(host());fireEvent.click(screen.getByRole('button'));
  act(()=>delta('Tên '));await screen.findByText('Tên');
  act(()=>delta('giống nhau'));await screen.findByText('Tên giống nhau');
  await act(async()=>finish({...ready,explanation:{...ready.explanation,limitations:['Chỉ là gợi ý.']}}));
  expect(screen.queryByText('Tên giống nhau')).not.toBeInTheDocument();
  expect(screen.getByText('Tên tương đồng.')).toBeVisible();
  expect(screen.queryByText('Giới hạn')).not.toBeInTheDocument();expect(screen.queryByText('Chỉ là gợi ý.')).not.toBeInTheDocument();
});

it.each([{active_operation:{operation_id:'op'}},{blockers:[{code:'RUN_ARCHIVED'}]}])('keeps workflow action gates',async gate=>{
  render(host({...workflow,...gate} as WorkflowRead));
  expect(screen.getByRole('button')).toBeDisabled();fireEvent.click(screen.getByRole('button'));
  expect(api.streamWorkflowPairExplanation).not.toHaveBeenCalled();
});

it.each(['TEMPLATE','STALE'])('removes provisional text on terminal %s',async status=>{
  let finish!:(value:api.ExplanationRead)=>void;
  vi.mocked(api.streamWorkflowPairExplanation).mockImplementation((_run,_pair,_body,delta)=>{
    delta('Unvalidated provisional text');return new Promise(resolve=>{finish=resolve;});
  });
  render(host());fireEvent.click(screen.getByRole('button'));
  await screen.findByText('Unvalidated provisional text');
  await act(async()=>finish({status,explanation:null}));
  expect(screen.queryByText('Unvalidated provisional text')).not.toBeInTheDocument();
  expect(screen.getByRole('button')).toBeEnabled();
});

it('ignores late deltas and terminal results after an identity change',async()=>{
  let delta!:(text:string)=>void,finish!:(value:api.ExplanationRead)=>void;
  vi.mocked(api.streamWorkflowPairExplanation).mockImplementation((_run,_pair,_body,onDelta)=>{
    delta=onDelta;return new Promise(resolve=>{finish=resolve;});
  });
  const view=render(host());fireEvent.click(screen.getByRole('button'));
  view.rerender(host({...workflow,decision_revision:2}));
  await act(async()=>{delta('Late text');finish(ready);});
  expect(screen.queryByText('Late text')).not.toBeInTheDocument();
  expect(screen.queryByText('Tên tương đồng.')).not.toBeInTheDocument();
  view.rerender(host());
  expect(screen.queryByText('Tên tương đồng.')).not.toBeInTheDocument();
  expect(screen.getByRole('button')).toBeEnabled();
});

it('bounds a large chunk reveal and clears its timer on close',async()=>{
  vi.useFakeTimers();
  try {
    let delta!:(text:string)=>void;
    vi.mocked(api.streamWorkflowPairExplanation).mockImplementation((_run,_pair,_body,onDelta)=>{
      delta=onDelta;return new Promise(()=>undefined);
    });
    const view=render(host());fireEvent.click(screen.getByRole('button'));
    const chunk='Real received text '.repeat(100);
    act(()=>delta(chunk));
    expect(screen.queryByText(chunk.trim())).not.toBeInTheDocument();
    await act(async()=>vi.advanceTimersByTime(176));
    expect(screen.getByText(chunk.trim())).toBeVisible();
    act(()=>delta(' more pending text '.repeat(50)));
    expect(vi.getTimerCount()).toBeGreaterThan(0);
    view.unmount();expect(vi.getTimerCount()).toBe(0);
  } finally {vi.useRealTimers();}
});
