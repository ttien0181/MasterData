import {afterEach,it,expect,vi} from 'vitest';
import {cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import {WorkflowContext} from './SessionWorkspace';
import {SessionCacheProvider} from './SessionCache';
import PairAutomation from './PairAutomation';
import PairReviewRow from './PairReviewRow';
import * as api from '@/lib/workflowApi';
import type {WorkflowRead} from '@/lib/workflowContracts';
vi.mock('@/lib/workflowApi',async original=>({...await original<typeof api>(),shadowAutomation:vi.fn(),applyAutomation:vi.fn(),stopAutomation:vi.fn()}));
afterEach(()=>{cleanup();vi.clearAllMocks();});
const w:WorkflowRead={run_id:'r',workflow_version:2,workflow_state:'DECIDE_REVIEW',workflow_revision:4,decision_revision:1,assessment_id:'a',cluster_revision:0,golden_revision:0,counts:{},allowed_actions:['automation-shadow','automation-apply','automation-stop'],blockers:[],active_operation:null,retryable_operation:null};
const preview={operation_id:'p',run_id:'r',operation_type:'AUTOMATION_SHADOW',status:'COMPLETED',stage:'completed',progress:{},error_code:null,result:{candidate_components:3,qualified_components:1,applicable_pairs:3,can_apply:true,preview_token:'a'.repeat(64),remaining_component_budget:19,source_decision_revision:1,source_assessment_id:'a',blocked_reasons:[]}};
function host(state:WorkflowRead=w,blocked=false){return <SessionCacheProvider userId="u"><WorkflowContext.Provider value={{workflow:state,refresh:vi.fn().mockResolvedValue(undefined)}}><PairAutomation blocked={blocked}/></WorkflowContext.Provider></SessionCacheProvider>;}
it('runs shadow without a certificate and never offers uncertified application',async()=>{
 vi.mocked(api.shadowAutomation).mockResolvedValue({...preview,status:'QUEUED'});
 render(host());fireEvent.click(screen.getByRole('button',{name:'Chạy thử tự động hóa'}));
 await waitFor(()=>expect(api.shadowAutomation).toHaveBeenCalledTimes(1));
 expect(api.applyAutomation).not.toHaveBeenCalled();
 expect(screen.getByRole('button',{name:'Áp dụng tự động'})).toBeDisabled();
});
it('requires explicit current-preview confirmation for the bounded application',async()=>{
 vi.mocked(api.applyAutomation).mockResolvedValue({...preview,operation_type:'AUTOMATION_APPLY',status:'QUEUED'});
 render(host({...w,latest_automation_operation:preview}));
 fireEvent.click(screen.getByRole('button',{name:'Áp dụng tự động'}));expect(api.applyAutomation).not.toHaveBeenCalled();
 fireEvent.click(screen.getByRole('button',{name:'Xác nhận áp dụng 1 cụm'}));
 await waitFor(()=>expect(api.applyAutomation).toHaveBeenCalledWith('r',expect.objectContaining({preview_operation_id:'p',preview_token:'a'.repeat(64),expected_decision_revision:1})));
});
it('disables application for a stale assessment and outstanding review intentions',()=>{
 const view=render(host({...w,latest_automation_operation:{...preview,result:{...preview.result,source_assessment_id:'old'}}}));
 expect(screen.getByRole('button',{name:'Áp dụng tự động'})).toBeDisabled();
 view.rerender(host({...w,latest_automation_operation:preview},true));
 expect(screen.getByRole('button',{name:'Áp dụng tự động'})).toBeDisabled();
});
it('can stop an active automation worker without claiming saved decisions are undone',async()=>{
 vi.mocked(api.stopAutomation).mockResolvedValue({...preview,operation_type:'AUTOMATION_STOP'});
 render(host({...w,active_operation:{...preview,status:'RUNNING'},allowed_actions:['automation-stop']}));
 fireEvent.click(screen.getByRole('button',{name:'Dừng tự động hóa phiên này'}));
 await waitFor(()=>expect(api.stopAutomation).toHaveBeenCalledTimes(1));
 expect(screen.getByText(/Các quyết định đã lưu được giữ lại/)).toBeInTheDocument();
});
it('distinguishes automatic acceptance from a human review',()=>{
 const p={pair_id:'auto-p',decision:'ACCEPTED',revision:1,score_unit:'rule',baseline:{id:'auto-p',left_id:'a',right_id:'b',decision:'MERGEABLE',score:12,field_evidence:[]},eligibility:{manual_allowed:true,auto_allowed:false,blocked_reasons:[]},automation:{source:'AUTOMATIC',operation_id:'auto-op',certificate_hash:'a',component_fingerprint:'b',cohort:'c'}};
 render(<table><tbody><PairReviewRow pair={p} selected={false} toggle={vi.fn()} canWrite={true} busy={false} decide={vi.fn()} compare={vi.fn()} explain={vi.fn()}/></tbody></table>);
 expect(screen.getByText('Đã chấp nhận tự động')).toBeInTheDocument();
});
it('does not describe a repaired rejection as currently accepted automatically',()=>{
 const p={pair_id:'repaired-p',decision:'REJECTED',revision:2,score_unit:'rule',baseline:{id:'repaired-p',left_id:'a',right_id:'b',decision:'MERGEABLE',score:12,field_evidence:[]},eligibility:{manual_allowed:true,auto_allowed:false,blocked_reasons:[]},automation:{source:'AUTOMATIC',operation_id:'old-auto',certificate_hash:'a',component_fingerprint:'b',cohort:'c'}};
 render(<table><tbody><PairReviewRow pair={p} selected={false} toggle={vi.fn()} canWrite={true} busy={false} decide={vi.fn()} compare={vi.fn()} explain={vi.fn()}/></tbody></table>);
 expect(screen.queryByText('Đã chấp nhận tự động')).not.toBeInTheDocument();
});
