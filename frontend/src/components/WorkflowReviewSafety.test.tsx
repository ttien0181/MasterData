import {cleanup,fireEvent,render,screen,waitFor,within} from '@testing-library/react';
import {afterEach,expect,it,vi} from 'vitest';
import {WorkflowContext} from './SessionWorkspace';
import {SessionCacheProvider} from './SessionCache';
import PairComparison from './PairComparison';
import PairBulkConfirmation from './PairBulkConfirmation';
import PairReviewRow from './PairReviewRow';
import PairConflicts from './PairConflicts';
import * as api from '@/lib/workflowApi';
import type {PairRead,WorkflowRead} from '@/lib/workflowContracts';
vi.mock('@/lib/workflowApi',async original=>({...await original<typeof api>(),previewBulkDecisions:vi.fn(),getConflicts:vi.fn()}));
afterEach(()=>{cleanup();vi.clearAllMocks();});
const w:WorkflowRead={run_id:'r',workflow_version:2,workflow_state:'DECIDE_REVIEW',workflow_revision:4,decision_revision:1,cluster_revision:0,golden_revision:0,counts:{},allowed_actions:[],blockers:[],active_operation:null,retryable_operation:null};
const pair:PairRead={pair_id:'p',revision:1,decision:'UNDECIDED',score_unit:'rule',baseline:{id:'p',left_id:'a',right_id:'b',decision:'REVIEW',score:3,field_evidence:[]},eligibility:{manual_allowed:true,auto_allowed:false,blocked_reasons:[]},left_record:{record_id:'a',source_system:'CRM',raw_fields:{name:'Same',email:'left@example.com'},normalized_fields:{name:'same'},extra_fields:{custom:'left'}},right_record:{record_id:'b',source_system:'BILL',raw_fields:{name:'Same',email:'right@example.com'},normalized_fields:{name:'same'},extra_fields:{custom:'right'}}};
it('prioritizes differences and includes extra source fields in comparison',()=>{
 render(<PairComparison pair={pair} close={vi.fn()}/>);
 expect(screen.getByText('left')).toBeInTheDocument();
 const rows=within(screen.getByRole('table')).getAllByRole('row');
 expect(rows.findIndex(r=>r.textContent?.includes('left@example.com'))).toBeLessThan(rows.findIndex(r=>r.textContent?.includes('Same')));
});
it('explains current cluster context and links both published memberships',()=>{
 render(<PairComparison pair={{...pair,cluster_context:{revision:2,stale:true,left_cluster:{cluster_id:'c1',member_count:3},right_cluster:{cluster_id:'c2',member_count:1}}}} close={vi.fn()}/>);
 expect(screen.getByText(/Ngữ cảnh nhóm đã cũ/)).toBeInTheDocument();expect(screen.getByRole('link',{name:/c1/})).toHaveAttribute('href',expect.stringContaining('cluster=c1'));expect(screen.getByRole('link',{name:/c2/})).toBeInTheDocument();
});
it('prevents resolved decisions from being silently overwritten by normal buttons',()=>{
 render(<table><tbody><PairReviewRow pair={{...pair,decision:'DENIED'}} selected={false} toggle={vi.fn()} canWrite busy={false} decide={vi.fn()} compare={vi.fn()} explain={vi.fn()} amend={vi.fn()}/></tbody></table>);
 expect(screen.getByRole('button',{name:'Accept cặp p'})).toBeDisabled();
 expect(screen.getByRole('button',{name:'Sửa quyết định cặp p'})).toBeEnabled();
});
it('requires a new preview if decision revisions change while waiting in the queue',async()=>{
 vi.mocked(api.previewBulkDecisions).mockResolvedValue({total:3,applicable:2,blocked:0,skipped:1,workflow_revision:4,decision_revision:1,preview_token:'token'});
 const body={request_id:'b',expected_workflow_revision:4,expected_decision_revision:1,decision:'deny' as const,scope:{filter:{}}};
 const confirm=vi.fn();const host=(workflow:WorkflowRead)=><SessionCacheProvider userId="u"><WorkflowContext.Provider value={{workflow,refresh:vi.fn()}}><PairBulkConfirmation body={body} label="All" busy={false} error="" close={vi.fn()} confirm={confirm} queued/></WorkflowContext.Provider></SessionCacheProvider>;
 const view=render(host(w));await waitFor(()=>expect(screen.getByRole('button',{name:'Xác nhận Deny'})).toBeEnabled());
 fireEvent.click(screen.getByRole('button',{name:'Xác nhận Deny'}));expect(confirm).toHaveBeenCalledWith(expect.objectContaining({preview_token:'token'}));
 view.rerender(host({...w,decision_revision:2,workflow_revision:5}));expect(screen.getByRole('button',{name:'Xác nhận Deny'})).toBeDisabled();
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
