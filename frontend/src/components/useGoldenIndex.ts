'use client';
import {useCallback} from 'react';
import {getGoldenIndex} from '@/lib/workflowApi';
import {tabReadiness} from '@/lib/workflowNavigation';
import {useWorkflow} from './SessionWorkspace';
import {useSessionRead} from './useSessionRead';

export function useGoldenIndex(status='all',q='',cursor:string|null=null,limit=25){
 const {workflow:w}=useWorkflow();
 const ready=tabReadiness(w.workflow_state,'golden',w.cluster_revision).ready;
 const load=useCallback((signal:AbortSignal)=>getGoldenIndex(w.run_id,new URLSearchParams({status,q,limit:String(limit),...(cursor?{cursor}:{})}),signal),[w.run_id,status,q,cursor,limit,w.cluster_revision,w.golden_revision,w.workflow_state,w.decision_revision]);
 const read=useSessionRead(w.run_id,['golden-index',w.cluster_revision,w.golden_revision,w.decision_revision,w.workflow_state],{status,q,cursor,limit},load,ready,true,Infinity);
 return {...read,ready};
}
