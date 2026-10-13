import {cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import {afterEach,expect,it,vi} from 'vitest';
import {WorkflowContext} from './SessionWorkspace';
import {SessionCacheProvider} from './SessionCache';
import ClusterPreviewDialog from './ClusterPreviewDialog';
import * as api from '@/lib/workflowApi';
import type {ClusterPreview,OperationRead,WorkflowRead} from '@/lib/workflowContracts';
vi.mock('@/lib/workflowApi',async original=>({...await original<typeof api>(),previewClusters:vi.fn(),getOperation:vi.fn(),finalizeDecisions:vi.fn(),approveGroup:vi.fn()}));
afterEach(()=>{cleanup();vi.clearAllMocks();});
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
it('requires explicit reason for a bridge exception and recalculates preview after approval',async()=>{
 const conflict={pair_id:'p',reason:'CLUSTER_BRIDGE_MISSING_EVIDENCE',record_ids:['a','b','c']};
 vi.mocked(api.previewClusters).mockResolvedValueOnce(op({...result,conflicts:[conflict],conflict_count:1})).mockResolvedValue(op());vi.mocked(api.approveGroup).mockResolvedValue(op());
 render(host());fireEvent.click(await screen.findByRole('button',{name:'Duyệt ngoại lệ nhóm'}));
 expect(screen.getByText('a / b / c')).toBeInTheDocument();expect(screen.getByRole('button',{name:'Xác nhận ngoại lệ nhóm'})).toBeDisabled();
 fireEvent.change(screen.getByRole('textbox',{name:'Lý do ngoại lệ nhóm'}),{target:{value:'Cross checked identity'}});fireEvent.click(screen.getByRole('button',{name:'Xác nhận ngoại lệ nhóm'}));
 await waitFor(()=>expect(api.approveGroup).toHaveBeenCalledWith('r',expect.objectContaining({record_ids:['a','b','c'],reason:'Cross checked identity'})));
 await waitFor(()=>expect(api.previewClusters).toHaveBeenCalledTimes(2));
});
it('shows failed preview operation without allowing creation',async()=>{
 vi.mocked(api.previewClusters).mockResolvedValue({...op(),status:'FAILED',error_code:'PREVIEW_ERROR'});render(host());await screen.findByText(/PREVIEW_ERROR/);expect(screen.getByRole('button',{name:'Tạo các nhóm đủ điều kiện'})).toBeDisabled();
});
