import {cleanup,fireEvent,render,screen,waitFor,within} from '@testing-library/react';
import {afterEach,expect,it,vi} from 'vitest';
import {WorkflowContext} from './SessionWorkspace';
import {SessionCacheProvider} from './SessionCache';
import PairComparison from './PairComparison';
import PairBulkConfirmation from './PairBulkConfirmation';
import PairReviewRow from './PairReviewRow';
import PairConflicts from './PairConflicts';
import PairAcceptReason from './PairAcceptReason';
import GroupApprovalDialog from './GroupApprovalDialog';
import {ApiError} from '@/lib/api';
import * as api from '@/lib/workflowApi';
import type {PairRead,WorkflowRead} from '@/lib/workflowContracts';
vi.mock('@/lib/workflowApi',async original=>({...await original<typeof api>(),previewBulkDecisions:vi.fn(),getConflicts:vi.fn(),approveGroup:vi.fn()}));
afterEach(()=>{cleanup();vi.clearAllMocks();});
const w:WorkflowRead={run_id:'r',workflow_version:2,workflow_state:'DECIDE_REVIEW',workflow_revision:4,decision_revision:1,cluster_revision:0,golden_revision:0,counts:{},allowed_actions:[],blockers:[],active_operation:null,retryable_operation:null};
const pair:PairRead={pair_id:'p',revision:1,decision:'UNDECIDED',score_unit:'rule',baseline:{id:'p',left_id:'a',right_id:'b',decision:'REVIEW',score:3,field_evidence:[]},eligibility:{manual_allowed:true,auto_allowed:false,blocked_reasons:[]},left_record:{record_id:'a',source_system:'CRM',raw_fields:{name:'Same',email:'left@example.com'},normalized_fields:{name:'same'},extra_fields:{custom:'left'}},right_record:{record_id:'b',source_system:'BILL',raw_fields:{name:'Same',email:'right@example.com'},normalized_fields:{name:'same'},extra_fields:{custom:'right'}}};
it('prioritizes differences and includes extra source fields in comparison',()=>{
 render(<PairComparison pair={pair} close={vi.fn()}/>);
 fireEvent.click(screen.getByRole('checkbox',{name:'Trường bổ sung'}));
 expect(screen.getByText('left')).toBeInTheDocument();
 const rows=within(screen.getByRole('table')).getAllByRole('row');
 expect(rows.findIndex(r=>r.textContent?.includes('left@example.com'))).toBeLessThan(rows.findIndex(r=>r.textContent?.includes('Same')));
});
it('offers only Đồng ý gộp and Không gộp and sends an explicit negative decision',()=>{
 const decide=vi.fn();render(<table><tbody><PairReviewRow pair={pair} selected={false} toggle={vi.fn()} canWrite busy={false} decide={decide} compare={vi.fn()}/></tbody></table>);
 expect(screen.getByRole('button',{name:'Đồng ý gộp cặp p'})).toBeEnabled();
 expect(screen.getByRole('button',{name:'Không gộp cặp p'})).toBeEnabled();
 expect(screen.queryByRole('button',{name:'Hoãn cặp p'})).not.toBeInTheDocument();
 fireEvent.click(screen.getByRole('button',{name:'Không gộp cặp p'}));expect(decide).toHaveBeenCalledWith('deny');
});
it('rejects an oversized soft Đồng ý gộp reason and closes the write gate after assessment changes',()=>{
 const confirm=vi.fn();const host=(workflow:WorkflowRead)=><SessionCacheProvider userId="accept-reason"><WorkflowContext.Provider value={{workflow,refresh:vi.fn()}}><PairAcceptReason pair={pair} close={vi.fn()} confirm={confirm}/></WorkflowContext.Provider></SessionCacheProvider>;
 const view=render(host(w));const reason=screen.getByRole('textbox',{name:'Lý do Đồng ý gộp'}),submit=screen.getByRole('button',{name:'Xác nhận Đồng ý gộp'});
 fireEvent.change(reason,{target:{value:'x'.repeat(501)}});expect(submit).toBeDisabled();fireEvent.change(reason,{target:{value:'Documents checked'}});expect(submit).toBeEnabled();
 view.rerender(host({...w,assessment_id:'new'}));expect(submit).toBeDisabled();fireEvent.click(submit);expect(confirm).not.toHaveBeenCalled();
});
it('requires a reason before bulk Đồng ý gộp when preview contains soft conflict pairs',async()=>{
 vi.mocked(api.previewBulkDecisions).mockResolvedValue({total:2,applicable:2,blocked:0,skipped:0,workflow_revision:4,decision_revision:1,reason_required:1});
 const confirm=vi.fn(),body={request_id:'b',expected_workflow_revision:4,expected_decision_revision:1,decision:'merge' as const,scope:{filter:{}}};
 render(<SessionCacheProvider userId="bulk-reason"><WorkflowContext.Provider value={{workflow:w,refresh:vi.fn()}}><PairBulkConfirmation body={body} label="All" busy={false} error="" close={vi.fn()} confirm={confirm}/></WorkflowContext.Provider></SessionCacheProvider>);
 await screen.findByText('2 cặp có thể Đồng ý gộp');expect(screen.getByRole('button',{name:'Xác nhận Đồng ý gộp'})).toBeDisabled();
 fireEvent.change(screen.getByRole('textbox',{name:'Lý do Đồng ý gộp'}),{target:{value:'   '}});expect(screen.getByRole('button',{name:'Xác nhận Đồng ý gộp'})).toBeDisabled();
 fireEvent.change(screen.getByRole('textbox',{name:'Lý do Đồng ý gộp'}),{target:{value:'Source verified'}});fireEvent.click(screen.getByRole('button',{name:'Xác nhận Đồng ý gộp'}));
 expect(confirm).toHaveBeenCalledWith(expect.anything(),'Source verified');
});

