import {cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import {afterEach,expect,it,vi} from 'vitest';
import {SessionCacheProvider} from './SessionCache';
import {WorkflowContext} from './SessionWorkspace';
import PairClusterReview from './PairClusterReview';
import {getReviewCluster} from '@/lib/workflowApi';
import type {ReviewCluster,WorkflowRead} from '@/lib/workflowContracts';
vi.mock('@/lib/workflowApi',async original=>({...await original<typeof import('@/lib/workflowApi')>(),getReviewCluster:vi.fn()}));
const w:WorkflowRead={run_id:'r',workflow_version:2,workflow_state:'DECIDE_REVIEW',workflow_revision:1,decision_revision:2,cluster_revision:0,golden_revision:0,assessment_id:null,counts:{},blockers:[],allowed_actions:['bulk-decisions'],active_operation:null,retryable_operation:null};
const data:ReviewCluster={seed_pair_id:'ab',complete:true,batch_allowed:true,blocked_reasons:[],pending_pair_ids:['ab','ac','bc'],decision_revision:2,assessment_id:null,
 records:['a','b','c'].map(id=>({record_id:id,external_id:id,source_system:'CRM',raw_fields:{name:`Person ${id}`,phone:'0901234567'},normalized_fields:{},extra_fields:{}})),
 pairs:['ab','ac','bc'].map(id=>({pair_id:id,revision:0,decision:'UNDECIDED',score_unit:'rule',baseline:{id,left_id:id[0],right_id:id[1],decision:'REVIEW',score:6,field_evidence:[{field:'phone',state:'EX',similarity:1}]},eligibility:{manual_allowed:true,auto_allowed:false,blocked_reasons:[]}}))};
afterEach(()=>{cleanup();vi.clearAllMocks();});
it('opens the sealed large-group preview when ordinary batch review is too large',async()=>{
 vi.mocked(getReviewCluster).mockResolvedValue({...data,complete:false,batch_allowed:false,blocked_reasons:['CLUSTER_REVIEW_TOO_LARGE']});
 const reviewLarge=vi.fn();
 render(<SessionCacheProvider userId="large-route"><WorkflowContext.Provider value={{workflow:w,refresh:vi.fn()}}><PairClusterReview pairId="ab" canWrite close={vi.fn()} accept={vi.fn()} reviewLargeGroup={reviewLarge}/></WorkflowContext.Provider></SessionCacheProvider>);
 fireEvent.click(await screen.findByRole('button',{name:'Xem trước để duyệt nhóm 21–100 thành viên'}));
 expect(reviewLarge).toHaveBeenCalledOnce();
});
it('allows explicit review of one edge when the whole region exceeds the normal limit',async()=>{
 vi.mocked(getReviewCluster).mockResolvedValue({...data,complete:false,batch_allowed:false,
  blocked_reasons:['CLUSTER_REVIEW_TOO_LARGE']});
 const accept=vi.fn(),acceptPair=vi.fn();
 render(<SessionCacheProvider userId="large-single-edge"><WorkflowContext.Provider value={{workflow:w,refresh:vi.fn()}}><PairClusterReview pairId="ab" canWrite close={vi.fn()} accept={accept} acceptPair={acceptPair}/></WorkflowContext.Provider></SessionCacheProvider>);
 fireEvent.click(await screen.findByRole('button',{name:'Accept riêng cặp ab'}));
 expect(acceptPair).toHaveBeenCalledWith(expect.objectContaining({pair_id:'ab',revision:0}));
 expect(accept).not.toHaveBeenCalled();
});
function show(value:ReviewCluster=data,current:WorkflowRead=w){vi.mocked(getReviewCluster).mockResolvedValue(value);const accept=vi.fn();render(<SessionCacheProvider userId="cluster-task"><WorkflowContext.Provider value={{workflow:current,refresh:vi.fn()}}><PairClusterReview pairId="ab" canWrite close={vi.fn()} accept={accept}/></WorkflowContext.Provider></SessionCacheProvider>);return accept;}
it('shows each source once and passes only exact pending IDs to existing confirmation',async()=>{
 const accept=show();await screen.findByText('Person a');expect(screen.getAllByText('Person a')).toHaveLength(1);
 expect(screen.getByRole('table',{name:'Thành viên cụm'})).toBeVisible();
 fireEvent.click(screen.getByRole('button',{name:'Accept 3 cặp trong cụm'}));expect(accept).toHaveBeenCalledWith(data);
});
it('does not offer batch acceptance for an incomplete component',async()=>{
 show({...data,complete:false,batch_allowed:false,blocked_reasons:['CLUSTER_REVIEW_TOO_LARGE']});
 await screen.findByText('Person a');expect(screen.getByRole('button',{name:'Accept 3 cặp trong cụm'})).toBeDisabled();
 expect(screen.getByText(/Cụm vượt giới hạn/)).toBeVisible();
});
it('fences stale decision and assessment identities',async()=>{
 show(data,{...w,decision_revision:3,assessment_id:'new'});await screen.findByText('Person a');
 await waitFor(()=>expect(screen.getByRole('button',{name:'Accept 3 cặp trong cụm'})).toBeDisabled());
 expect(screen.getByRole('alert')).toHaveTextContent('đã thay đổi');
});

it('identifies same-name conflicting edges by source ID and opens full evidence',async()=>{
 const sameNames={...data,records:data.records.map((r,i)=>({...r,source_system:i?'BILL':'CRM',raw_fields:{name:'Same Name',dob:i?'1981-01-01':'1980-01-01'}})),pairs:data.pairs.map((p,i)=>i?p:{...p,baseline:{...p.baseline,primary_group:'D',field_evidence:[{field:'dob',state:'CF',similarity:0}]},eligibility:{...p.eligibility,reason_required:true}})};
 show(sameNames);await screen.findByRole('table',{name:'Thành viên cụm'});
 const evidence=screen.getByText('Bằng chứng 3 cặp').closest('details')!;
 expect(evidence).toHaveAttribute('open');expect(evidence).toHaveTextContent('CRM · a');expect(evidence).toHaveTextContent('BILL · b');
 fireEvent.click(screen.getByRole('button',{name:'Đối chiếu đầy đủ cặp ab'}));
 expect(await screen.findByText('1980-01-01')).toBeVisible();expect(screen.getByText('1981-01-01')).toBeVisible();
 expect(screen.getByText('Mâu thuẫn',{exact:true})).toBeVisible();
});

it('shows recomputed absent-edge evidence without adding it to the acceptance scope',async()=>{
 const observed={...data.pairs[0],pair_id:'observed-only'};
 const accept=show({...data,additional_evidence:[observed]});await screen.findByText('Person a');
 expect(screen.getByText('Bằng chứng bổ sung đã tính từ bản ghi nguồn')).toBeVisible();
 fireEvent.click(screen.getByRole('button',{name:'Accept 3 cặp trong cụm'}));
 expect(accept.mock.calls[0][0].pending_pair_ids).toEqual(['ab','ac','bc']);
});
