import {cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import {afterEach,expect,it,vi} from 'vitest';
import {WorkflowContext} from './SessionWorkspace';
import {SessionCacheProvider} from './SessionCache';
import GroupApprovalDialog from './GroupApprovalDialog';
import GroupMembersTable from './GroupMembersTable';
import * as api from '@/lib/workflowApi';
import type {PreviewMember,WorkflowRead} from '@/lib/workflowContracts';
vi.mock('@/lib/workflowApi',async original=>({...await original<typeof api>(),approveGroup:vi.fn(),getReviewGroupMembers:vi.fn(),getReviewGroupEvidence:vi.fn()}));
afterEach(()=>{cleanup();vi.clearAllMocks();});
const w:WorkflowRead={run_id:'r',workflow_version:2,assessment_id:'a',workflow_state:'DECIDE_REVIEW',workflow_revision:4,decision_revision:1,cluster_revision:0,golden_revision:0,counts:{},allowed_actions:['approve-group'],blockers:[],active_operation:null,retryable_operation:null};
const members:PreviewMember[]=Array.from({length:100},(_,i)=>({record_id:`r${i}`,source_system:'CRM',name:`Person ${i}`,fields:{name:`Person ${i}`}}));
it('approves every frozen member in a hundred-member group, including later pages',async()=>{
 vi.mocked(api.approveGroup).mockResolvedValue({operation_id:'approved',run_id:'r',operation_type:'GROUP_APPROVAL',status:'COMPLETED',stage:'done',progress:{},error_code:null,result:{}});
 render(<SessionCacheProvider userId="large"><WorkflowContext.Provider value={{workflow:w,refresh:vi.fn()}}><GroupApprovalDialog recordIds={members.map(m=>m.record_id)} members={members} close={vi.fn()} approved={vi.fn()}/></WorkflowContext.Provider></SessionCacheProvider>);
 fireEvent.change(screen.getByRole('textbox',{name:'Lý do ngoại lệ nhóm'}),{target:{value:'Verified all members'}});
 const confirm=screen.getByRole('button',{name:'Xác nhận ngoại lệ nhóm'});expect(confirm).toBeEnabled();fireEvent.click(confirm);
 await waitFor(()=>expect(api.approveGroup).toHaveBeenCalledWith('r',expect.objectContaining({record_ids:members.map(m=>m.record_id)})));
});
it('shows totals and pages the member comparison through the last member',async()=>{
 render(<GroupMembersTable members={members}/>);
 expect(screen.getByText('Hiển thị 25 / 100 thành viên')).toBeVisible();
 expect(screen.queryByText('Person 99')).not.toBeInTheDocument();
 for(let i=0;i<3;i++)fireEvent.click(screen.getByRole('button',{name:'Trang thành viên tiếp'}));
 await waitFor(()=>expect(screen.getAllByText('Person 99').length).toBeGreaterThan(0));
 expect(screen.getByRole('button',{name:'Trang thành viên tiếp'})).toBeDisabled();
});
it('pages frozen server members and sends the complete receipt rather than a displayed page',async()=>{
 const fingerprint='f'.repeat(64);
 vi.mocked(api.getReviewGroupMembers).mockResolvedValue({items:members.slice(25,50),total:100,next_cursor:'50',stale:false});
 render(<SessionCacheProvider userId="large-remote"><WorkflowContext.Provider value={{workflow:w,refresh:vi.fn()}}><GroupApprovalDialog recordIds={members.map(m=>m.record_id)} members={members.slice(0,25)} fingerprint={fingerprint} previewOperationId="preview" totalMemberCount={100} close={vi.fn()} approved={vi.fn()}/></WorkflowContext.Provider></SessionCacheProvider>);
 fireEvent.click(screen.getByRole('button',{name:'Trang thành viên tiếp'}));
 await waitFor(()=>expect(screen.getAllByText('Person 49').length).toBeGreaterThan(0));
 expect(api.getReviewGroupMembers).toHaveBeenCalledWith('r','preview',fingerprint,expect.any(URLSearchParams));
 fireEvent.change(screen.getByRole('textbox',{name:'Lý do ngoại lệ nhóm'}),{target:{value:'Verified all members'}});
 fireEvent.click(screen.getByRole('button',{name:'Xác nhận ngoại lệ nhóm'}));
 await waitFor(()=>expect(api.approveGroup).toHaveBeenCalledWith('r',expect.objectContaining({record_ids:members.map(m=>m.record_id),fingerprint,preview_operation_id:'preview'})));
});
it('shows paginated additional evidence on request',async()=>{
 vi.mocked(api.getReviewGroupEvidence).mockResolvedValue({items:[{id:'computed',left_id:'r98',right_id:'r99',decision:'REVIEW',score:1,field_evidence:[],computed_only:true}],total:4950,next_cursor:'25'});
 render(<SessionCacheProvider userId="large-evidence"><WorkflowContext.Provider value={{workflow:w,refresh:vi.fn()}}><GroupApprovalDialog recordIds={members.map(m=>m.record_id)} members={members.slice(0,25)} fingerprint={'f'.repeat(64)} previewOperationId="preview" close={vi.fn()} approved={vi.fn()}/></WorkflowContext.Provider></SessionCacheProvider>);
 fireEvent.click(screen.getByRole('button',{name:'Xem thông tin đối chiếu giữa các thành viên'}));
 expect(await screen.findByText(/1 \/ 4950 cặp/)).toBeVisible();
 expect(screen.getByText(/r98 \/ r99/)).toBeVisible();
 expect(screen.getByRole('button',{name:'Trang thông tin đối chiếu tiếp'})).toBeEnabled();
});
