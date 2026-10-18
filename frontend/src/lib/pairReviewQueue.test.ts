import { expect,it,vi } from 'vitest';
import { clearSessionReviewQueues, PairReviewQueue, sessionReviewQueueStorage } from './pairReviewQueue';
import { ApiError } from './api';
import type { OperationRead,WorkflowRead } from './workflowContracts';
const state:WorkflowRead={run_id:'r',workflow_version:2,workflow_state:'DECIDE_REVIEW',assessment_id:'v1',workflow_revision:5,decision_revision:3,cluster_revision:0,golden_revision:0,counts:{},blockers:[],active_operation:null,retryable_operation:null,allowed_actions:['decide-pair','bulk-decisions']};
function setup(){const services={workflow:vi.fn().mockResolvedValue(state),pair:vi.fn().mockResolvedValue({pair_id:'a',revision:1}),bulk:vi.fn(),operation:vi.fn(),onPair:vi.fn(),refresh:vi.fn().mockResolvedValue(undefined),alive:vi.fn().mockReturnValue(true),onError:vi.fn(),delay:vi.fn().mockResolvedValue(undefined)};return{q:new PairReviewQueue(services),services};}
const intent={key:'pair:a',label:'Accept a',assessmentId:'v1',decision:'merge' as const,pair:{id:'a',revision:0}};
const settle=async()=>{for(let i=0;i<30;i++)await Promise.resolve();};
it('waits for a background cluster refresh before submitting the next review',async()=>{
  const {q,services}=setup();
  services.workflow.mockResolvedValueOnce({...state,workflow_state:'CLUSTER_RUNNING',allowed_actions:['queue-decisions'],active_operation:{operation_id:'clusters',operation_type:'CLUSTER',status:'RUNNING'} as OperationRead}).mockResolvedValue(state);
  q.add(intent);await settle();
  expect(services.delay).toHaveBeenCalled();expect(services.pair).toHaveBeenCalledTimes(1);
  expect(q.snapshot()[0].status).toBe('DONE');expect(services.onError).not.toHaveBeenCalled();
});
it('retains reviewed group identifiers and evidence token in queued bulk writes',async()=>{
 const {q,services}=setup();services.bulk.mockResolvedValue({operation_id:'job',status:'COMPLETED'});
 q.add({key:'group-review',label:'Reviewed group',assessmentId:'v1',decision:'merge',scope:{pair_ids:['a']},
   reviewGroupRecordIds:['r1','r2'],reviewGroupToken:'source-bound-token',reason:'Documents checked'});
 await settle();expect(services.bulk).toHaveBeenCalledWith(expect.objectContaining({
   review_group_record_ids:['r1','r2'],review_group_token:'source-bound-token'}));
});
it('persists reviewed group context before dispatch so it survives reload',()=>{
 const saved=sessionReviewQueueStorage('group-test','run');
 saved.save([{...intent,id:'group',status:'WAITING',reviewGroupRecordIds:['r1','r2'],reviewGroupToken:'token'}]);
 expect(saved.load()[0]).toMatchObject({reviewGroupRecordIds:['r1','r2'],reviewGroupToken:'token'});saved.clear();
});
it('preserves approval reasons in queued single and bulk writes',async()=>{
 const {q,services}=setup();services.bulk.mockResolvedValue({operation_id:'job',status:'COMPLETED'});
 q.add({...intent,reason:'Checked source documents'});await settle();
 expect(services.pair).toHaveBeenCalledWith('a',expect.objectContaining({reason:'Checked source documents'}));
 q.add({key:'reason-bulk',label:'Bulk',assessmentId:'v1',decision:'merge',scope:{pair_ids:['b']},reason:'Verified records'});await settle();
 expect(services.bulk).toHaveBeenCalledWith(expect.objectContaining({reason:'Verified records'}));
});
it('does not rebase a queued decision across assessment changes',async()=>{const {q,services}=setup();services.workflow.mockResolvedValue({...state,assessment_id:'v2'});q.add(intent);await settle();expect(services.pair).not.toHaveBeenCalled();expect(q.snapshot()[0].status).toBe('FAILED');expect(q.snapshot()[0].error).toContain('đánh giá đã đổi');});
it('uses current workflow revision but retains the observed pair revision',async()=>{const {q,services}=setup();services.workflow.mockResolvedValue({...state,workflow_revision:9,decision_revision:7});q.add(intent);await settle();expect(services.pair).toHaveBeenCalledWith('a',expect.objectContaining({expected_workflow_revision:9,expected_assessment_id:'v1',expected_pair_revision:0}));expect(q.snapshot()[0].status).toBe('DONE');});
it('cancels unsent work and never dispatches it after an earlier bulk job',async()=>{const {q,services}=setup();let resume!:()=>void;services.workflow.mockResolvedValue({...state,active_operation:{operation_type:'BULK_DECISIONS'} as OperationRead});services.delay.mockImplementationOnce(()=>new Promise<void>(resolve=>{resume=resolve;}));q.add(intent);await settle();q.cancel(q.snapshot()[0].id);services.workflow.mockResolvedValue(state);resume();await settle();expect(services.pair).not.toHaveBeenCalled();expect(q.snapshot()[0].status).toBe('CANCELLED');});
it('prevents duplicate clicks and serializes rapid writes',async()=>{const {q,services}=setup();let finish!:(value:{pair_id:string;revision:number})=>void;services.pair.mockImplementationOnce(()=>new Promise(resolve=>{finish=resolve;}));q.add(intent);expect(q.add(intent)).toBe(false);q.add({...intent,key:'pair:b',pair:{id:'b',revision:0}});await settle();expect(services.pair).toHaveBeenCalledTimes(1);finish({pair_id:'a',revision:1});await settle();expect(services.pair).toHaveBeenCalledTimes(2);});
it('preserves acknowledged writes when the follow-up refresh fails',async()=>{const {q,services}=setup();services.refresh.mockRejectedValue(new Error('read unavailable'));q.add(intent);await settle();expect(q.snapshot()[0].status).toBe('DONE');expect(services.pair).toHaveBeenCalledTimes(1);});
it('stops dispatching when logout clears the session',async()=>{const {q,services}=setup();services.alive.mockReturnValue(false);q.add(intent);await settle();expect(services.workflow).not.toHaveBeenCalled();expect(services.pair).not.toHaveBeenCalled();expect(q.snapshot()[0].status).toBe('CANCELLED');});
it('waits for bulk completion before sending the next single decision',async()=>{const {q,services}=setup();services.bulk.mockResolvedValue({operation_id:'job',status:'QUEUED'});services.operation.mockResolvedValue({operation_id:'job',status:'COMPLETED'});q.add({key:'scope:A',label:'Group A',assessmentId:'v1',decision:'merge',scope:{filter:{group:'A'}}});q.add(intent);await settle();expect(services.operation).toHaveBeenCalledWith('job');expect(services.pair).toHaveBeenCalledTimes(1);expect(services.bulk.mock.invocationCallOrder[0]).toBeLessThan(services.pair.mock.invocationCallOrder[0]);});

