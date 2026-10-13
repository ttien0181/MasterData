import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { WorkflowContext } from './SessionWorkspace';
import { SessionCacheProvider } from './SessionCache';
import DecisionWorkspace from './DecisionWorkspace';
import PairBulkConfirmation from './PairBulkConfirmation';
import { ApiError } from '@/lib/api';
import * as api from '@/lib/workflowApi';
import type { WorkflowRead } from '@/lib/workflowContracts';
vi.mock('next/navigation', () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock('@/lib/workflowApi', async original => ({ ...await original<typeof api>(),
  getWorkflow:vi.fn(), getOperation:vi.fn(), getPair:vi.fn(), finalizeDecisions:vi.fn(), getPairs: vi.fn(), getPairSummary: vi.fn(), previewBulkDecisions: vi.fn(), bulkDecisions: vi.fn(), decidePair: vi.fn(),
}));
const workflow: WorkflowRead = { run_id:'r', workflow_version:2, workflow_state:'DECIDE_REVIEW', workflow_revision:4, decision_revision:1, cluster_revision:0, golden_revision:0, counts:{}, allowed_actions:['decide-pair','bulk-decisions'], blockers:[], active_operation:null,retryable_operation:null };
const pair = (index: number) => ({ pair_id:`p${index}`, revision:0, decision:'UNDECIDED', score_unit:'p119_rule_v1', baseline:{ id:`p${index}`,left_id:`a${index}`,right_id:`b${index}`,primary_group:'A',subgroup:'A1_NAME_PHONE',decision:'REVIEW',score:5,field_evidence:[] }, eligibility:{manual_allowed:true,auto_allowed:false,blocked_reasons:[]}, left_record:{record_id:`a${index}`,source_system:'CRM',raw_fields:{name:`Person ${index}`,phone:'0901234567'},normalized_fields:{name:`person ${index}`},extra_fields:{}}, right_record:{record_id:`b${index}`,source_system:'BILL',raw_fields:{name:`Other ${index}`},normalized_fields:{},extra_fields:{}} });
beforeEach(() => {
  vi.clearAllMocks();sessionStorage.clear();
  vi.mocked(api.getWorkflow).mockResolvedValue(workflow);
  vi.mocked(api.getOperation).mockResolvedValue({operation_id:'op',run_id:'r',operation_type:'BULK_DECISIONS',status:'COMPLETED',stage:'completed',progress:{},error_code:null,result:{}});
  vi.mocked(api.getPairs).mockImplementation(async (_run, query) => { const offset=Number(query?.get('offset') || 0), limit=Number(query?.get('limit') || 25); return {items:Array.from({length:Math.min(limit,12-offset)},(_,i)=>pair(i+offset)),total:12,offset,next_cursor:offset+limit<12?'p9':null}; });
  vi.mocked(api.getPairSummary).mockResolvedValue({total:12,needs_review:12,accepted:0,denied:0,kept_separate:0,deferred:0,blocked:0,manual_available:12,auto_available:0,decision_revision:1,groups:[{key:'A',total:12,subgroups:[{key:'A1_NAME_PHONE',total:12}]}]});
  vi.mocked(api.previewBulkDecisions).mockResolvedValue({total:12,applicable:10,blocked:2,skipped:0,workflow_revision:4,decision_revision:1,preview_token:'reviewed-token'});
  vi.mocked(api.getPair).mockResolvedValue(pair(0));
  vi.mocked(api.bulkDecisions).mockResolvedValue({operation_id:'op',run_id:'r',operation_type:'BULK_DECISIONS',status:'QUEUED',stage:'queued',progress:{},error_code:null,result:{}});
  vi.mocked(api.decidePair).mockResolvedValue({...pair(0),decision:'NON_MATCH'});
});
afterEach(cleanup);
function show() { return render(<SessionCacheProvider userId="u"><WorkflowContext.Provider value={{workflow,refresh:vi.fn().mockResolvedValue(undefined)}}><DecisionWorkspace /></WorkflowContext.Provider></SessionCacheProvider>); }
it('keeps other pair actions enabled and queues a second click without parallel writes',async()=>{
  let complete!: (value:ReturnType<typeof pair>)=>void;
  vi.mocked(api.decidePair).mockImplementationOnce(()=>new Promise(resolve=>{complete=resolve;}));
  show();await screen.findByText('Person 0');
  fireEvent.click(screen.getByRole('button',{name:'Accept cặp p0'}));
  await waitFor(()=>expect(api.decidePair).toHaveBeenCalledTimes(1));
  expect(screen.getByRole('button',{name:'Accept cặp p0'})).toBeDisabled();
  expect(screen.getByRole('button',{name:'Accept cặp p1'})).toBeEnabled();
  fireEvent.click(screen.getByRole('button',{name:'Accept cặp p1'}));
  expect(api.decidePair).toHaveBeenCalledTimes(1);
  await screen.findByText(/Hàng chờ duyệt/);
  complete({...pair(0),revision:1,decision:'ACCEPTED'});
  await waitFor(()=>expect(api.decidePair).toHaveBeenCalledTimes(2));
  expect(api.decidePair).toHaveBeenLastCalledWith('r','p1',expect.objectContaining({expected_pair_revision:0,decision:'merge'}));
});
it('accepts another scope while a bulk job is active without sending it concurrently',async()=>{
  const active={...workflow,allowed_actions:['queue-decisions'],active_operation:{operation_id:'existing',run_id:'r',operation_type:'BULK_DECISIONS',status:'RUNNING',stage:'review',progress:{},error_code:null,result:{}}};
  vi.mocked(api.getWorkflow).mockResolvedValue(active);
  render(<SessionCacheProvider userId="u"><WorkflowContext.Provider value={{workflow:active,refresh:vi.fn()}}><DecisionWorkspace/></WorkflowContext.Provider></SessionCacheProvider>);
  await screen.findByText('Person 0');
  fireEvent.change(screen.getByRole('combobox',{name:'Group'}),{target:{value:'A'}});
  fireEvent.change(screen.getByRole('combobox',{name:'Phạm vi thao tác'}),{target:{value:'subgroup'}});
  fireEvent.change(screen.getByRole('combobox',{name:'SubGroup'}),{target:{value:'A1_NAME_PHONE'}});
  await waitFor(()=>expect(screen.getByRole('button',{name:'Accept theo phạm vi'})).toBeEnabled());
  fireEvent.click(screen.getByRole('button',{name:'Accept theo phạm vi'}));
  const confirm=await screen.findByRole('button',{name:'Xác nhận Accept'});await waitFor(()=>expect(confirm).toBeEnabled());fireEvent.click(confirm);
  await screen.findByText(/Hàng chờ duyệt/);
  expect(api.bulkDecisions).not.toHaveBeenCalled();
});
it('continues queued decisions when another workspace tab is shown',async()=>{
  let complete!: (value:ReturnType<typeof pair>)=>void;
  vi.mocked(api.decidePair).mockImplementationOnce(()=>new Promise(resolve=>{complete=resolve;}));
  const host=(visible:boolean)=><SessionCacheProvider userId="u"><WorkflowContext.Provider value={{workflow,refresh:vi.fn().mockResolvedValue(undefined)}}>{visible?<DecisionWorkspace/>:<p>Tab dữ liệu</p>}</WorkflowContext.Provider></SessionCacheProvider>;
  const view=render(host(true));await screen.findByText('Person 0');
  fireEvent.click(screen.getByRole('button',{name:'Accept cặp p0'}));await waitFor(()=>expect(api.decidePair).toHaveBeenCalledTimes(1));
  fireEvent.click(screen.getByRole('button',{name:'Accept cặp p1'}));view.rerender(host(false));
  complete({...pair(0),revision:1,decision:'ACCEPTED'});
  await waitFor(()=>expect(api.decidePair).toHaveBeenCalledTimes(2));
  view.rerender(host(true));expect(await screen.findByRole('region',{name:'Hàng chờ duyệt'})).toBeVisible();
});
it('clearly separates assessment controls and synthetic scope from Accept/Deny', async()=>{
  show(); await screen.findByText('Person 0');
  expect(screen.getByRole('region',{name:'Đánh giá bằng chứng'})).toBeInTheDocument();
  expect(screen.getByRole('button',{name:'Đánh giá lại bằng chứng'})).toBeDisabled();
  expect(screen.getByRole('button',{name:'LLM đánh giá cặp mơ hồ'})).toBeDisabled();
  expect(screen.getByRole('checkbox',{name:/Phiên dùng benchmark tổng hợp/})).not.toBeChecked();
});
it('renders ten human-readable pairs and supports forward/back pagination', async () => {
  show(); await screen.findByText('Person 0');
  expect(screen.getAllByRole('checkbox', {name:/Chọn cặp/})).toHaveLength(10);
  expect(screen.getAllByText('Tên và điện thoại', {exact:false}).length).toBeGreaterThan(0);
  fireEvent.click(screen.getByRole('button',{name:'Trang tiếp'}));
  await screen.findByText('Person 10');
  expect(screen.getAllByRole('checkbox',{name:/Chọn cặp/})).toHaveLength(2);
  fireEvent.click(screen.getByRole('button',{name:'Trang trước'}));
  await screen.findByText('Person 0');
});
it('previews an explicit whole-run scope and sends nothing when cancelled', async () => {
  show(); await screen.findByText('Person 0');
  fireEvent.change(screen.getByRole('combobox',{name:'Phạm vi thao tác'}),{target:{value:'all'}});
  fireEvent.click(screen.getByRole('button',{name:'Accept theo phạm vi'}));
  await screen.findByText(/10 cặp có thể áp dụng/);
  expect(screen.getByText(/2 cặp bị chặn/)).toBeInTheDocument();
  expect(api.previewBulkDecisions).toHaveBeenCalledWith('r',expect.objectContaining({decision:'merge',scope:{filter:{}},auto_only:false}),expect.any(AbortSignal));
  fireEvent.click(screen.getByRole('button',{name:'Hủy'}));
  expect(api.bulkDecisions).not.toHaveBeenCalled();
});
it('refreshes stale preview state without retrying a decision or changing frozen intent',async()=>{
  const refresh=vi.fn().mockResolvedValue(undefined);
  const body={request_id:'preview-stale',expected_workflow_revision:4,expected_decision_revision:1,decision:'deny' as const,scope:{filter:{}}};
  vi.mocked(api.previewBulkDecisions).mockRejectedValue(new ApiError('STALE_REVISION',409));
  const content=(state:WorkflowRead)=><SessionCacheProvider userId="u"><WorkflowContext.Provider value={{workflow:state,refresh}}><PairBulkConfirmation body={body} label="Toàn bộ phiên" busy={false} error="" close={vi.fn()} confirm={vi.fn()}/></WorkflowContext.Provider></SessionCacheProvider>;
  const view=render(content(workflow));
  await waitFor(()=>expect(refresh).toHaveBeenCalledTimes(1));
  expect(screen.getByRole('button',{name:'Xác nhận Deny'})).toBeDisabled();
  view.rerender(content({...workflow,workflow_revision:5}));
  expect(screen.getByText(/Phiên đã thay đổi. Đóng hộp thoại/)).toBeInTheDocument();
  expect(api.bulkDecisions).not.toHaveBeenCalled();
  expect(api.previewBulkDecisions).toHaveBeenCalledTimes(1);
});
it('denies only checked pairs after confirmation and preserves the explicit cannot-link command', async () => {
  show(); await screen.findByText('Person 0');
  fireEvent.click(screen.getByRole('checkbox',{name:'Chọn cặp p0'}));
  fireEvent.click(screen.getByRole('checkbox',{name:'Chọn cặp p1'}));
  fireEvent.click(screen.getByRole('button',{name:'Deny theo phạm vi'}));
  const confirm=await screen.findByRole('button',{name:'Xác nhận Deny'}); await waitFor(()=>expect(confirm).toBeEnabled()); fireEvent.click(confirm);
  await waitFor(()=>expect(api.bulkDecisions).toHaveBeenCalledWith('r',expect.objectContaining({decision:'deny',scope:{pair_ids:['p0','p1']}})));
});
it.each(['group','subgroup'] as const)('previews the complete %s independently of page and status filter',async kind=>{
  show(); await screen.findByText('Person 0');
  fireEvent.change(screen.getByRole('combobox',{name:'Group'}),{target:{value:'A'}});
  if(kind==='subgroup')fireEvent.change(screen.getByRole('combobox',{name:'SubGroup'}),{target:{value:'A1_NAME_PHONE'}});
  fireEvent.change(screen.getByRole('combobox',{name:'Quyết định'}),{target:{value:'PENDING'}});
  fireEvent.change(screen.getByRole('combobox',{name:'Phạm vi thao tác'}),{target:{value:kind}});
  await waitFor(()=>expect(screen.getByRole('button',{name:'Accept theo phạm vi'})).toBeEnabled());
  fireEvent.click(screen.getByRole('button',{name:'Accept theo phạm vi'}));
  await screen.findByText(/10 cặp có thể áp dụng/);
  expect(api.previewBulkDecisions).toHaveBeenCalledWith('r',expect.objectContaining({scope:{filter:kind==='group'?{group:'A'}:{group:'A',subgroup:'A1_NAME_PHONE'}}}),expect.any(AbortSignal));
});
it('returns to a valid page when the last pending pair disappears after a new revision',async()=>{
  let total=11;
  vi.mocked(api.getPairs).mockImplementation(async(_run,q)=>{const offset=Number(q?.get('offset') || 0);return{items:Array.from({length:Math.max(0,Math.min(10,total-offset))},(_,i)=>pair(offset+i)),total,offset,next_cursor:offset+10<total?'p9':null};});
  const view=show(); await screen.findByText('Person 0');
  fireEvent.change(screen.getByRole('combobox',{name:'Quyết định'}),{target:{value:'PENDING'}});
  fireEvent.click(await screen.findByRole('button',{name:'Trang tiếp'})); await screen.findByText('Person 10');
  total=10;
  view.rerender(<SessionCacheProvider userId="u"><WorkflowContext.Provider value={{workflow:{...workflow,workflow_revision:5,decision_revision:2},refresh:vi.fn()}}><DecisionWorkspace/></WorkflowContext.Provider></SessionCacheProvider>);
  await screen.findByText('Person 0');
  expect(screen.getByText(/Trang 1 \/ 1/)).toBeInTheDocument();
});
it('keeps statistics visible while revised counts are being fetched',async()=>{
  const view=show();
  await screen.findByText('Tổng cặp');
  vi.mocked(api.getPairSummary).mockImplementationOnce(()=>new Promise(()=>{}));
  view.rerender(<SessionCacheProvider userId="u"><WorkflowContext.Provider value={{workflow:{...workflow,workflow_revision:5,decision_revision:2},refresh:vi.fn()}}><DecisionWorkspace/></WorkflowContext.Provider></SessionCacheProvider>);
  await waitFor(()=>expect(api.getPairSummary).toHaveBeenCalledTimes(2));
  expect(screen.getByText('Tổng cặp')).toBeVisible();
});
it('hydrates comparison through the individual pair read',async()=>{
 show();await screen.findByText('Person 0');fireEvent.click(screen.getByRole('button',{name:'Đối chiếu cặp p0'}));
 await screen.findByRole('dialog',{name:'Đối chiếu cặp bản ghi'});expect(api.getPair).toHaveBeenCalledWith('r','p0',expect.any(AbortSignal));
});
it('offers partial generation beside the required filter before the pair list',async()=>{
 const partial={...workflow,allowed_actions:['finalize-partial'],blockers:[{code:'PENDING_DECISIONS',count:12,unit:'pairs'}]};
 vi.mocked(api.finalizeDecisions).mockResolvedValue({operation_id:'cl',run_id:'r',operation_type:'CLUSTER',status:'QUEUED',stage:'queued',progress:{},error_code:null,result:{}});
 render(<SessionCacheProvider userId="u"><WorkflowContext.Provider value={{workflow:partial,refresh:vi.fn()}}><DecisionWorkspace/></WorkflowContext.Provider></SessionCacheProvider>);
 await screen.findByText('Person 0');const completion=screen.getByRole('region',{name:'Hoàn tất duyệt'}),list=screen.getByRole('table',{name:'Danh sách cặp bản ghi'});
 expect(completion.compareDocumentPosition(list)&Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
 fireEvent.click(screen.getByRole('button',{name:'Xem cặp cần xử lý'}));expect(screen.getByRole('combobox',{name:'Quyết định'})).toHaveValue('REQUIRED');
 fireEvent.click(screen.getByRole('button',{name:'Tạo nhóm phần đã xử lý'}));await waitFor(()=>expect(api.finalizeDecisions).toHaveBeenCalledWith('r',expect.objectContaining({allow_partial:true})));
});
