import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { ClusterGeneration, GoldenRead, WorkflowRead } from '@/lib/workflowContracts';
import SessionWorkspace, { WorkflowContext } from './SessionWorkspace';
import { ApiError } from '@/lib/api';
import DecisionWorkspace from './DecisionWorkspace';
import ClusterWorkspace from './ClusterWorkspace';
import GoldenFieldEditor from './GoldenFieldEditor';
import GoldenRecords from './GoldenRecords';
import SessionHub from './SessionHub';
import * as api from '@/lib/workflowApi';
import { SessionCacheProvider } from './SessionCache';
const navigation = vi.hoisted(() => ({ pathname: '/app' }));
vi.mock('next/navigation', () => ({ useRouter: () => ({ push: vi.fn(), replace: vi.fn() }), usePathname: () => navigation.pathname }));
vi.mock('./AppSession', () => ({ useAppUser: () => ({ id: 'u', email: 'u@example.test', capabilities: ['read','create_run'] }) }));
vi.mock('@/lib/workflowApi', async importOriginal => ({ ...await importOriginal<typeof api>(), getWorkflow: vi.fn(), getPairSummary: vi.fn().mockResolvedValue({total:2,needs_review:2,accepted:0,denied:0,kept_separate:0,deferred:0,blocked:0,manual_available:2,auto_available:0,groups:[]}), getPairs: vi.fn(), getClusters: vi.fn(), getCluster:vi.fn(),getClusterGolden:vi.fn(),getGoldenResolution:vi.fn().mockResolvedValue({resolution:null,undo_operation_id:null,stale:false}),getGoldenRecords: vi.fn(), decidePair: vi.fn(), previewClusters:vi.fn(),finalizeDecisions: vi.fn(), saveGolden: vi.fn() }));
vi.mock('@/lib/api', async importOriginal => ({ ...await importOriginal<typeof import('@/lib/api')>(), getResolutionRuns: vi.fn().mockResolvedValue({ items: [], total: 0 }), getUploadSessions: vi.fn().mockResolvedValue({ items: [] }) }));
afterEach(() => { cleanup(); vi.useRealTimers(); navigation.pathname = '/app'; });
const w: WorkflowRead = { run_id: 'r', workflow_version: 2, workflow_state: 'DECIDE_REVIEW', workflow_revision: 3, decision_revision: 2, cluster_revision: 0, golden_revision: 0, counts: { records: { value: 3, unit: 'records' } }, allowed_actions: ['decide-pair', 'bulk-decisions', 'finalize-decisions'], blockers: [], active_operation: null, retryable_operation: null };

it('shows queued work without inventing progress when the total is unknown', async () => {
  vi.mocked(api.getWorkflow).mockResolvedValue({ ...w, workflow_state: 'DATA_PREPARING', active_operation: {
    operation_id: 'queued-prepare', run_id: 'r', operation_type: 'PREPARE', status: 'QUEUED',
    stage: 'queued', progress: {}, error_code: null, result: {},
  } });
  render(<SessionCacheProvider userId="test"><SessionWorkspace runId="r"><span>Workspace</span></SessionWorkspace></SessionCacheProvider>);
  await screen.findByText('0% · Đang chờ xử lý');
  expect(screen.getByRole('status')).not.toHaveTextContent('0 / 0');
  expect(screen.getByRole('progressbar', { name: 'Đang tải dữ liệu' })).toHaveAttribute('value', '0');
});