it('does not ask for a reason or offer Đồng ý gộp when preview has zero eligible pairs',async()=>{
 vi.mocked(api.previewBulkDecisions).mockResolvedValue({total:194821,applicable:0,blocked:194821,skipped:0,workflow_revision:4,decision_revision:1});
 const body={request_id:'empty',expected_workflow_revision:4,expected_decision_revision:1,decision:'merge' as const,scope:{filter:{group:'C'}}};
 render(<SessionCacheProvider userId="empty-bulk"><WorkflowContext.Provider value={{workflow:w,refresh:vi.fn()}}><PairBulkConfirmation body={body} label="Nhóm C" busy={false} error="" close={vi.fn()} confirm={vi.fn()} queued/></WorkflowContext.Provider></SessionCacheProvider>);
 await screen.findByText('Không có cặp đủ điều kiện Đồng ý gộp');
 expect(screen.queryByRole('textbox',{name:'Lý do Đồng ý gộp'})).not.toBeInTheDocument();
 expect(screen.queryByRole('button',{name:'Xác nhận Đồng ý gộp'})).not.toBeInTheDocument();
 expect(screen.getByRole('button',{name:'Đóng'})).toBeEnabled();
});
it('omits group context and membership links from the pair comparison',()=>{
 render(<PairComparison pair={{...pair,cluster_context:{revision:2,stale:true,left_cluster:{cluster_id:'c1',member_count:3},right_cluster:{cluster_id:'c2',member_count:1}}}} close={vi.fn()}/>);
 expect(screen.queryByRole('region',{name:'Ngữ cảnh nhóm'})).not.toBeInTheDocument();expect(screen.queryByText(/Ngữ cảnh nhóm/)).not.toBeInTheDocument();expect(screen.queryByRole('link',{name:/c1|c2/})).not.toBeInTheDocument();
});
it('prevents resolved decisions from being silently overwritten by normal buttons',()=>{
 render(<table><tbody><PairReviewRow pair={{...pair,decision:'DENIED'}} selected={false} toggle={vi.fn()} canWrite busy={false} decide={vi.fn()} compare={vi.fn()}/></tbody></table>);
 expect(screen.getByRole('button',{name:'Đồng ý gộp cặp p'})).toBeDisabled();
 expect(screen.getByRole('button',{name:'Không gộp cặp p'})).toBeDisabled();
 const amend=vi.fn();render(<PairComparison pair={{...pair,decision:'DENIED'}} close={vi.fn()} amend={amend}/>);
 fireEvent.click(screen.getByRole('button',{name:'Sửa quyết định'}));expect(amend).toHaveBeenCalledTimes(1);
});
it('requires a new preview if decision revisions change while waiting in the queue',async()=>{
 vi.mocked(api.previewBulkDecisions).mockResolvedValue({total:3,applicable:2,blocked:0,skipped:1,workflow_revision:4,decision_revision:1,preview_token:'token'});
 const body={request_id:'b',expected_workflow_revision:4,expected_decision_revision:1,decision:'deny' as const,scope:{filter:{}}};
 const confirm=vi.fn();const host=(workflow:WorkflowRead)=><SessionCacheProvider userId="u"><WorkflowContext.Provider value={{workflow,refresh:vi.fn()}}><PairBulkConfirmation body={body} label="All" busy={false} error="" close={vi.fn()} confirm={confirm} queued/></WorkflowContext.Provider></SessionCacheProvider>;
 const view=render(host(w));await waitFor(()=>expect(screen.getByRole('button',{name:'Xác nhận Không gộp'})).toBeEnabled());
 fireEvent.click(screen.getByRole('button',{name:'Xác nhận Không gộp'}));expect(confirm).toHaveBeenCalledWith(expect.objectContaining({preview_token:'token'}));
 view.rerender(host({...w,decision_revision:2,workflow_revision:5}));expect(screen.getByRole('button',{name:'Xác nhận Không gộp'})).toBeDisabled();
});
it('refreshes conflicts after reassessment even when decision revision is unchanged',async()=>{
 const conflict=(reason:string)=>({conflict_id:'c',pair_id:'p',reason,record_ids:['a','b'],records:[{record_id:'a',source_system:'CRM',raw_fields:{name:reason==='CLUSTER_REVIEW_VETO'?'Old evidence':'New assessment evidence'},normalized_fields:{},extra_fields:{}}],pairs:[],related_pair_count:0});
 vi.mocked(api.getConflicts).mockResolvedValueOnce({items:[conflict('CLUSTER_REVIEW_VETO')],total:1,next_cursor:null})
  .mockResolvedValueOnce({items:[conflict('ASSESSMENT_ACCEPTED_CONFLICT')],total:1,next_cursor:null});
 const host=(workflow:WorkflowRead)=><SessionCacheProvider userId="conflict-owner"><WorkflowContext.Provider value={{workflow,refresh:vi.fn()}}><PairConflicts close={vi.fn()} compare={vi.fn()} amend={vi.fn()}/></WorkflowContext.Provider></SessionCacheProvider>;
 const view=render(host(w));await screen.findByText(/Old evidence/);
 view.rerender(host({...w,workflow_revision:5,assessment_id:'new-assessment'}));
 await screen.findByText(/New assessment evidence/);
});
it('offers group exceptions only for bridge conflicts and requires the exact members and reason',async()=>{
 const conflict=(reason:string)=>({conflict_id:reason,pair_id:'p',reason,record_ids:['a','b','c'],records:[],pairs:[],related_pair_count:0});
 vi.mocked(api.getConflicts).mockResolvedValue({items:[conflict('CLUSTER_BRIDGE_MISSING_EVIDENCE'),conflict('CLUSTER_REVIEW_VETO'),conflict('CLUSTER_VERIFIED_ID_CONFLICT')],total:3,next_cursor:null});
 vi.mocked(api.approveGroup).mockResolvedValue({operation_id:'approved',run_id:'r',operation_type:'GROUP_APPROVAL',status:'COMPLETED',stage:'complete',progress:{},error_code:null,result:{}});
 render(<SessionCacheProvider userId="conflict-approval"><WorkflowContext.Provider value={{workflow:{...w,allowed_actions:['approve-group']},refresh:vi.fn().mockResolvedValue(undefined)}}><PairConflicts close={vi.fn()} compare={vi.fn()} amend={vi.fn()}/></WorkflowContext.Provider></SessionCacheProvider>);
 const approve=await screen.findByRole('button',{name:'Duyệt ngoại lệ nhóm'});expect(screen.getAllByRole('button',{name:'Duyệt ngoại lệ nhóm'})).toHaveLength(1);fireEvent.click(approve);
 expect(screen.getByText('a / b / c')).toBeInTheDocument();fireEvent.change(screen.getByRole('textbox',{name:'Lý do ngoại lệ nhóm'}),{target:{value:'Evidence checked'}});fireEvent.click(screen.getByRole('button',{name:'Xác nhận ngoại lệ nhóm'}));
 await waitFor(()=>expect(api.approveGroup).toHaveBeenCalledWith('r',expect.objectContaining({record_ids:['a','b','c'],reason:'Evidence checked'})));
});
it('explains the actual hard constraint blocking group approval and preserves the reason after refresh',async()=>{
 vi.mocked(api.approveGroup).mockRejectedValue(new ApiError('Blocked',409,{code:'GROUP_APPROVAL_BLOCKED',reason:'VERIFIED_GOV_ID_CONFLICT'}));
 const refresh=vi.fn().mockResolvedValue(undefined),approved=vi.fn();
 render(<SessionCacheProvider userId="blocked-approval"><WorkflowContext.Provider value={{workflow:{...w,allowed_actions:['approve-group']},refresh}}><GroupApprovalDialog recordIds={['a','b','c']} close={vi.fn()} approved={approved}/></WorkflowContext.Provider></SessionCacheProvider>);
 fireEvent.change(screen.getByRole('textbox',{name:'Lý do ngoại lệ nhóm'}),{target:{value:'Documents checked'}});fireEvent.click(screen.getByRole('button',{name:'Xác nhận ngoại lệ nhóm'}));
 expect(await screen.findByRole('alert')).toHaveTextContent('Giấy tờ đã xác minh mâu thuẫn');expect(screen.getByRole('textbox',{name:'Lý do ngoại lệ nhóm'})).toHaveValue('Documents checked');
 expect(refresh).toHaveBeenCalledTimes(1);expect(api.approveGroup).toHaveBeenCalledTimes(1);expect(approved).not.toHaveBeenCalled();
});
it('retains the existing stale-revision explanation for unrelated group approval conflicts',async()=>{
 vi.mocked(api.approveGroup).mockRejectedValue(new ApiError('Stale',409,{code:'STALE_REVISION'}));
 render(<SessionCacheProvider userId="stale-approval"><WorkflowContext.Provider value={{workflow:{...w,allowed_actions:['approve-group']},refresh:vi.fn().mockResolvedValue(undefined)}}><GroupApprovalDialog recordIds={['a','b']} close={vi.fn()} approved={vi.fn()}/></WorkflowContext.Provider></SessionCacheProvider>);
 fireEvent.change(screen.getByRole('textbox',{name:'Lý do ngoại lệ nhóm'}),{target:{value:'Documents checked'}});fireEvent.click(screen.getByRole('button',{name:'Xác nhận ngoại lệ nhóm'}));
 expect(await screen.findByRole('alert')).toHaveTextContent('Phiên đã thay đổi hoặc chưa đủ điều kiện. Đã tải lại dữ liệu; lựa chọn chưa lưu vẫn được giữ.');
});
