'use client';
import { useEffect, useRef, useState } from 'react';
import { ApiError } from '@/lib/api';
import { explainWorkflowPair, mutation, type ExplanationRead } from '@/lib/workflowApi';
import type { PairRead } from '@/lib/workflowContracts';
import { fieldLabels } from '@/lib/pairReview';
import { useAppUser } from './AppSession';
import { useWorkflow } from './SessionWorkspace';
import { useSessionCache, useStoredState } from './SessionCache';
import styles from './DecisionWorkspace.module.css';

export default function PairExplanation({pair}: {pair:PairRead}) {
  const {workflow:w,refresh}=useWorkflow(),user=useAppUser(),cache=useSessionCache();
  const identity=JSON.stringify([pair.pair_id,pair.revision,w.assessment_id,w.decision_revision,w.workflow_revision]);
  const [result,setResult]=useStoredState<ExplanationRead|null>(w.run_id,`pair-explanation:${identity}`,null);
  const [busy,setBusy]=useState(false),[error,setError]=useState('');
  const pending=useRef<AbortController|null>(null);
  useEffect(()=>()=>pending.current?.abort(),[]);
  if(!user.capabilities.includes('explain')||cache.blocked[w.run_id]) return null;
  const stale=result?.status==='STALE';
  async function explain() {
    if(pending.current) return;
    const controller=new AbortController();pending.current=controller;setBusy(true);setError('');
    try {
      const value=await explainWorkflowPair(w.run_id,pair.pair_id,mutation(w),controller.signal);
      if(controller.signal.aborted) return;
      setResult(value);
      if(value.status==='STALE') await refresh();
    } catch(cause) {
      if(controller.signal.aborted) return;
      cache.handleError(cause,w.run_id);
      if(cause instanceof ApiError&&cause.status===409) {
        setResult(null);setError('Phiên đã thay đổi. Mở lại đối chiếu để giải thích.');await refresh().catch(()=>undefined);
      } else setError('Chưa tải được giải thích. Hãy thử lại.');
    } finally {
      if(!controller.signal.aborted) {pending.current=null;setBusy(false);}
    }
  }
  const explanation=stale?null:result?.explanation;
  const ready=result?.status==='READY'&&!!explanation;
  return <section className={styles.explanationPanel} aria-label="Giải thích cặp">
    <button aria-label={`Giải thích bằng AI cặp ${pair.pair_id}`} disabled={busy||ready||!!w.active_operation||w.blockers.some(b=>b.code==='RUN_ARCHIVED')} onClick={()=>void explain()}>{busy?'Đang giải thích…':ready?'Đã có giải thích AI':result?'Thử lại AI':'Giải thích bằng AI'}</button>
    {busy&&<p role="status">Đang tạo giải thích…</p>}
    {(error||stale)&&<p role="alert">{error||'Dữ liệu đã thay đổi. Mở lại đối chiếu để giải thích.'}</p>}
    {explanation&&<div role="status"><h3>{ready?'Giải thích AI':'Giải thích theo quy tắc'}</h3>{!ready&&<p>AI chưa có kết quả hợp lệ.</p>}<p>{explanation.summary_vi}</p>
      {!!explanation.attention_fields?.length&&<p>Cần đối chiếu: {explanation.attention_fields.map(f=>fieldLabels[f]||f).join(', ')}</p>}
      {!!explanation.limitations?.length&&<details><summary>Giới hạn</summary>{explanation.limitations.map((text,i)=><p key={i}>{text}</p>)}</details>}
    </div>}
  </section>;
}