it('shows incomplete embedding coverage and automation disabled in degraded runs', async () => {
  vi.mocked(api.getWorkflow).mockResolvedValue({...w, matching_agents:{status:'DEGRADED',warning:'Coverage incomplete',coverage:{expected_pairs:2,embedding_pairs:0},artifact_id:'evidence',automation_allowed:false}});
  render(<SessionCacheProvider userId="test"><SessionWorkspace runId="r"><span>Workspace</span></SessionWorkspace></SessionCacheProvider>);
  expect(await screen.findByRole('alert')).toHaveTextContent('Đối chiếu tên và địa chỉ chưa đầy đủ');
  expect(screen.getByRole('alert')).toHaveTextContent('tự động gộp đã bị tắt');
});
const golden: GoldenRead = { cluster_id: 'c', cluster_revision: 1, golden_revision: 2, revision: 0, status: 'DRAFT', fields: { name: { value: 'Server', provenance: 'SURVIVORSHIP' }, phone: { value: '090', values: [{ value: '090' }, { value: '091' }] } }, alternatives: { name: [{ source_record_id: 'member', source_system: 'CRM', value: 'Source', auto_rule_available: true }] }, selections: {} };
it('explains skipped name and address evidence in the cluster processing details', async () => {
  navigation.pathname = '/app/sessions/r/clusters';
  vi.mocked(api.getWorkflow).mockResolvedValue({...w, matching_agents:{status:'SKIPPED',warning:'Demo',coverage:{expected_pairs:2,embedding_pairs:0},artifact_id:'evidence',automation_allowed:false}});
  render(<SessionCacheProvider userId="test"><SessionWorkspace runId="r"><span>Workspace</span></SessionWorkspace></SessionCacheProvider>);
  fireEvent.click(await screen.findByText('Thông tin xử lý'));
  expect(screen.getByText(/Chưa có kết quả so sánh tên và địa chỉ/)).toBeVisible();
  expect(screen.getByText(/bạn cần kiểm tra các cặp trước khi gộp/)).toBeVisible();
});
function workspace(ui: React.ReactNode, workflow = w) { return render(<SessionCacheProvider userId="test"><WorkflowContext.Provider value={{ workflow, refresh: vi.fn().mockResolvedValue(undefined) }}>{ui}</WorkflowContext.Provider></SessionCacheProvider>); }

it('shows accurate group ranges, returns to the previous cursor and preserves page selection',async()=>{
 const group=(id:string)=>({cluster_id:id,display_name:id,member_record_ids:[id],member_count:1,singleton:true,status:'SINGLETON',evidence:{items:[],total:0,next_cursor:null}});
 vi.mocked(api.getClusters).mockImplementation(async(_run,query)=>query?.get('cursor')==='after-c1'
  ?{items:[group('c2')],total:2,next_cursor:null}
  :{items:[group('c1')],total:2,next_cursor:'after-c1'});
 workspace(<ClusterWorkspace/>,{...w,workflow_state:'GOLDEN_REVIEW',cluster_revision:1});
 await screen.findByRole('table',{name:'Danh sách nhóm hồ sơ'});
 expect(screen.getByText('Hiển thị 1–1 / 2 nhóm')).toBeVisible();
 expect(screen.getByRole('button',{name:'Trang trước'})).toBeDisabled();
 fireEvent.click(screen.getByRole('checkbox',{name:'Chọn trang hiện tại'}));
 fireEvent.click(screen.getByRole('button',{name:'Trang tiếp'}));
 await screen.findByRole('checkbox',{name:'Chọn nhóm c2'});
 expect(screen.getByText('Hiển thị 2–2 / 2 nhóm')).toBeVisible();
 expect(screen.getByText('Đã chọn 1 nhóm')).toBeVisible();
 expect(screen.getByRole('button',{name:'Trang tiếp'})).toBeDisabled();
 fireEvent.click(screen.getByRole('button',{name:'Trang trước'}));
 expect(await screen.findByRole('checkbox',{name:'Chọn nhóm c1'})).toBeChecked();
 expect(screen.getByText('Hiển thị 1–1 / 2 nhóm')).toBeVisible();
 fireEvent.click(screen.getByRole('button',{name:'Chọn tất cả 2 nhóm'}));
 expect(screen.getByText('Đã chọn 2 nhóm')).toBeVisible();
 fireEvent.click(screen.getByRole('checkbox',{name:'Chọn nhóm c1'}));
 expect(screen.getByText('Đã chọn 1 nhóm')).toBeVisible();
});

