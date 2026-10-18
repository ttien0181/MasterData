import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { WorkflowContext } from './SessionWorkspace';
import { SessionCacheProvider } from './SessionCache';
import DecisionWorkspace from './DecisionWorkspace';
import PairBulkConfirmation from './PairBulkConfirmation';
import ClusterPreviewDialog from './ClusterPreviewDialog';
import { ApiError } from '@/lib/api';
import * as api from '@/lib/workflowApi';
import type { WorkflowRead } from '@/lib/workflowContracts';
it('explains model validation separately from human review',async()=>{
 show();await screen.findByText('Person 0');
 fireEvent.click(screen.getByText('Công cụ đánh giá'));
 expect(screen.getByText(/Mô hình: Chưa áp dụng hoặc chưa có mô hình đạt kiểm định/)).toBeInTheDocument();
});
it('shows retained insufficient evidence in the review taxonomy without calling it a human rejection',async()=>{
 vi.mocked(api.getPairSummary).mockResolvedValue({total:3,insufficient:2,needs_review:1,accepted:0,denied:0,kept_separate:0,deferred:0,blocked:0,manual_available:3,auto_available:0,decision_revision:1,groups:[]});
 const weak={...pair(0),baseline:{...pair(0).baseline,decision:'INSUFFICIENT_EVIDENCE',triage_status:'INSUFFICIENT_EVIDENCE',primary_group:'C',subgroup:'C_NO_VALID_IDENTITY_ANCHOR'}};
 vi.mocked(api.getPairs).mockResolvedValue({items:[weak],total:1,next_cursor:null,offset:0});
 show();await screen.findByText('Person 0');
 const row=screen.getByRole('checkbox',{name:'Chọn cặp p0'}).closest('tr')!;
 expect(within(row).getByText('Thiếu thông tin đối chiếu')).toBeInTheDocument();
 expect(within(row).getByText('Giữ riêng tạm thời')).toBeInTheDocument();
 expect(within(row).queryByText('Đã xác nhận khác khách hàng')).toBeNull();
 const params=vi.mocked(api.getPairs).mock.calls[0][1];
 expect(params?.get('visibility')).toBe('review');
 expect(params?.get('taxonomy')).toBe('mvp_v1');
 expect(api.decidePair).not.toHaveBeenCalled();
});
vi.mock('next/navigation', () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock('@/lib/workflowApi', async original => ({ ...await original<typeof api>(),
  getWorkflow:vi.fn(), getOperation:vi.fn(), getPair:vi.fn(), getReviewCluster:vi.fn(), previewClusters:vi.fn(),finalizeDecisions:vi.fn(), getPairs: vi.fn(), getPairSummary: vi.fn(), previewBulkDecisions: vi.fn(), bulkDecisions: vi.fn(), decidePair: vi.fn(),
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
it('asks for a reason before a soft-conflict Đồng ý gộp and retains it through the queue',async()=>{
 vi.mocked(api.getPairs).mockResolvedValue({items:[{...pair(0),baseline:{...pair(0).baseline,primary_group:'D'},eligibility:{manual_allowed:true,auto_allowed:false,blocked_reasons:[],reason_required:true}}],total:1,next_cursor:null});
 show();await screen.findByText('Person 0');fireEvent.click(screen.getByRole('button',{name:'Đồng ý gộp cặp p0'}));
 expect(api.decidePair).not.toHaveBeenCalled();const confirm=await screen.findByRole('button',{name:'Xác nhận Đồng ý gộp'});expect(confirm).toBeDisabled();
 fireEvent.change(screen.getByRole('textbox',{name:'Lý do Đồng ý gộp'}),{target:{value:'Source documents match'}});fireEvent.click(confirm);
 await waitFor(()=>expect(api.decidePair).toHaveBeenCalledWith('r','p0',expect.objectContaining({reason:'Source documents match',decision:'merge'})));
});
it('opens a cluster preview before any finalize write',async()=>{
 const current={...workflow,allowed_actions:['decide-pair','bulk-decisions','finalize-decisions','preview-clusters']};
 vi.mocked(api.previewClusters).mockResolvedValue({operation_id:'preview',run_id:'r',operation_type:'CLUSTER_PREVIEW',status:'COMPLETED',stage:'complete',progress:{},error_code:null,result:{preview_token:'token',publication:{publication_status:'COMPLETE',covered_records:2,total_records:2,multi_record_groups:1,singletons:0,unresolved_records:0,clusters:1},groups:[{member_record_ids:['a','b'],singleton:false,status:'DRAFT'}],conflicts:[],conflict_count:0,pending_regions:[],source_decision_revision:1,source_assessment_id:null,allow_partial:false}});
 render(<SessionCacheProvider userId="preview-integration"><WorkflowContext.Provider value={{workflow:current,refresh:vi.fn().mockResolvedValue(undefined)}}><ClusterPreviewDialog partial={false} pending={0} attention={0} close={vi.fn()} created={vi.fn()}/></WorkflowContext.Provider></SessionCacheProvider>);
 await screen.findByRole('table',{name:'Nhóm dự kiến'});expect(api.finalizeDecisions).not.toHaveBeenCalled();
 fireEvent.click(screen.getByRole('button',{name:'Xác nhận tạo nhóm'}));await waitFor(()=>expect(api.finalizeDecisions).toHaveBeenCalledWith('r',expect.objectContaining({preview_operation_id:'preview',preview_token:'token'})));
});
it('uses a full diagnostic standalone preview to expose bridge approval while server pairs remain pending',async()=>{
 const current={...workflow,allowed_actions:['preview-clusters','finalize-partial','approve-group'],blockers:[{code:'PENDING_DECISIONS',count:1,unit:'pairs'}]};
 vi.mocked(api.previewClusters).mockImplementation(async(_run,body)=>({operation_id:'diagnostic',run_id:'r',operation_type:'CLUSTER_PREVIEW',status:'COMPLETED',stage:'complete',progress:{},error_code:null,result:{preview_token:'diagnostic-token',publication:{publication_status:'PARTIAL',covered_records:0,total_records:3,multi_record_groups:0,singletons:0,unresolved_records:3,clusters:0},groups:[],conflicts:body.allow_partial?[]:[{pair_id:'ac',reason:'CLUSTER_BRIDGE_MISSING_EVIDENCE',record_ids:['a','b','c']}],conflict_count:body.allow_partial?0:1,pending_regions:[{record_ids:['a','b','c'],record_count:3,pending_pair_ids:['ac'],pending_pair_count:1}],source_decision_revision:1,source_assessment_id:null,allow_partial:body.allow_partial}}));
 render(<SessionCacheProvider userId="diagnostic-pending"><WorkflowContext.Provider value={{workflow:current,refresh:vi.fn().mockResolvedValue(undefined)}}><ClusterPreviewDialog partial={false} pending={0} attention={0} close={vi.fn()} created={vi.fn()}/></WorkflowContext.Provider></SessionCacheProvider>);
 await waitFor(()=>expect(api.previewClusters).toHaveBeenCalledWith('r',expect.objectContaining({allow_partial:false})));
 expect(await screen.findByRole('button',{name:'Duyệt ngoại lệ nhóm'})).toBeEnabled();
 expect(screen.getByText('Hệ thống còn 1 cặp chưa duyệt hoặc đang hoãn.')).toBeInTheDocument();
 expect(screen.getByRole('button',{name:'Xác nhận tạo nhóm'})).toBeDisabled();expect(api.finalizeDecisions).not.toHaveBeenCalled();
});
it('keeps other pair actions enabled and queues a second click without parallel writes',async()=>{
  let complete!: (value:ReturnType<typeof pair>)=>void;
  vi.mocked(api.decidePair).mockImplementationOnce(()=>new Promise(resolve=>{complete=resolve;}));
  show();await screen.findByText('Person 0');
  fireEvent.click(screen.getByRole('button',{name:'Đồng ý gộp cặp p0'}));
  await waitFor(()=>expect(api.decidePair).toHaveBeenCalledTimes(1));
  expect(screen.getByRole('button',{name:'Đồng ý gộp cặp p0'})).toBeDisabled();
  expect(screen.getByRole('button',{name:'Đồng ý gộp cặp p1'})).toBeEnabled();
  fireEvent.click(screen.getByRole('button',{name:'Đồng ý gộp cặp p1'}));
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
  fireEvent.click(screen.getByRole('checkbox',{name:'Chọn cặp p0'}));
  await waitFor(()=>expect(screen.getByRole('button',{name:'Đồng ý gộp theo phạm vi'})).toBeEnabled());
  fireEvent.click(screen.getByRole('button',{name:'Đồng ý gộp theo phạm vi'}));
  const confirm=await screen.findByRole('button',{name:'Xác nhận Đồng ý gộp'});await waitFor(()=>expect(confirm).toBeEnabled());fireEvent.click(confirm);
  await screen.findByText(/Hàng chờ duyệt/);
  expect(api.bulkDecisions).not.toHaveBeenCalled();
});
it('continues queued decisions when another workspace tab is shown',async()=>{
  let complete!: (value:ReturnType<typeof pair>)=>void;
  vi.mocked(api.decidePair).mockImplementationOnce(()=>new Promise(resolve=>{complete=resolve;}));
  const host=(visible:boolean)=><SessionCacheProvider userId="u"><WorkflowContext.Provider value={{workflow,refresh:vi.fn().mockResolvedValue(undefined)}}>{visible?<DecisionWorkspace/>:<p>Tab dữ liệu</p>}</WorkflowContext.Provider></SessionCacheProvider>;
  const view=render(host(true));await screen.findByText('Person 0');
  fireEvent.click(screen.getByRole('button',{name:'Đồng ý gộp cặp p0'}));await waitFor(()=>expect(api.decidePair).toHaveBeenCalledTimes(1));
  fireEvent.click(screen.getByRole('button',{name:'Đồng ý gộp cặp p1'}));view.rerender(host(false));
  complete({...pair(0),revision:1,decision:'ACCEPTED'});
  await waitFor(()=>expect(api.decidePair).toHaveBeenCalledTimes(2));
  view.rerender(host(true));expect(await screen.findByRole('region',{name:'Hàng chờ duyệt'})).toBeVisible();
});
it('clearly separates assessment controls and synthetic scope from Đồng ý gộp/Không gộp', async()=>{
  show(); await screen.findByText('Person 0');
  fireEvent.click(screen.getByText('Công cụ đánh giá'));
  expect(screen.getByRole('region',{name:'Đánh giá thông tin đối chiếu'})).toBeInTheDocument();
  expect(screen.getByRole('button',{name:'Đánh giá lại thông tin đối chiếu'})).toBeDisabled();
  expect(screen.getByRole('button',{name:'Nhờ AI hỗ trợ đối chiếu'})).toBeDisabled();
  expect(screen.getByRole('checkbox',{name:/Phiên dùng dữ liệu kiểm thử tổng hợp/})).not.toBeChecked();
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
  fireEvent.click(screen.getByRole('button',{name:'Chọn tất cả (12)'}));
  fireEvent.click(screen.getByRole('button',{name:'Đồng ý gộp theo phạm vi'}));
  await screen.findByText(/10 cặp có thể Đồng ý gộp/);
  expect(screen.getByText(/2 bị chặn/)).toBeInTheDocument();
  expect(api.previewBulkDecisions).toHaveBeenCalledWith('r',expect.objectContaining({decision:'merge',scope:{filter:{visibility:'review',taxonomy:'mvp_v1'},excluded_pair_ids:[]},auto_only:false}),expect.any(AbortSignal));
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
  expect(screen.getByRole('button',{name:'Xác nhận Không gộp'})).toBeDisabled();
  view.rerender(content({...workflow,workflow_revision:5}));
  expect(screen.getByText(/Phiên đã thay đổi. Đóng hộp thoại/)).toBeInTheDocument();
  expect(api.bulkDecisions).not.toHaveBeenCalled();
  expect(api.previewBulkDecisions).toHaveBeenCalledTimes(1);
});
it('denies only checked pairs after confirmation and preserves the explicit cannot-link command', async () => {
  show(); await screen.findByText('Person 0');
  fireEvent.click(screen.getByRole('checkbox',{name:'Chọn cặp p0'}));
  fireEvent.click(screen.getByRole('checkbox',{name:'Chọn cặp p1'}));
  fireEvent.click(screen.getByRole('button',{name:'Không gộp theo phạm vi'}));
  const confirm=await screen.findByRole('button',{name:'Xác nhận Không gộp'}); await waitFor(()=>expect(confirm).toBeEnabled()); fireEvent.click(confirm);
  await waitFor(()=>expect(api.bulkDecisions).toHaveBeenCalledWith('r',expect.objectContaining({decision:'deny',scope:{pair_ids:['p0','p1']}})));
});
it.each([['A_CONTACT','Trùng thông tin liên hệ'],['B_SUPPORT','Có căn cứ cần xác minh thêm']])('previews the complete MVP group %s independently of pagination',async (group,label)=>{
  vi.mocked(api.getPairSummary).mockResolvedValue({total:12,needs_review:12,accepted:0,denied:0,kept_separate:0,deferred:0,blocked:0,manual_available:12,auto_available:0,decision_revision:1,groups:[{key:group,total:12,subgroups:[]}]});
  show(); await screen.findByText('Person 0');
  fireEvent.click(screen.getByRole('button',{name:new RegExp(label)}));
  await screen.findByText('Person 0');
  fireEvent.click(await screen.findByRole('button',{name:'Trang tiếp'}));
  await screen.findByText('Person 10');
  fireEvent.click(screen.getByRole('button',{name:`Đồng ý gộp ${label}`}));
  await screen.findByText(/10 cặp có thể Đồng ý gộp/);
  expect(api.previewBulkDecisions).toHaveBeenCalledWith('r',expect.objectContaining({scope:{filter:{group,visibility:'review',taxonomy:'mvp_v1'}}}),expect.any(AbortSignal));
});
it('returns to a valid page when the last pending pair disappears after a new revision',async()=>{
  let total=11;
  vi.mocked(api.getPairs).mockImplementation(async(_run,q)=>{const offset=Number(q?.get('offset') || 0);return{items:Array.from({length:Math.max(0,Math.min(10,total-offset))},(_,i)=>pair(offset+i)),total,offset,next_cursor:offset+10<total?'p9':null};});
  const view=show(); await screen.findByText('Person 0');
  fireEvent.click(await screen.findByRole('button',{name:'Trang tiếp'})); await screen.findByText('Person 10');
  total=10;
  view.rerender(<SessionCacheProvider userId="u"><WorkflowContext.Provider value={{workflow:{...workflow,workflow_revision:5,decision_revision:2},refresh:vi.fn()}}><DecisionWorkspace/></WorkflowContext.Provider></SessionCacheProvider>);
  await screen.findByText('Person 0');
  expect(screen.getByText(/Trang 1 \/ 1/)).toBeInTheDocument();
});
it('keeps statistics visible while revised counts are being fetched',async()=>{
  const view=show();
  await screen.findByText('Tổng cặp hồ sơ');
  vi.mocked(api.getPairSummary).mockImplementationOnce(()=>new Promise(()=>{}));
  view.rerender(<SessionCacheProvider userId="u"><WorkflowContext.Provider value={{workflow:{...workflow,workflow_revision:5,decision_revision:2},refresh:vi.fn()}}><DecisionWorkspace/></WorkflowContext.Provider></SessionCacheProvider>);
  await waitFor(()=>expect(api.getPairSummary).toHaveBeenCalledTimes(2));
  expect(screen.getByText('Tổng cặp hồ sơ')).toBeVisible();
});
it('hydrates comparison through the individual pair read',async()=>{
 show();await screen.findByText('Person 0');fireEvent.click(screen.getByRole('button',{name:'Đối chiếu cặp p0'}));
 await screen.findByRole('dialog',{name:'Đối chiếu cặp hồ sơ'});expect(api.getPair).toHaveBeenCalledWith('r','p0',expect.any(AbortSignal));
});
it('links review to the group step without an explicit finalize write',async()=>{
 const partial={...workflow,allowed_actions:['finalize-partial'],blockers:[{code:'PENDING_DECISIONS',count:12,unit:'pairs'}]};
 vi.mocked(api.finalizeDecisions).mockResolvedValue({operation_id:'cl',run_id:'r',operation_type:'CLUSTER',status:'QUEUED',stage:'queued',progress:{},error_code:null,result:{}});
 render(<SessionCacheProvider userId="u"><WorkflowContext.Provider value={{workflow:partial,refresh:vi.fn()}}><DecisionWorkspace/></WorkflowContext.Provider></SessionCacheProvider>);
 await screen.findByText('Person 0');const completion=screen.getByRole('region',{name:'Chuyển sang tạo nhóm'});
 expect(within(completion).getByRole('link',{name:'Sang Bước 3'})).toHaveAttribute('href','/app/sessions/r/clusters');
 expect(screen.queryByRole('button',{name:'Tạo các nhóm đủ điều kiện'})).toBeNull();
 expect(api.previewClusters).not.toHaveBeenCalled();expect(api.finalizeDecisions).not.toHaveBeenCalled();
});

it('opens a cluster and queues only its exact pending pair IDs',async()=>{
 vi.mocked(api.getReviewCluster).mockResolvedValue({seed_pair_id:'p0',complete:true,batch_allowed:true,blocked_reasons:[],pending_pair_ids:['p0','p1'],decision_revision:1,assessment_id:null,records:[pair(0).left_record,pair(0).right_record],pairs:[pair(0),pair(1)]});
 vi.mocked(api.getPair).mockResolvedValue({...pair(0),baseline:{...pair(0).baseline,primary_group:'D'}});
 show();await screen.findByText('Person 0');
 fireEvent.click(screen.getByRole('button',{name:'Đối chiếu cặp p0'}));
 fireEvent.click(await screen.findByRole('button',{name:'Đối chiếu nhóm'}));
 const clusterAccept=await screen.findByRole('button',{name:'Đồng ý gộp 2 cặp trong cụm'});
 await waitFor(()=>expect(clusterAccept).toBeEnabled());fireEvent.click(clusterAccept);
 const confirm=await screen.findByRole('button',{name:'Xác nhận Đồng ý gộp'});await waitFor(()=>expect(confirm).toBeEnabled());
 expect(api.previewBulkDecisions).toHaveBeenLastCalledWith('r',expect.objectContaining({scope:{pair_ids:['p0','p1']},auto_only:false}),expect.any(AbortSignal));
 fireEvent.click(confirm);await waitFor(()=>expect(api.bulkDecisions).toHaveBeenCalledWith('r',expect.objectContaining({scope:{pair_ids:['p0','p1']},decision:'merge',auto_only:false})));
});
