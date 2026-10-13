import {cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import {afterEach,expect,it,vi} from 'vitest';
import PairCompletion from './PairCompletion';
import GenerationStatus from './GenerationStatus';
import PairAmendment from './PairAmendment';
import PairConflicts from './PairConflicts';
import {WorkflowContext} from './SessionWorkspace';
import {SessionCacheProvider} from './SessionCache';
import * as api from '@/lib/workflowApi';
import type {ClusterGeneration,PairRead,WorkflowRead} from '@/lib/workflowContracts';
vi.mock('@/lib/workflowApi',async original=>({...await original<typeof api>(),amendPair:vi.fn(),getConflicts:vi.fn(),getUnresolvedRecords:vi.fn()}));
afterEach(()=>{cleanup();vi.clearAllMocks();});
const w:WorkflowRead={run_id:'r',workflow_version:2,workflow_state:'DECIDE_REVIEW',workflow_revision:4,decision_revision:1,cluster_revision:0,golden_revision:0,counts:{},allowed_actions:['finalize-partial','amend-pair'],blockers:[{code:'PENDING_DECISIONS',count:3,unit:'pairs'}],active_operation:null,retryable_operation:null};
const pair:PairRead={pair_id:'p',revision:1,decision:'DENIED',score_unit:'rule',baseline:{id:'p',left_id:'a',right_id:'b',decision:'REVIEW',score:3,field_evidence:[]},eligibility:{manual_allowed:true,auto_allowed:false,blocked_reasons:[]}};
const host=(children:React.ReactNode)=><SessionCacheProvider userId="u"><WorkflowContext.Provider value={{workflow:w,refresh:vi.fn().mockResolvedValue(undefined)}}>{children}</WorkflowContext.Provider></SessionCacheProvider>;
it('lists blockers and queue attention while blocking both generation actions',()=>{
 render(<PairCompletion workflow={w} pending={2} attention={1} busy={false} required={vi.fn()} conflicts={vi.fn()} finalize={vi.fn()}/>);
 expect(screen.getByText(/2 yêu cầu đang chờ/)).toBeInTheDocument();expect(screen.getByText(/1 yêu cầu cần đối chiếu/)).toBeInTheDocument();
 expect(screen.getByRole('button',{name:'Tạo nhóm phần đã xử lý'})).toBeDisabled();expect(screen.getByRole('button',{name:'Hoàn tất phân loại và tạo nhóm'})).toBeDisabled();
});
it('offers explicitly partial generation when unresolved decisions remain',()=>{
 const finalize=vi.fn();render(<PairCompletion workflow={w} pending={0} attention={0} busy={false} required={vi.fn()} conflicts={vi.fn()} finalize={finalize}/>);
 fireEvent.click(screen.getByRole('button',{name:'Tạo nhóm phần đã xử lý'}));expect(finalize).toHaveBeenCalledWith(true);
});
it('shows a positive readiness explanation when full completion is allowed',()=>{
 render(<PairCompletion workflow={{...w,allowed_actions:['finalize-decisions'],blockers:[]}} pending={0} attention={0} busy={false} required={vi.fn()} conflicts={vi.fn()} finalize={vi.fn()}/>);
 expect(screen.getByText('Sẵn sàng tạo nhóm')).toBeInTheDocument();
});
it('displays partial coverage, unresolved counts, and stale readonly publication',()=>{
 render(<GenerationStatus generation={{revision:2,source_decision_revision:1,stale:true,read_only:true,publication_status:'PARTIAL',clusters:2,multi_record_groups:1,singletons:1,covered_records:3,unresolved_records:2,total_records:5}}/>);
 expect(screen.getByText(/3 \/ 5 bản ghi/)).toBeInTheDocument();expect(screen.getByText(/2 bản ghi chưa giải quyết/)).toBeInTheDocument();expect(screen.getByRole('status')).toHaveTextContent('chỉ đọc');
});
it('loads unresolved records only after opening and pages through bounded results',async()=>{
 const generation:ClusterGeneration={revision:2,source_decision_revision:1,stale:true,read_only:true,publication_status:'PARTIAL',clusters:2,multi_record_groups:1,singletons:1,covered_records:3,unresolved_records:50,total_records:53,unresolved_record_ids:['legacy-hidden-id']};
 vi.mocked(api.getUnresolvedRecords).mockImplementation(async(_run,query)=>({items:[{record_id:query?.get('cursor')?'b':'a',source_system:query?.get('cursor')?'BILL':'CRM',raw_fields:{name:query?.get('cursor')?'Second person':'First person'},normalized_fields:{},extra_fields:{}}],total:50,next_cursor:query?.get('cursor')?null:'25',generation}));
 render(host(<><GenerationStatus runId="r" generation={generation}/><PairCompletion workflow={w} pending={0} attention={1} busy={false} required={vi.fn()} conflicts={vi.fn()} finalize={vi.fn()}/></>));
 expect(api.getUnresolvedRecords).not.toHaveBeenCalled();expect(screen.queryByText('legacy-hidden-id')).not.toBeInTheDocument();
 fireEvent.click(screen.getByText('Bản ghi chưa giải quyết'));await screen.findByText('First person');
 expect(api.getUnresolvedRecords).toHaveBeenCalledWith('r',expect.any(URLSearchParams),expect.any(AbortSignal));expect(vi.mocked(api.getUnresolvedRecords).mock.calls[0][1]?.get('limit')).toBe('25');
 fireEvent.click(screen.getByRole('button',{name:'Trang tiếp'}));await screen.findByText('Second person');expect(vi.mocked(api.getUnresolvedRecords).mock.calls[1][1]?.get('cursor')).toBe('25');
 expect(screen.getByRole('button',{name:'Tạo nhóm phần đã xử lý'})).toBeDisabled();expect(screen.getByRole('button',{name:'Hoàn tất phân loại và tạo nhóm'})).toBeDisabled();
});
it('explains the approval permission blocker in Vietnamese',()=>{
 render(<PairCompletion workflow={{...w,allowed_actions:[],blockers:[{code:'APPROVE_PERMISSION_REQUIRED'}]}} pending={0} attention={0} busy={false} required={vi.fn()} conflicts={vi.fn()} finalize={vi.fn()}/>);
 expect(screen.getByText(/Tài khoản cần quyền phê duyệt/)).toBeInTheDocument();
});
it('requires amendment reason and preserves provenance failures without retry',async()=>{
 vi.mocked(api.amendPair).mockRejectedValue(new Error('DENY_SOURCE_UNKNOWN'));
 render(host(<PairAmendment pair={pair} close={vi.fn()} saved={vi.fn()}/>));
 const submit=screen.getByRole('button',{name:'Lưu sửa quyết định'});expect(submit).toBeDisabled();
 fireEvent.change(screen.getByRole('textbox',{name:'Lý do sửa'}),{target:{value:'Source checked'}});expect(submit).toBeDisabled();
 fireEvent.change(screen.getByRole('combobox',{name:'Quyết định mới'}),{target:{value:'no_merge'}});fireEvent.click(submit);
 await screen.findByText(/không xác định được nguồn/);expect(api.amendPair).toHaveBeenCalledTimes(1);
});
it('shows related records and offers pair hydration outside the current page',async()=>{
 vi.mocked(api.getConflicts).mockResolvedValue({items:[{conflict_id:'c',pair_id:'p',reason:'cannot_link',record_ids:['a','b'],records:[{record_id:'a',source_system:'CRM',raw_fields:{name:'Person'},normalized_fields:{},extra_fields:{}}],pairs:[pair],related_pair_count:1}],total:1,next_cursor:null});
 const compare=vi.fn();render(host(<PairConflicts close={vi.fn()} compare={compare} amend={vi.fn()}/>));
 await screen.findByText(/Person · CRM/);fireEvent.click(screen.getByRole('button',{name:'Đối chiếu cặp p'}));expect(compare).toHaveBeenCalledWith('p');
 await waitFor(()=>expect(api.getConflicts).toHaveBeenCalledTimes(1));
});