it('can return to a cached group page after the next page fails without selecting stale rows',async()=>{
 const group={cluster_id:'c',member_record_ids:['a'],member_count:1,singleton:true,status:'SINGLETON',evidence:{items:[],total:0,next_cursor:null}};
 vi.mocked(api.getClusters).mockImplementation(async(_run,query)=>{
  if(query?.get('cursor'))throw new Error('Page unavailable');
  return {items:[group],total:2,next_cursor:'opaque-next'};
 });
 workspace(<ClusterWorkspace/>,{...w,workflow_state:'GOLDEN_REVIEW',cluster_revision:1});
 await screen.findByRole('table',{name:'Danh sách nhóm hồ sơ'});
 fireEvent.click(screen.getByRole('button',{name:'Trang tiếp'}));
 await screen.findByText('Page unavailable');
 expect(screen.getByRole('checkbox',{name:'Chọn nhóm c'})).toBeDisabled();
 expect(screen.getByRole('button',{name:'Trang trước'})).toBeEnabled();
 fireEvent.click(screen.getByRole('button',{name:'Trang trước'}));
 await waitFor(()=>expect(screen.getByRole('checkbox',{name:'Chọn nhóm c'})).toBeEnabled());
 expect(screen.getByText('Hiển thị 1–1 / 2 nhóm')).toBeVisible();
});

it('opens published groups by name and compares member fields without raw JSON',async()=>{
 const group={cluster_id:'c',member_record_ids:['a','b'],member_count:2,singleton:false,status:'REVIEW_APPROVED',display_name:'Nguyễn Văn An',evidence:{items:[],total:0,next_cursor:null}};
 const records=['a','b'].map((id,index)=>({record_id:id,source_system:index?'ERP':'CRM',raw_fields:{name:'Nguyễn Văn An',dob:index?'1990-05-16':'1990-05-15'},normalized_fields:{},extra_fields:{}}));
 vi.mocked(api.getClusters).mockResolvedValue({items:[group],total:1,next_cursor:null});
 vi.mocked(api.getCluster).mockResolvedValue({...group,members:{items:records,total:2,next_cursor:null},pair_evidence:{items:[],total:0,next_cursor:null},llm_stats:{},workflow_context:{}});
 vi.mocked(api.getClusterGolden).mockResolvedValue(golden);
 workspace(<ClusterWorkspace initialClusterId="c"/>,{...w,workflow_state:'GOLDEN_REVIEW',cluster_revision:1});
 expect(await screen.findByRole('table',{name:'Đối chiếu thành viên nhóm'})).toBeVisible();
 expect(screen.getByRole('button',{name:/Đối chiếu nhóm Nguyễn Văn An/})).toBeVisible();
 expect(screen.getByText('1990-05-15')).toBeVisible();expect(screen.getByText('1990-05-16')).toBeVisible();
});

