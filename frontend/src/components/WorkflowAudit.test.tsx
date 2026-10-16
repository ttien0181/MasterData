import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import ClusterWorkspace from './ClusterWorkspace';
import DecisionWorkspace from './DecisionWorkspace';
import { WorkflowContext } from './SessionWorkspace';
import type { WorkflowRead } from '@/lib/workflowContracts';
import * as api from '@/lib/workflowApi';
import { SessionCacheProvider } from './SessionCache';
vi.mock('next/navigation', () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock('./AppSession', () => ({ useAppUser: () => ({ capabilities: ['read','explain'] }) }));
vi.mock('@/lib/workflowApi', async orig => ({ ...await orig<typeof api>(),
  getClusters: vi.fn().mockResolvedValue({items:[{cluster_id:'c',member_count:1,singleton:true,status:'SINGLETON'}],total:1,next_cursor:null}),
  getPairSummary: vi.fn().mockResolvedValue({total:1,needs_review:1,accepted:0,denied:0,kept_separate:0,deferred:0,blocked:0,manual_available:1,auto_available:0,groups:[]}), getPairs: vi.fn().mockResolvedValue({items:[{pair_id:'p',revision:0,decision:'UNDECIDED',baseline:{left_id:'a',right_id:'b',decision:'REVIEW',field_evidence:[],score:1},eligibility:{manual_allowed:true,auto_allowed:false,blocked_reasons:[]}}],total:1,next_cursor:null}),
  explainWorkflowPair: vi.fn().mockResolvedValue({status:'TEMPLATE',error_code:'PROVIDER_DENIED',explanation:{summary_vi:'Fallback',limitations:[]}})
  ,getPair:vi.fn().mockResolvedValue({pair_id:'p',revision:0,decision:'UNDECIDED',baseline:{left_id:'a',right_id:'b',decision:'REVIEW',field_evidence:[],score:1},eligibility:{manual_allowed:true,auto_allowed:false,blocked_reasons:[]}})
}));
afterEach(cleanup);
const w: WorkflowRead = {run_id:'r',workflow_version:2,workflow_state:'GOLDEN_REVIEW',workflow_revision:4,decision_revision:2,cluster_revision:1,golden_revision:1,counts:{},allowed_actions:['confirm-golden','resolve-golden'],blockers:[],active_operation:null,retryable_operation:null};
function show(ui: React.ReactNode, state=w) { render(<SessionCacheProvider userId="test"><WorkflowContext.Provider value={{workflow:state,refresh:vi.fn()}}>{ui}</WorkflowContext.Provider></SessionCacheProvider>); }
it('offers group checkboxes and resolution in partial generation', async () => {
  show(<ClusterWorkspace />, {...w,workflow_state:'DECIDE_REVIEW'});
  await waitFor(()=>expect(screen.getByRole('checkbox',{name:'Chọn nhóm c'})).toBeInTheDocument());
  expect(screen.getByRole('button',{name:'Tự xử lý & tạo Golden'})).toBeEnabled();
});
it('explains fallback source without calling a template AI output', async () => {
  show(<DecisionWorkspace />, {...w,workflow_state:'DECIDE_REVIEW'});
  fireEvent.click(await screen.findByRole('button',{name:'Đối chiếu cặp p'}));
  fireEvent.click(await screen.findByRole('button',{name:'Giải thích bằng AI cặp p'}));
  await waitFor(() => expect(api.explainWorkflowPair).toHaveBeenCalled());
  expect(await screen.findByText('Giải thích theo quy tắc')).toBeInTheDocument();
  expect(screen.getByText('Fallback')).toBeInTheDocument();
});
it('reloads comparison evidence after reassessment even when decision revision is unchanged', async () => {
  const detail=(assessment:string,name:string)=>({pair_id:'p',revision:0,assessment_id:assessment,score_unit:'rule',decision:'UNDECIDED',baseline:{id:'p',left_id:'a',right_id:'b',decision:'REVIEW',score:1,field_evidence:[]},eligibility:{manual_allowed:true,auto_allowed:false,blocked_reasons:[]},left_record:{record_id:'a',source_system:'CRM',raw_fields:{name},normalized_fields:{},extra_fields:{}},right_record:{record_id:'b',source_system:'ERP',raw_fields:{name},normalized_fields:{},extra_fields:{}}});
  vi.mocked(api.getPair).mockResolvedValueOnce(detail('old','Old evidence')).mockResolvedValueOnce(detail('new','New evidence'));
  const host=(assessment:string)=><SessionCacheProvider userId="assessment-audit"><WorkflowContext.Provider value={{workflow:{...w,workflow_state:'DECIDE_REVIEW',assessment_id:assessment},refresh:vi.fn()}}><DecisionWorkspace/></WorkflowContext.Provider></SessionCacheProvider>;
  const view=render(host('old'));
  fireEvent.click(await screen.findByRole('button',{name:'Đối chiếu cặp p'}));
  expect(await screen.findAllByText('Old evidence')).toHaveLength(2);
  view.rerender(host('new'));
  expect(await screen.findAllByText('New evidence')).toHaveLength(2);
  expect(screen.queryByText('Old evidence')).not.toBeInTheDocument();
});
