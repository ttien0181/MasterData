import {cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import {afterEach,expect,it,vi} from 'vitest';
import GoldenOverview from './GoldenOverview';
import GoldenExport from './GoldenExport';
import {WorkflowContext} from './SessionWorkspace';
import {SessionCacheProvider} from './SessionCache';
import type {WorkflowRead} from '@/lib/workflowContracts';
import * as api from '@/lib/workflowApi';
import * as transport from '@/lib/api';
vi.mock('next/navigation',()=>({useRouter:()=>({replace:vi.fn()})}));
vi.mock('@/lib/workflowApi',async original=>({...await original<typeof api>(),getGoldenIndex:vi.fn(),prepareWorkflowExport:vi.fn()}));
vi.mock('@/lib/api',async original=>({...await original<typeof transport>(),getResolutionExports:vi.fn(),executeResolutionPreview:vi.fn()}));
afterEach(()=>{cleanup();vi.clearAllMocks();});
const generation={revision:1,source_decision_revision:1,stale:false,read_only:false,publication_status:'COMPLETE' as const,clusters:10,multi_record_groups:2,singletons:8,covered_records:12,unresolved_records:0,total_records:12};
const w:WorkflowRead={run_id:'r',workflow_version:2,workflow_state:'GOLDEN_REVIEW',workflow_revision:3,decision_revision:1,cluster_revision:1,golden_revision:1,cluster_result:generation,counts:{},allowed_actions:[],blockers:[],active_operation:null,retryable_operation:null};
function show(ui:React.ReactNode,state=w){
 vi.mocked(api.getGoldenIndex).mockResolvedValue({items:[],total:10,next_cursor:null,summary:{total:10,confirmed:7,needs_review:3},generation});
 vi.mocked(transport.getResolutionExports).mockResolvedValue({items:[]});
 return render(<SessionCacheProvider userId="golden-pages"><WorkflowContext.Provider value={{workflow:state,refresh:vi.fn().mockResolvedValue(undefined)}}>{ui}</WorkflowContext.Provider></SessionCacheProvider>);
}
it('shows progress with a direct path to required reviews and keeps export actions on their own page',async()=>{
 show(<GoldenOverview/>);
 expect(await screen.findByRole('progressbar',{name:'Golden đã xác nhận'})).toHaveAttribute('value','7');
 expect(screen.getByRole('link',{name:/Tiếp tục duyệt/})).toHaveAttribute('href','/app/sessions/r/golden/records?status=review');
 expect(screen.queryByRole('button',{name:'Chốt phiên'})).not.toBeInTheDocument();
});
it('does not label a finalized workflow as a successful CSV export and allows retry after failure',async()=>{
 vi.mocked(api.prepareWorkflowExport).mockResolvedValue({preview_id:'p',estimated_output_rows:10,result_hash:'h'} as Awaited<ReturnType<typeof api.prepareWorkflowExport>>);
 vi.mocked(transport.executeResolutionPreview).mockRejectedValue(new Error('Server disconnected'));
 show(<GoldenExport/>,{...w,workflow_state:'FINALIZED',allowed_actions:['export']});
 const preview=await screen.findByRole('button',{name:'Xem trước CSV'});
 await waitFor(()=>expect(preview).toBeEnabled());
 fireEvent.click(preview);
 fireEvent.click(await screen.findByRole('button',{name:'Xuất CSV'}));
 await waitFor(()=>expect(screen.getByRole('alert')).toHaveTextContent('Server disconnected'));
 expect(screen.getByText('Xuất CSV thất bại')).toBeVisible();
 expect(screen.queryByRole('button',{name:'Tải CSV'})).not.toBeInTheDocument();
 expect(screen.getByRole('button',{name:'Thử xuất lại'})).toBeEnabled();
});