it('opens group details in a dialog and preserves the hồ sơ đại diện draft when closing and reopening',async()=>{
 const group={cluster_id:'c',display_name:'An',member_record_ids:['a'],member_count:1,singleton:true,status:'SINGLETON',evidence:{items:[],total:0,next_cursor:null}};
 vi.mocked(api.getClusters).mockResolvedValue({items:[group],total:1,next_cursor:null});
 vi.mocked(api.getCluster).mockResolvedValue({...group,members:{items:[],total:0,next_cursor:null},pair_evidence:{items:[],total:0,next_cursor:null},llm_stats:{},workflow_context:{}});
 vi.mocked(api.getClusterGolden).mockResolvedValue(golden);
 workspace(<ClusterWorkspace/>,{...w,workflow_state:'GOLDEN_REVIEW',cluster_revision:1,allowed_actions:['save-golden','confirm-golden']});
 fireEvent.click(await screen.findByRole('checkbox',{name:'Chọn trang hiện tại'}));
 fireEvent.click(await screen.findByRole('button',{name:'Đối chiếu nhóm An'}));
 expect(await screen.findByRole('dialog',{name:'Chi tiết nhóm An'})).toBeVisible();
 fireEvent.click(screen.getByLabelText('Nhập giá trị khác Họ tên'));
 fireEvent.change(await screen.findByRole('textbox',{name:'Nhập trực tiếp name'}),{target:{value:'Draft in popup'}});
 const confirm=vi.spyOn(window,'confirm').mockReturnValue(false);
 try{
  fireEvent.click(screen.getByRole('button',{name:'Đóng chi tiết nhóm'}));
  expect(screen.getByRole('dialog')).toBeVisible();
  confirm.mockReturnValue(true);
  fireEvent.click(screen.getByRole('button',{name:'Đóng chi tiết nhóm'}));
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(window.location.search).not.toContain('cluster=');
  expect(screen.getByRole('button',{name:'Xác nhận hồ sơ đại diện đã chọn'})).toBeDisabled();
  fireEvent.click(screen.getByRole('button',{name:'Đối chiếu nhóm An'}));
  expect(await screen.findByRole('textbox',{name:'Nhập trực tiếp name'})).toHaveValue('Draft in popup');
  fireEvent.click(screen.getByRole('button',{name:'Bỏ bản nháp'}));
  fireEvent.click(screen.getByRole('button',{name:'Đóng chi tiết nhóm'}));
  expect(screen.getByRole('button',{name:'Xác nhận hồ sơ đại diện đã chọn'})).toBeEnabled();
 }finally{confirm.mockRestore();}
});

it('keeps separate groups with identical names keyed by their identities',async()=>{
 const error=vi.spyOn(console,'error').mockImplementation(()=>{});
 try{
  const groups=['c1','c2'].map(id=>({cluster_id:id,member_record_ids:[id],member_count:1,singleton:true,
   status:'DRAFT',display_name:'Same customer name',evidence:{items:[],total:0,next_cursor:null}}));
  vi.mocked(api.getClusters).mockResolvedValue({items:groups,total:2,next_cursor:null});
  workspace(<ClusterWorkspace/>,{...w,workflow_state:'GOLDEN_REVIEW',cluster_revision:1});
  expect(await screen.findAllByRole('button',{name:/Đối chiếu nhóm Same customer name/})).toHaveLength(2);
  expect(error.mock.calls.flat().join(' ')).not.toContain('same key');
 }finally{error.mockRestore();}
});

