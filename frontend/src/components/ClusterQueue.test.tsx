import {cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import {afterEach,expect,it,vi} from 'vitest';
import ClusterQueue from './ClusterQueue';
import {WorkflowContext} from './SessionWorkspace';
import {SessionCacheProvider} from './SessionCache';
import * as api from '@/lib/workflowApi';
import type {WorkflowRead} from '@/lib/workflowContracts';
vi.mock('@/lib/workflowApi',async original=>({...await original<typeof api>(),getClusterQueue:vi.fn(),buildClusterQueue:vi.fn()}));
afterEach(()=>{cleanup();vi.clearAllMocks();});
it('reads queue on opening without building; explicit button submits pending scope despite unreviewed pairs',async()=>{
 vi.mocked(api.getClusterQueue).mockResolvedValue({pending_count:2,accepted_count:2,published_count:0,decision_revision:3,cluster_revision:0});
 vi.mocked(api.buildClusterQueue).mockResolvedValue({operation_id:'job',run_id:'r',operation_type:'CLUSTER',status:'COMPLETED',stage:'completed',progress:{},error_code:null,result:{}});
 const workflow={run_id:'r',workflow_version:2,workflow_state:'DECIDE_REVIEW',workflow_revision:4,decision_revision:3,cluster_revision:0,golden_revision:0,counts:{},allowed_actions:['build-cluster-queue'],blockers:[{code:'PENDING_DECISIONS',count:100000}],active_operation:null,retryable_operation:null} as WorkflowRead;
 render(<SessionCacheProvider userId="u"><WorkflowContext.Provider value={{workflow,refresh:vi.fn()}}><ClusterQueue/></WorkflowContext.Provider></SessionCacheProvider>);
 const button=await screen.findByRole('button',{name:'Tạo nhóm mới'});
 await waitFor(()=>expect(button).toBeEnabled());expect(api.buildClusterQueue).not.toHaveBeenCalled();
 fireEvent.click(button);
 await waitFor(()=>expect(api.buildClusterQueue).toHaveBeenCalledWith('r',expect.objectContaining({queue_mode:'pending',expected_decision_revision:3})));
});
