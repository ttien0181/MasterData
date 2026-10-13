import { expect, it } from 'vitest';
import { PairReviewQueue, sessionReviewQueueStorage } from '@/lib/pairReviewQueue';
import type { WorkflowRead } from '@/lib/workflowContracts';
import { SessionUIStore } from './SessionCache';

const workflow: WorkflowRead = {run_id:'r',workflow_version:2,workflow_state:'DECIDE_REVIEW',
  workflow_revision:1,decision_revision:1,cluster_revision:0,golden_revision:0,counts:{},
  allowed_actions:['decide-pair'],blockers:[],active_operation:null,retryable_operation:null};
function registered() {
  const ui = new SessionUIStore(), storage = sessionReviewQueueStorage('lifecycle-user','r');
  storage.clear();
  const queue = new PairReviewQueue({workflow:()=>new Promise(()=>undefined),pair:()=>new Promise(()=>undefined),
    bulk:()=>new Promise(()=>undefined),operation:()=>new Promise(()=>undefined),onPair:()=>undefined,
    refresh:async()=>undefined,alive:()=>true,onError:()=>undefined},storage);
  ui.write('"r":pairs:review-queue-controller',queue);
  queue.add({key:'pair:p',label:'Pair p',assessmentId:null,decision:'defer',pair:{id:'p',revision:0}});
  return {ui,queue,storage};
}
it('clearing authenticated UI disposes queued work and removes saved intentions',()=>{
  const {ui,queue,storage}=registered();
  expect(storage.load()).toHaveLength(1);
  ui.clear();
  expect(storage.load()).toHaveLength(0);
  expect(queue.snapshot()[0].status).toBe('CANCELLED');
});
it('ordinary cache unmount preserves saved work for explicit review after reopening',()=>{
  const {ui,queue,storage}=registered();
  ui.clear(false);
  expect(storage.load()).toHaveLength(1);
  expect(queue.add({key:'pair:other',label:'Other',assessmentId:null,decision:'defer',pair:{id:'other',revision:0}})).toBe(false);
  storage.clear();
});