it('requests optional enrichment only after published groups and keeps publication separate',async()=>{
 vi.mocked(api.getClusters).mockResolvedValue({items:[],total:0,next_cursor:null});
 const request=vi.spyOn(api,'requestGroupAnnotations').mockResolvedValue({operation_id:'annotations',run_id:'r',
  operation_type:'GROUP_ANNOTATIONS',status:'QUEUED',stage:'queued',progress:{},error_code:null,result:{}});
 try{
  workspace(<ClusterWorkspace/>,{...w,workflow_state:'GOLDEN_REVIEW',cluster_revision:2,
   allowed_actions:['annotate-groups']});
  fireEvent.click(screen.getByText('Công cụ bổ sung'));
  fireEvent.click(await screen.findByRole('button',{name:'Bổ sung đối chiếu địa chỉ'}));
  await waitFor(()=>expect(request).toHaveBeenCalledWith('r',expect.objectContaining({expected_cluster_revision:2})));
  expect(api.finalizeDecisions).not.toHaveBeenCalled();
 }finally{request.mockRestore();}
});
beforeEach(() => { vi.clearAllMocks(); sessionStorage.clear(); vi.mocked(api.getPairs).mockResolvedValue({ items: [{ pair_id: 'p', baseline: { id: 'p', left_id: 'a', right_id: 'b', score: 10, decision: 'INSUFFICIENT_EVIDENCE', primary_group: 'D', field_evidence: [] }, decision: 'UNDECIDED', revision: 1, score_unit: 'p119_rule_v1', eligibility: { manual_allowed: false, auto_allowed: false, blocked_reasons: ['NO_EVIDENCE'] } }], total: 1, next_cursor: null }); });
it('hub starts at overview and performs no workflow computation', async () => { render(<SessionHub />); expect(await screen.findByRole('heading', { name: 'Tổng quan' })).toBeInTheDocument(); expect(api.finalizeDecisions).not.toHaveBeenCalled(); });
it('shows insufficient evidence and server permissions while step 3 navigation performs no generation write', async () => {
  vi.mocked(api.previewClusters).mockResolvedValue({operation_id:'preview',run_id:'r',operation_type:'CLUSTER_PREVIEW',status:'COMPLETED',stage:'complete',progress:{},error_code:null,result:{preview_token:'token',publication:{publication_status:'COMPLETE',covered_records:3,total_records:3,multi_record_groups:0,singletons:3,unresolved_records:0,clusters:3},groups:[{member_record_ids:['a'],singleton:true,status:'DRAFT'}],conflicts:[],conflict_count:0,pending_regions:[],source_decision_revision:2,source_assessment_id:null,allow_partial:false}});
  workspace(<DecisionWorkspace />);
  expect(await screen.findByText('Giữ riêng tạm thời')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Đồng ý gộp cặp p' })).toBeDisabled();
  expect(screen.getByRole('button', { name: 'Không gộp cặp p' })).toHaveTextContent(/^Không gộp$/);
  expect(screen.queryByRole('button', { name: 'Hoãn cặp p' })).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Không gộp cặp p' })).toBeEnabled();
  expect(api.finalizeDecisions).not.toHaveBeenCalled();
  expect(screen.getByRole('link',{name:'Sang Bước 3'})).toHaveAttribute('href','/app/sessions/r/clusters');
  expect(api.finalizeDecisions).not.toHaveBeenCalled();
});
it('pending/defer does not block navigation, queued cluster never reads unpublished membership', () => {
  const view = workspace(<DecisionWorkspace />, { ...w, allowed_actions: ['decide-pair'], blockers: [{ code: 'PENDING_DECISIONS', count: 1 }] });
  expect(screen.getByRole('link', { name: 'Sang Bước 3' })).toHaveAttribute('href','/app/sessions/r/clusters'); view.unmount();
  workspace(<ClusterWorkspace />, { ...w, workflow_state: 'CLUSTER_RUNNING' });
  expect(api.getClusters).not.toHaveBeenCalled();
});
it('edits server golden while preserving multivalue and explicit source choice', async () => {
  const save = vi.fn().mockResolvedValue(undefined);
  render(<GoldenFieldEditor golden={golden} editable busy={false} onSave={save} />);
  expect(screen.getByText('091')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Chọn name từ CRM: Source' }));
  fireEvent.click(screen.getByRole('button', { name: 'Lưu lựa chọn hồ sơ đại diện' }));
  await waitFor(() => expect(save).toHaveBeenCalledWith({ name: { selection_mode: 'SOURCE_CHOICE', source_record_id: 'member' } }, 1, 2));
});
it('unconfirmed drafts cannot finalize or export', async () => {
  vi.mocked(api.getGoldenRecords).mockResolvedValue({ items: [golden], total: 1, next_cursor: null });
  workspace(<GoldenRecords />, { ...w, workflow_state: 'GOLDEN_REVIEW', cluster_revision: 1, allowed_actions: ['save-golden'], blockers: [{ code: 'UNCONFIRMED_GOLDEN', count: 1 }] });
  expect(await screen.findByText('DRAFT')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Chốt phiên' })).toBeDisabled();
  expect(screen.queryByRole('button', { name: 'Xuất CSV' })).not.toBeInTheDocument();
});
const generation:ClusterGeneration={revision:1,source_decision_revision:1,stale:true,read_only:true,publication_status:'PARTIAL',clusters:1,multi_record_groups:1,singletons:0,covered_records:2,unresolved_records:1,total_records:3};
it('keeps stale golden snapshots visible during regeneration and blocks finalization',async()=>{
 vi.mocked(api.getGoldenRecords).mockResolvedValue({items:[{...golden,read_only:true,generation}],total:1,next_cursor:null,generation});
 workspace(<GoldenRecords/>,{...w,workflow_state:'CLUSTER_RUNNING',cluster_revision:1,cluster_result:generation,allowed_actions:['finalize-golden','resolve-golden']});
 await screen.findByText('DRAFT');expect(screen.getByRole('status')).toHaveTextContent('chỉ đọc');expect(screen.getByRole('button',{name:'Chốt phiên'})).toBeDisabled();expect(screen.getByRole('button',{name:'Tự xử lý & tạo hồ sơ đại diện'})).toBeDisabled();
});
it('prevents export of partial publication even if stale permissions are displayed',async()=>{
 vi.mocked(api.getGoldenRecords).mockResolvedValue({items:[golden],total:1,next_cursor:null,generation:{...generation,stale:false}});
 workspace(<GoldenRecords/>,{...w,workflow_state:'FINALIZED',cluster_revision:1,cluster_result:{...generation,stale:false},allowed_actions:['export']});
 await screen.findByText('DRAFT');expect(screen.getByRole('button',{name:'Chuẩn bị xuất CSV'})).toBeDisabled();
});
it('honors stale page metadata even when the workflow cache still reports a current generation',async()=>{
 vi.mocked(api.getGoldenRecords).mockResolvedValue({items:[{...golden,read_only:true,generation}],total:1,next_cursor:null,generation});
 workspace(<GoldenRecords/>,{...w,workflow_state:'FINALIZED',cluster_revision:1,cluster_result:{...generation,stale:false,publication_status:'COMPLETE',covered_records:3,unresolved_records:0},allowed_actions:['export']});
 await screen.findByText('DRAFT');expect(screen.getByRole('button',{name:'Chuẩn bị xuất CSV'})).toBeDisabled();
});
it('preserves unsaved hồ sơ đại diện intent across hệ thống revision and blocks silent rebase', () => {
  const save = vi.fn();
  const view = render(<GoldenFieldEditor golden={golden} editable busy={false} onSave={save} />);
  fireEvent.change(screen.getByRole('textbox', { name: 'Nhập trực tiếp name' }), { target: { value: 'My draft' } });
  view.rerender(<GoldenFieldEditor golden={{ ...golden, golden_revision: 3, fields: { name: { value: 'Other writer' } } }} editable busy={false} onSave={save} />);
  expect(screen.getByRole('textbox', { name: 'Nhập trực tiếp name' })).toHaveValue('My draft');
  expect(screen.getByRole('button', { name: 'Lưu lựa chọn hồ sơ đại diện' })).toBeDisabled();
  expect(save).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Bỏ bản nháp' }));
  expect(screen.getByRole('textbox', { name: 'Nhập trực tiếp name' })).toHaveValue('');
});
it('zero candidates retain step 3 navigation without guessing a generated result', async () => {
  vi.mocked(api.getPairSummary).mockResolvedValueOnce({decision_revision:2,total:0,needs_review:0,accepted:0,denied:0,kept_separate:0,deferred:0,blocked:0,manual_available:0,auto_available:0,groups:[]});
  vi.mocked(api.getPairs).mockResolvedValue({ items: [], total: 0, next_cursor: null });
  workspace(<DecisionWorkspace />);
  expect(await screen.findByText('Không có cặp khớp bộ lọc.')).toBeInTheDocument();
  expect(screen.getByRole('link', { name: 'Sang Bước 3' })).toHaveAttribute('href','/app/sessions/r/clusters');
  expect(api.finalizeDecisions).not.toHaveBeenCalled();
});
it('extra fields expose source choices and no manual/auto ranking invented in FE', () => {
  render(<GoldenFieldEditor golden={{ ...golden, alternatives: { custom: [{ source_record_id: 'member', source_system: 'CRM', value: 'Extra', auto_rule_available: false }] } }} editable busy={false} onSave={vi.fn()} />);
  expect(screen.getByText(/Với thông tin bổ sung, hãy chọn giá trị từ hồ sơ nguồn/)).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Chọn custom từ CRM: Extra' })).toBeEnabled();
  expect(screen.queryByRole('textbox', { name: 'Nhập trực tiếp custom' })).not.toBeInTheDocument();
});
it('manual eligibility comes from hệ thống even for Nhóm D, stale request preserves intent', async () => {
  const original = await api.getPairs('r');
  vi.mocked(api.getPairs).mockResolvedValue({ ...original, items: original.items.map(p => ({ ...p, eligibility: { ...p.eligibility, manual_allowed: true } })) });
  vi.mocked(api.getWorkflow).mockResolvedValue(w);
  vi.mocked(api.decidePair).mockRejectedValue(new ApiError('stale', 409, { code: 'STALE_PAIR_REVISION' }));
  workspace(<DecisionWorkspace />);
  const merge = await screen.findByRole('button', { name: 'Đồng ý gộp cặp p' }); expect(merge).toBeEnabled(); fireEvent.click(merge);
  expect(await screen.findByText(/Không xác nhận được kết quả/)).toBeInTheDocument();
  expect(screen.getByText('Đồng ý gộp · p')).toBeInTheDocument();
  expect(api.decidePair).toHaveBeenCalledTimes(1);
});
it('run change/unmount cancels initial and active-operation polling reads', async () => {
  vi.useFakeTimers(); const signals: AbortSignal[] = [];
  vi.mocked(api.getWorkflow).mockImplementation((_run, signal) => {
    if (signal) signals.push(signal);
    if (signals.length > 1) return new Promise((_resolve, reject) => signal?.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError'))));
    return Promise.resolve({ ...w, active_operation: { operation_id: 'op', run_id: 'r', operation_type: 'CLUSTER', status: 'RUNNING', stage: 'compute', progress: {}, error_code: null, result: {} } });
  });
  const view = render(<SessionCacheProvider userId="test"><SessionWorkspace key="r" runId="r"><span>Workspace</span></SessionWorkspace></SessionCacheProvider>);
  await act(async () => { await vi.advanceTimersByTimeAsync(0); });
  await act(async () => { await vi.advanceTimersByTimeAsync(1800); });
  view.rerender(<SessionCacheProvider userId="test"><SessionWorkspace key="second" runId="second"><span>Workspace</span></SessionWorkspace></SessionCacheProvider>);
  expect(signals[1].aborted).toBe(true); view.unmount(); expect(signals.slice(1).every(s => s.aborted)).toBe(true);
  expect(api.finalizeDecisions).not.toHaveBeenCalled();
});
it('keeps hồ sơ đại diện actions locked until queued finalization finishes',async()=>{
 vi.mocked(api.getGoldenRecords).mockResolvedValue({items:[golden],total:1,next_cursor:null});
 workspace(<GoldenRecords/>,{...w,workflow_state:'GOLDEN_REVIEW',cluster_revision:1,
  allowed_actions:['finalize-golden','resolve-golden'],active_operation:{operation_id:'final-job',run_id:'r',
   operation_type:'FINALIZE_GOLDEN',status:'RUNNING',stage:'finalize-golden',progress:{},error_code:null,result:{}}});
 await screen.findByText('Hồ sơ đại diện',{selector:'h2'});
 expect(screen.getByRole('button',{name:'Chốt phiên'})).toBeDisabled();
 expect(screen.queryByRole('button',{name:'Chuẩn bị xuất CSV'})).not.toBeInTheDocument();
});
