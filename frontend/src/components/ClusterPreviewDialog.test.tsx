import {act,cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import {afterEach,expect,it,vi} from 'vitest';
import {WorkflowContext} from './SessionWorkspace';
import {SessionCacheProvider,sessionKey,useSessionCache} from './SessionCache';
import ClusterPreviewDialog from './ClusterPreviewDialog';
import * as api from '@/lib/workflowApi';
import type {ClusterPreview,OperationRead,WorkflowRead} from '@/lib/workflowContracts';
vi.mock('@/lib/workflowApi',async original=>({...await original<typeof api>(),previewClusters:vi.fn(),getPreviewGroups:vi.fn(),getOperation:vi.fn(),finalizeDecisions:vi.fn(),approveGroup:vi.fn()}));
afterEach(()=>{cleanup();vi.clearAllMocks();vi.useRealTimers();});
const w:WorkflowRead={run_id:'r',workflow_version:2,assessment_id:'a',workflow_state:'DECIDE_REVIEW',workflow_revision:4,decision_revision:1,cluster_revision:0,golden_revision:0,counts:{},allowed_actions:['finalize-decisions','finalize-partial','approve-group'],blockers:[],active_operation:null,retryable_operation:null};
const result:ClusterPreview={preview_token:'token',publication:{publication_status:'PARTIAL',clusters:1,multi_record_groups:1,singletons:0,covered_records:2,unresolved_records:3,total_records:5},groups:[{member_record_ids:['a','b'],singleton:false,status:'DRAFT'}],conflicts:[],conflict_count:0,pending_regions:[{record_ids:['c','d','e'],pending_pair_ids:['p'],record_count:3}],source_decision_revision:1,source_assessment_id:'a',allow_partial:true};
const op=(data=result):OperationRead=>({operation_id:'preview',run_id:'r',operation_type:'CLUSTER_PREVIEW',status:'COMPLETED',stage:'complete',progress:{},error_code:null,result:data as unknown as Record<string,unknown>});
const host=(workflow=w,pending=0)=><SessionCacheProvider userId="preview-user"><WorkflowContext.Provider value={{workflow,refresh:vi.fn().mockResolvedValue(undefined)}}><ClusterPreviewDialog partial pending={pending} attention={0} close={vi.fn()} created={vi.fn()}/></WorkflowContext.Provider></SessionCacheProvider>;
it('previews safe groups and binds creation to token even after workflow-only revision changes',async()=>{
 vi.mocked(api.previewClusters).mockResolvedValue(op());vi.mocked(api.finalizeDecisions).mockResolvedValue(op());
 const view=render(host());await screen.findByText('a / b');expect(api.finalizeDecisions).not.toHaveBeenCalled();
 expect(screen.getByText(/2 \/ 5 bản ghi/)).toBeInTheDocument();expect(screen.getByText(/c \/ d \/ e/)).toBeInTheDocument();
 view.rerender(host({...w,workflow_revision:5}));fireEvent.click(screen.getByRole('button',{name:'Tạo các nhóm đủ điều kiện'}));
 await waitFor(()=>expect(api.finalizeDecisions).toHaveBeenCalledWith('r',expect.objectContaining({expected_workflow_revision:5,preview_operation_id:'preview',preview_token:'token',allow_partial:true})));
});
it('blocks creation for pending queue work and decision drift',async()=>{
 vi.mocked(api.previewClusters).mockResolvedValue(op());const view=render(host(w,1));await screen.findByText('a / b');
 expect(screen.getByRole('button',{name:'Tạo các nhóm đủ điều kiện'})).toBeDisabled();
 view.rerender(host({...w,decision_revision:2}));expect(screen.getByRole('button',{name:'Tạo các nhóm đủ điều kiện'})).toBeDisabled();expect(screen.getByRole('alert')).toHaveTextContent('đã thay đổi');
});
it('distinguishes server pending pairs from queued local requests in the diagnostic preview',async()=>{
 vi.mocked(api.previewClusters).mockResolvedValue(op());
 render(host({...w,blockers:[{code:'PENDING_DECISIONS',count:7,unit:'pairs'}]},2));await screen.findByText('a / b');
 expect(screen.getByText('Server còn 7 cặp chưa duyệt hoặc đang hoãn.')).toBeInTheDocument();expect(screen.getByText(/2 yêu cầu đang chờ; chỉ xem trước/)).toBeInTheDocument();
});
it('discloses when prospective groups and conflicts show only part of the result',async()=>{
 const groups=Array.from({length:25},(_,index)=>({member_record_ids:[`group-${index}`],singleton:true,status:'DRAFT'}));
 const conflicts=Array.from({length:25},(_,index)=>({pair_id:`conflict-${index}`,reason:'CLUSTER_REVIEW_VETO',record_ids:[`conflict-record-${index}`]}));
 vi.mocked(api.previewClusters).mockResolvedValue(op({...result,publication:{...result.publication,clusters:30},groups,conflicts,conflict_count:40}));
 render(host());await screen.findByText('Hiển thị 25 / 30 nhóm dự kiến.');expect(screen.getByText('Hiển thị 25 / 40 xung đột.')).toBeInTheDocument();
 expect(screen.getByRole('table',{name:'Nhóm dự kiến'}).querySelectorAll('tbody tr')).toHaveLength(25);
});
it('uses total pending pairs and discloses the first 100 record IDs in a large region',async()=>{
 const region={record_ids:Array.from({length:101},(_,index)=>`record-${index+1}`),record_count:140,pending_pair_ids:Array.from({length:25},(_,index)=>`pair-${index}`),pending_pair_count:35};
 vi.mocked(api.previewClusters).mockResolvedValue(op({...result,pending_regions:[region]}));
 render(host());await screen.findByText(/35 cặp còn chờ/);expect(screen.getByText('Hiển thị 100 / 140 mã bản ghi (tối đa 100).')).toBeInTheDocument();
 const members=screen.getByText(/record-1 \/ record-2/);expect(members).toHaveTextContent('record-100');expect(members).not.toHaveTextContent('record-101');
});
it('falls back to returned pending IDs when the total pending-pair count is unavailable',async()=>{
 vi.mocked(api.previewClusters).mockResolvedValue(op());render(host());await screen.findByText(/1 cặp còn chờ/);
 expect(screen.getByText('Hiển thị 3 / 3 mã bản ghi (tối đa 100).')).toBeInTheDocument();
});
it('requires explicit reason for a bridge exception and recalculates preview after approval',async()=>{
 const conflict={pair_id:'p',reason:'CLUSTER_BRIDGE_MISSING_EVIDENCE',record_ids:['a','b','c']};
 vi.mocked(api.previewClusters).mockResolvedValueOnce(op({...result,conflicts:[conflict],conflict_count:1})).mockResolvedValue(op());vi.mocked(api.approveGroup).mockResolvedValue(op());
 render(host());fireEvent.click(await screen.findByRole('button',{name:'Duyệt ngoại lệ nhóm'}));
 expect(screen.getByText('a / b / c')).toBeInTheDocument();expect(screen.getByRole('button',{name:'Xác nhận ngoại lệ nhóm'})).toBeDisabled();
 fireEvent.change(screen.getByRole('textbox',{name:'Lý do ngoại lệ nhóm'}),{target:{value:'Cross checked identity'}});fireEvent.click(screen.getByRole('button',{name:'Xác nhận ngoại lệ nhóm'}));
 await waitFor(()=>expect(api.approveGroup).toHaveBeenCalledWith('r',expect.objectContaining({record_ids:['a','b','c'],reason:'Cross checked identity'})));
 await waitFor(()=>expect(api.previewClusters).toHaveBeenCalledTimes(2));
});
it('recalculates from refreshed server revisions before the provider rerenders after approval',async()=>{
 let approved=false;const next={...w,workflow_revision:5,decision_revision:2};
 vi.mocked(api.previewClusters).mockResolvedValueOnce(op({...result,conflicts:[{pair_id:'p',reason:'CLUSTER_BRIDGE_MISSING_EVIDENCE',record_ids:['a','b','c']}],conflict_count:1})).mockResolvedValue(op({...result,source_decision_revision:2}));
 vi.mocked(api.approveGroup).mockImplementation(async()=>{approved=true;return op();});
 function CachedHost(){const cache=useSessionCache();const refresh=async()=>{if(approved)cache.client.setQueryData(sessionKey(cache.userId,'r','workflow'),next);};return <WorkflowContext.Provider value={{workflow:w,refresh}}><ClusterPreviewDialog partial pending={0} attention={0} close={vi.fn()} created={vi.fn()}/></WorkflowContext.Provider>;}
 render(<SessionCacheProvider userId="approval-refresh"><CachedHost/></SessionCacheProvider>);
 fireEvent.click(await screen.findByRole('button',{name:'Duyệt ngoại lệ nhóm'}));fireEvent.change(screen.getByRole('textbox',{name:'Lý do ngoại lệ nhóm'}),{target:{value:'Cross checked'}});fireEvent.click(screen.getByRole('button',{name:'Xác nhận ngoại lệ nhóm'}));
 await waitFor(()=>expect(api.previewClusters).toHaveBeenLastCalledWith('r',expect.objectContaining({expected_workflow_revision:5,expected_decision_revision:2})));
});
it('shows failed preview operation without allowing creation',async()=>{
 vi.mocked(api.previewClusters).mockResolvedValue({...op(),status:'FAILED',error_code:'PREVIEW_ERROR'});render(host());await screen.findByText(/PREVIEW_ERROR/);expect(screen.getByRole('button',{name:'Tạo các nhóm đủ điều kiện'})).toBeDisabled();
});
it('polls queued preview progress before enabling creation',async()=>{
 vi.useFakeTimers();vi.mocked(api.previewClusters).mockResolvedValue({...op(),status:'QUEUED',stage:'queued',result:{}});vi.mocked(api.getOperation).mockResolvedValue(op());
 render(host());await act(async()=>{});expect(screen.getByRole('status')).toHaveTextContent('queued');expect(screen.getByRole('button',{name:'Tạo các nhóm đủ điều kiện'})).toBeDisabled();
 await act(async()=>{await vi.advanceTimersByTimeAsync(1500);});expect(screen.getByText('a / b')).toBeInTheDocument();expect(screen.getByRole('button',{name:'Tạo các nhóm đủ điều kiện'})).toBeEnabled();
});
it('recovers a progress read failure by reading the same operation without replaying preview creation',async()=>{
 vi.useFakeTimers();vi.mocked(api.previewClusters).mockResolvedValue({...op(),status:'RUNNING',stage:'groups',result:{}});vi.mocked(api.getOperation).mockRejectedValueOnce(new Error('Offline')).mockResolvedValue(op());
 render(host());await act(async()=>{});await act(async()=>{await vi.advanceTimersByTimeAsync(1500);});expect(screen.getByRole('alert')).toHaveTextContent('Offline');
 fireEvent.click(screen.getByRole('button',{name:'Đọc lại tiến độ'}));await act(async()=>{});expect(screen.getByText('a / b')).toBeInTheDocument();expect(api.previewClusters).toHaveBeenCalledTimes(1);
});

it('shows names, sources and a member comparison before creation',async()=>{
 const group={...result.groups[0],id:'group',display_name:'Nguyễn Văn An',preview_members:[
  {record_id:'a',source_system:'CRM',name:'Nguyễn Văn An',fields:{name:'Nguyễn Văn An',dob:'1990-05-15'}},
  {record_id:'b',source_system:'ERP',name:'Nguyễn Văn An',fields:{name:'Nguyễn Văn An',dob:'1990-05-16'}}],
  golden_fields:{name:{value:'Nguyễn Văn An'}}};
 vi.mocked(api.previewClusters).mockResolvedValue(op({...result,groups:[group]}));
 render(host());await screen.findByText('Nguyễn Văn An');
 fireEvent.click(screen.getByRole('button',{name:'Đối chiếu nhóm Nguyễn Văn An'}));
 expect(screen.getByRole('table',{name:'Đối chiếu thành viên nhóm'})).toBeVisible();
 expect(screen.getByText('1990-05-15')).toBeVisible();expect(screen.getByText('1990-05-16')).toBeVisible();
 expect(screen.getByText('Golden dự kiến')).toBeVisible();
});

it('reads subsequent pages without requesting another preview job',async()=>{
 vi.mocked(api.previewClusters).mockResolvedValue(op({...result,publication:{...result.publication,clusters:30},groups_next_cursor:'25'}));
 vi.mocked(api.getPreviewGroups).mockResolvedValue({items:[{member_record_ids:['last'],singleton:true,status:'SINGLETON',display_name:'Người cuối'}],total:30,next_cursor:null,source_decision_revision:1,source_assessment_id:'a',stale:false});
 render(host());await screen.findByText('a / b');fireEvent.click(screen.getByRole('button',{name:'Trang nhóm tiếp'}));
 await screen.findByText('Người cuối');expect(api.previewClusters).toHaveBeenCalledTimes(1);
 expect(api.getPreviewGroups).toHaveBeenCalledWith('r','preview',expect.any(URLSearchParams));
});

it('offers recovery when decisions changed during snapshot preparation',async()=>{
 vi.mocked(api.previewClusters).mockResolvedValue({...op(),error_code:'CLUSTER_PREVIEW_STALE',result:{stale:true}});
 render(host());await screen.findByText(/Quyết định đã thay đổi trong lúc chuẩn bị/);
 expect(screen.getByRole('button',{name:'Tạo các nhóm đủ điều kiện'})).toBeDisabled();
 expect(screen.getByRole('button',{name:'Tính lại xem trước'})).toBeEnabled();
});

it('keeps a structured group reason tied to an explanatory note',async()=>{
 vi.mocked(api.previewClusters).mockResolvedValue(op({...result,conflicts:[{pair_id:'p',reason:'CLUSTER_BRIDGE_MISSING_EVIDENCE',record_ids:['a','b']}],conflict_count:1}));
 vi.mocked(api.approveGroup).mockResolvedValue(op());render(host());
 fireEvent.click(await screen.findByRole('button',{name:'Duyệt ngoại lệ nhóm'}));
 fireEvent.change(screen.getByRole('combobox',{name:'Loại lý do ngoại lệ'}),{target:{value:'CONTACT_HISTORY_CONFIRMED'}});
 expect(screen.getByRole('button',{name:'Xác nhận ngoại lệ nhóm'})).toBeDisabled();
 fireEvent.change(screen.getByRole('textbox',{name:'Lý do ngoại lệ nhóm'}),{target:{value:'Đã kiểm tra liên hệ ở cả hai nguồn'}});
 fireEvent.click(screen.getByRole('button',{name:'Xác nhận ngoại lệ nhóm'}));
 await waitFor(()=>expect(api.approveGroup).toHaveBeenCalledWith('r',expect.objectContaining({reason:'CONTACT_HISTORY_CONFIRMED: Đã kiểm tra liên hệ ở cả hai nguồn'})));
});