function storage(){let saved='';return {load:()=>saved?JSON.parse(saved):[],save:(items:unknown)=>{saved=JSON.stringify(items);},clear:()=>{saved='';}};}
it('restores unsent work for explicit confirmation without sending it',async()=>{
  const saved=storage();const {services}=setup();services.workflow.mockImplementation(()=>new Promise(()=>{}));
  const first=new PairReviewQueue(services,saved);first.add(intent);
  const restoredServices=setup().services;const restored=new PairReviewQueue({...restoredServices,readPair:async()=>({revision:0})},saved);
  await settle();expect(restored.snapshot()[0].status).toBe('NEEDS_REVIEW');expect(restoredServices.pair).not.toHaveBeenCalled();
  await restored.resume(restored.snapshot()[0].id);await settle();expect(restored.snapshot()[0].status).toBe('DONE');
});
it('rejects confirmed unsent work when its observed pair revision changed',async()=>{
  const saved=storage();saved.save([{...intent,id:'restored',status:'WAITING'}]);const {services}=setup();
  const q=new PairReviewQueue({...services,readPair:async()=>({revision:1})},saved);
  await q.resume('restored');await settle();expect(q.snapshot()[0].status).toBe('FAILED');expect(services.pair).not.toHaveBeenCalled();
});
it('retains exact sent request after an uncertain write and forbids resubmission',async()=>{
  const saved=storage();const {services}=setup();services.pair.mockRejectedValue(new ApiError('conflict',409));
  const q=new PairReviewQueue({...services,receipt:async()=>{throw new Error('offline');}},saved);q.add(intent);await settle();
  const item=q.snapshot()[0];expect(item.status).toBe('NEEDS_REVIEW');expect(item.error).not.toContain('chưa áp dụng');
  expect(saved.load()[0].request).toEqual({request_id:item.id,expected_workflow_revision:5,expected_assessment_id:'v1',expected_pair_revision:0,decision:'merge'});
  await q.resume(item.id);expect(services.pair).toHaveBeenCalledTimes(1);
});
it('reconciles a restored sent request receipt without duplicating the write',async()=>{
  const saved=storage();saved.save([{...intent,id:'sent',status:'SENDING',request:{request_id:'sent',expected_workflow_revision:5,expected_assessment_id:'v1',expected_pair_revision:0,decision:'merge'}}]);
  const {services}=setup();const pair={pair_id:'a',revision:1};
  const q=new PairReviewQueue({...services,receipt:async()=>({operation_id:'receipt',operation_type:'PAIR_DECISION',status:'COMPLETED',result:pair} as unknown as OperationRead)},saved);
  await settle();expect(q.snapshot()[0].status).toBe('DONE');expect(services.onPair).toHaveBeenCalledWith(pair);expect(services.pair).not.toHaveBeenCalled();
});
it('permits explicit resume after the server confirms no receipt using the exact original body',async()=>{
  const saved=storage();const body={request_id:'sent',expected_workflow_revision:5,expected_assessment_id:'v1',expected_pair_revision:0,decision:'merge'};
  saved.save([{...intent,id:'sent',status:'SENDING',request:body}]);const {services}=setup();services.workflow.mockResolvedValue({...state,workflow_revision:99});
  const q=new PairReviewQueue({...services,receipt:async()=>null,readPair:async()=>({revision:0})},saved);
  await settle();expect(q.snapshot()[0].status).toBe('NEEDS_REVIEW');expect(services.pair).not.toHaveBeenCalled();
  await q.resume('sent');await settle();expect(services.pair).toHaveBeenCalledWith('a',body);
});
it('clears persisted intents and prevents late response callbacks on logout',async()=>{
  const saved=storage();const {services}=setup();let finish!:(pair:unknown)=>void;services.pair.mockImplementation(()=>new Promise(resolve=>{finish=resolve;}));
  const q=new PairReviewQueue(services,saved);q.add(intent);await settle();services.alive.mockReturnValue(false);finish({pair_id:'a',revision:1});await settle();
  expect(saved.load()).toEqual([]);expect(services.onPair).not.toHaveBeenCalled();expect(q.snapshot()[0].status).toBe('CANCELLED');
});
it('isolates same-tab persistence by user and run and removes completed work',()=>{
  const a=sessionReviewQueueStorage('u1','r1'),b=sessionReviewQueueStorage('u2','r1'),c=sessionReviewQueueStorage('u1','r2');
  a.save([{...intent,id:'i',status:'WAITING'}]);expect(b.load()).toEqual([]);expect(c.load()).toEqual([]);expect(a.load()).toHaveLength(1);a.clear();expect(a.load()).toEqual([]);
});
it('does not erase another controller persistence when an unused cached initializer is constructed',async()=>{
  const saved=storage();saved.save([{...intent,id:'pending',status:'WAITING'}]);const {services}=setup();services.alive.mockReturnValue(false);
  new PairReviewQueue(services,saved);await settle();expect(saved.load()).toHaveLength(1);
});
it('keeps failed work until explicitly dismissed instead of discarding it when another intent is added',async()=>{
  const {q,services}=setup();services.workflow.mockResolvedValue({...state,assessment_id:'changed'});q.add(intent);await settle();
  q.add({...intent,key:'pair:b',pair:{id:'b',revision:0}});await settle();expect(q.snapshot().filter(item=>item.status==='FAILED')).toHaveLength(2);
  q.dismiss(q.snapshot()[0].id);expect(q.snapshot()).toHaveLength(1);
});
it('does not rebase a bulk preview across decision revision changes',async()=>{
  const {q,services}=setup();q.add({key:'bulk',label:'Bulk',assessmentId:'v1',decision:'merge',scope:{pair_ids:['a']},expectedDecisionRevision:2});
  await settle();expect(q.snapshot()[0].status).toBe('FAILED');expect(services.bulk).not.toHaveBeenCalled();
});
it('clears all saved runs for a logged out user without deleting another user queue',()=>{
  const a=sessionReviewQueueStorage('logout-user','r1'),b=sessionReviewQueueStorage('logout-user','r2'),other=sessionReviewQueueStorage('other-user','r1');
  for(const saved of [a,b,other])saved.save([{...intent,id:'i',status:'WAITING'}]);
  clearSessionReviewQueues('logout-user');expect(a.load()).toEqual([]);expect(b.load()).toEqual([]);expect(other.load()).toHaveLength(1);other.clear();
});
it('does not send an intent when storage cannot preserve it for reload',async()=>{
  const {services}=setup();const q=new PairReviewQueue(services,{load:()=>[],save:()=>{throw new Error('quota');},clear:()=>{}});
  expect(()=>q.add(intent)).not.toThrow();await settle();expect(q.snapshot()[0].status).toBe('FAILED');expect(services.pair).not.toHaveBeenCalled();
});
it('does not send a request when storing the exact body fails',async()=>{
  const {services}=setup();let writes=0;const q=new PairReviewQueue(services,{load:()=>[],save:()=>{if(++writes>1)throw new Error('quota');},clear:()=>{}});
  q.add(intent);await settle();expect(q.snapshot()[0].status).toBe('FAILED');expect(services.pair).not.toHaveBeenCalled();
});
it('retains the confirmed bulk preview token in the exact submitted request',async()=>{
  const {q,services}=setup();services.bulk.mockResolvedValue({operation_id:'job',status:'COMPLETED'});
  q.add({key:'bulk-token',label:'Bulk',assessmentId:'v1',decision:'merge',scope:{pair_ids:['a']},expectedDecisionRevision:3,previewToken:'confirmed-preview'});
  await settle();expect(services.bulk).toHaveBeenCalledWith(expect.objectContaining({preview_token:'confirmed-preview',expected_decision_revision:3}));
});
it('keeps an acknowledged write complete even when refreshing throws synchronously',async()=>{
  const {q,services}=setup();services.refresh.mockImplementation(()=>{throw new Error('refresh callback unavailable');});q.add(intent);
  await settle();expect(q.snapshot()[0].status).toBe('DONE');expect(services.pair).toHaveBeenCalledTimes(1);
});
it('uses a known operation to reconcile a restored running bulk even when request lookup returns no receipt',async()=>{
  const saved=storage();saved.save([{id:'bulk-running',key:'bulk',label:'Bulk',assessmentId:'v1',decision:'merge',scope:{pair_ids:['a']},status:'RUNNING',operationId:'known-job',
    request:{request_id:'bulk-running',expected_workflow_revision:5,expected_assessment_id:'v1',expected_decision_revision:3,decision:'merge',scope:{pair_ids:['a']}}}]);
  const {services}=setup();services.operation.mockResolvedValue({operation_id:'known-job',status:'COMPLETED'});
  const q=new PairReviewQueue({...services,receipt:async()=>null},saved);await settle();
  expect(q.snapshot()[0].status).toBe('DONE');expect(services.operation).toHaveBeenCalledWith('known-job');expect(services.bulk).not.toHaveBeenCalled();
});
it('checks logout again after publishing the sending state and before dispatching',async()=>{
  const {q,services}=setup();q.subscribe(()=>{if(q.snapshot().some(item=>item.status==='SENDING'))services.alive.mockReturnValue(false);});
  q.add(intent);await settle();expect(services.pair).not.toHaveBeenCalled();expect(q.snapshot()[0].status).toBe('CANCELLED');
});
it('preserves sent and waiting requests when the provider unmounts and ignores late responses',async()=>{
  const saved=storage();const {services}=setup();let finish!:(pair:unknown)=>void;
  services.pair.mockImplementation(()=>new Promise(resolve=>{finish=resolve;}));
  const q=new PairReviewQueue(services,saved);q.add(intent);q.add({...intent,key:'pair:b',pair:{id:'b',revision:0}});await settle();
  const before=saved.load();expect(before.map((item:{status:string})=>item.status)).toEqual(['SENDING','WAITING']);
  q.dispose(false);finish({pair_id:'a',revision:1});await settle();
  expect(saved.load()).toEqual(before);expect(services.onPair).not.toHaveBeenCalled();expect(services.pair).toHaveBeenCalledTimes(1);
  const restored=new PairReviewQueue({...setup().services,receipt:async()=>null},saved);await settle();
  expect(restored.snapshot().map(item=>item.status)).toEqual(['NEEDS_REVIEW','NEEDS_REVIEW']);
});
it('clears preserved requests when an already stopped controller is explicitly discarded',async()=>{
  const saved=storage();const {services}=setup();services.workflow.mockImplementation(()=>new Promise(()=>{}));
  const q=new PairReviewQueue(services,saved);q.add(intent);q.dispose(false);expect(saved.load()).toHaveLength(1);
  q.dispose();expect(saved.load()).toEqual([]);
});
it('waits while the backend offers queue-decisions during an active bulk before dispatching a collected pair',async()=>{
  const {q,services}=setup();let continuePolling!:()=>void;
  services.workflow.mockResolvedValue({...state,allowed_actions:['queue-decisions'],active_operation:{operation_id:'bulk',operation_type:'BULK_DECISIONS',status:'RUNNING'} as OperationRead});
  services.delay.mockImplementationOnce(()=>new Promise<void>(resolve=>{continuePolling=resolve;}));
  q.add(intent);await settle();expect(q.snapshot()[0].status).toBe('WAITING');expect(services.pair).not.toHaveBeenCalled();expect(services.onError).not.toHaveBeenCalled();
  services.workflow.mockResolvedValue(state);continuePolling();await settle();expect(q.snapshot()[0].status).toBe('DONE');expect(services.pair).toHaveBeenCalledTimes(1);
});
it('accepts explicit confirmation of a restored pair during active bulk without claiming permission was lost',async()=>{
  const saved=storage();saved.save([{...intent,id:'restored-bulk',status:'WAITING'}]);const {services}=setup();let continuePolling!:()=>void;
  services.workflow.mockResolvedValue({...state,allowed_actions:['queue-decisions'],active_operation:{operation_id:'bulk',operation_type:'BULK_DECISIONS',status:'RUNNING'} as OperationRead});
  services.delay.mockImplementationOnce(()=>new Promise<void>(resolve=>{continuePolling=resolve;}));
  const q=new PairReviewQueue({...services,readPair:async()=>({revision:0})},saved);await settle();
  expect(await q.resume('restored-bulk')).toBe(true);await settle();expect(q.snapshot()[0].status).toBe('WAITING');expect(services.pair).not.toHaveBeenCalled();
  services.workflow.mockResolvedValue(state);continuePolling();await settle();expect(q.snapshot()[0].status).toBe('DONE');expect(services.pair).toHaveBeenCalledTimes(1);
});
it('rejects assessment drift immediately even when a bulk job is active',async()=>{
  const {q,services}=setup();services.workflow.mockResolvedValue({...state,assessment_id:'v2',allowed_actions:['queue-decisions'],active_operation:{operation_type:'BULK_DECISIONS',status:'RUNNING'} as OperationRead});
  q.add(intent);await settle();expect(q.snapshot()[0].status).toBe('FAILED');expect(services.delay).not.toHaveBeenCalled();expect(services.pair).not.toHaveBeenCalled();
});
