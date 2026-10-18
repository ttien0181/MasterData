'use client';
import { useSyncExternalStore } from 'react';
import { bulkDecisions, decidePair, getOperation, getOperationByRequestId, getPair, getWorkflow } from '@/lib/workflowApi';
import { PairReviewQueue, sessionReviewQueueStorage } from '@/lib/pairReviewQueue';
import type { PairRead } from '@/lib/workflowContracts';
import { sessionKey, useSessionCache, useStoredState } from './SessionCache';
import { useWorkflow } from './SessionWorkspace';
export function usePairReviewQueue(updatedPair:(pair:PairRead)=>void){
  const cache=useSessionCache(),{workflow:w,refresh}=useWorkflow();
  const [callbacks]=useStoredState(w.run_id,'pairs:review-queue-callbacks',()=>({updatedPair,refresh}));
  callbacks.updatedPair=updatedPair;callbacks.refresh=refresh;
  const slot='pairs:review-queue-controller',key=`${JSON.stringify(w.run_id)}:${slot}`;
  const [queue]=useStoredState<PairReviewQueue>(w.run_id,slot,()=>{
    const controller:PairReviewQueue=new PairReviewQueue({
    workflow:async()=>{const found=await getWorkflow(w.run_id);if(cache.ui.read(key,null)===controller)cache.client.setQueryData(sessionKey(cache.userId,w.run_id,'workflow'),(previous:typeof found|undefined)=>previous&&previous.workflow_revision>found.workflow_revision?previous:found);return found;},
    pair:(id,body)=>decidePair(w.run_id,id,body),bulk:body=>bulkDecisions(w.run_id,body),operation:id=>getOperation(w.run_id,id),
    receipt:id=>getOperationByRequestId(w.run_id,id),readPair:id=>getPair(w.run_id,id),
    onPair:pair=>{callbacks.updatedPair(pair);window.dispatchEvent(new CustomEvent('demo:completed',{detail:'pair-decision'}));},refresh:()=>callbacks.refresh(),
    alive:()=>cache.ui.read(key,null)===controller&&!cache.blocked[w.run_id],onError:error=>cache.handleError(error,w.run_id),
  },sessionReviewQueueStorage(cache.userId,w.run_id));return controller;});
  const items=useSyncExternalStore(queue.subscribe,queue.snapshot,queue.snapshot);
  return {queue,items,pending:items.filter(item=>['WAITING','SENDING','RUNNING'].includes(item.status)).length,
    attention:items.filter(item=>['FAILED','NEEDS_REVIEW'].includes(item.status)).length};
}
