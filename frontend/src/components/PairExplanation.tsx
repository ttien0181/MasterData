'use client';
import { useEffect, useRef, useState } from 'react';
import {createPortal} from 'react-dom';
import MasterBotText,{HighlightedText} from './MasterBotText';
import {useMasterBot} from './MasterBotProvider';
import { ApiError } from '@/lib/api';
import { streamWorkflowPairExplanation, mutation, type ExplanationRead } from '@/lib/workflowApi';
import { isExplanationResult } from '@/lib/explanationStream';
import type { PairRead } from '@/lib/workflowContracts';
import {masterBotReminder} from '@/lib/masterBotExplanation';
import { useAppUser } from './AppSession';
import { useWorkflow } from './SessionWorkspace';
import { useSessionCache, useStoredState } from './SessionCache';
import styles from './DecisionWorkspace.module.css';

export default function PairExplanation({pair,autoStart=false,requestKey=0}: {pair:PairRead;autoStart?:boolean;requestKey?:number}) {
  const {workflow:w}=useWorkflow(),user=useAppUser(),cache=useSessionCache();
  const identity=JSON.stringify([w.run_id,pair.pair_id,pair.revision,w.assessment_id,w.decision_revision,w.workflow_revision]);
  if(!user.capabilities.includes('explain')||cache.blocked[w.run_id]) return null;
  // A new comparison owns a new request and transient state; READY lives in the session cache.
  return <ExplanationPanel key={identity} pair={pair} identity={identity} autoStart={autoStart} requestKey={requestKey}/>;
}

function ExplanationPanel({pair,identity,autoStart,requestKey}: {pair:PairRead;identity:string;autoStart:boolean;requestKey:number}) {
  const bot=useMasterBot();
  const {workflow:w,refresh}=useWorkflow(),cache=useSessionCache();
  const [cached,setCached]=useStoredState<ExplanationRead|null>(w.run_id,`pair-explanation:${identity}`,null);
  const [transient,setTransient]=useState<ExplanationRead|null>(null);
  const [busy,setBusy]=useState(false),[error,setError]=useState(''),[partial,setPartial]=useState('');
  const pending=useRef<AbortController|null>(null);
  const revealQueue=useRef(''),revealDeadline=useRef(0);
  const revealTimer=useRef<ReturnType<typeof setTimeout>|null>(null);
  function clearReveal() {
    if(revealTimer.current!==null) clearTimeout(revealTimer.current);
    revealTimer.current=null;revealQueue.current='';
  }
  useEffect(()=>()=>{pending.current?.abort();pending.current=null;clearReveal();},[]);
  const result=cached?.status==='READY'&&isExplanationResult(cached)?cached:transient;
  const stale=result?.status==='STALE';
  const explanation=stale?null:result?.explanation;
  const ready=result?.status==='READY'&&!!explanation;
  const reminder=masterBotReminder(explanation?.attention_fields);
  const message=explanation?[explanation.summary_vi,reminder].filter(Boolean).join('\n\n'):'';
  const disabled=busy||ready||!!w.active_operation||w.blockers.some(b=>b.code==='RUN_ARCHIVED');
  async function explain() {
    if(pending.current||disabled) return;
    const controller=new AbortController();pending.current=controller;
    clearReveal();setBusy(true);setError('');setPartial('');setTransient(null);
    function reveal() {
      revealTimer.current=null;
      if(controller.signal.aborted) return;
      // Reveal only received text, with at most 160ms of visual lag for a large chunk.
      const ticks=Math.max(1,Math.ceil((revealDeadline.current-Date.now())/16));
      const count=Math.max(1,Math.ceil(revealQueue.current.length/ticks));
      const next=revealQueue.current.slice(0,count);
      revealQueue.current=revealQueue.current.slice(count);
      setPartial(previous=>previous+next);
      if(revealQueue.current) revealTimer.current=setTimeout(reveal,16);
    }
    try {
      const value=await streamWorkflowPairExplanation(w.run_id,pair.pair_id,mutation(w),text=>{
        if(controller.signal.aborted||!text) return;
        if(bot){setPartial(previous=>previous+text);return;}
        if(!revealQueue.current) revealDeadline.current=Date.now()+160;
        revealQueue.current+=text;
        if(revealTimer.current===null) reveal();
      },controller.signal);
      if(controller.signal.aborted) return;
      if(!isExplanationResult(value)) throw new Error('Invalid Master Bot result');
      clearReveal();setPartial('');
      if(value.status==='READY') setCached(value);
      else setTransient(value);
      if(value.status==='STALE') await refresh().catch(()=>undefined);
    } catch(cause) {
      if(controller.signal.aborted) return;
      clearReveal();setPartial('');setTransient(null);
      cache.handleError(cause,w.run_id);
      if(cause instanceof ApiError&&cause.status===409) {
        setError('Phiên đã thay đổi. Mở lại đối chiếu để giải thích.');await refresh().catch(()=>undefined);
      } else setError('Chưa tải được giải thích từ Master Bot. Hãy thử lại.');
    } finally {
      if(!controller.signal.aborted) {pending.current=null;setBusy(false);}
    }
  }
  useEffect(()=>{if(autoStart) void explain();},[requestKey,autoStart]); // Requests originate only from the explicit row action.
  const rememberReply=bot?.rememberReply;
  useEffect(()=>{
    if(explanation)rememberReply?.(message);
    else if(partial)rememberReply?.(partial);
    else if(error||stale)rememberReply?.(error||'Dữ liệu đã thay đổi. Chọn lại cặp hồ sơ để giải thích.');
    else rememberReply?.(autoStart?'Mình đang chuẩn bị lời giải thích cho cặp hồ sơ này.':'Dữ liệu đã thay đổi. Chọn lại cặp hồ sơ để giải thích.');
  },[explanation,message,partial,error,stale,autoStart,rememberReply]);
  const setHasReply=bot?.setHasReply;
  const setReplyReady=bot?.setReplyReady;
  useEffect(()=>{setReplyReady?.(!busy&&!!(explanation||error||stale));},[busy,explanation,error,stale,requestKey,setReplyReady]);
  useEffect(()=>{setHasReply?.(true);return ()=>setHasReply?.(false);},[setHasReply]);
  const content=<section className={styles.explanationPanel} aria-label="Master Bot giải thích cặp">
    {(!bot||!!error||!!result&&!ready)&&<button aria-label={`${error||result&&!ready?'Thử lại với':'Hỏi'} Master Bot cặp ${pair.pair_id}`} disabled={disabled} onClick={()=>void explain()}>{busy?'Master Bot đang trả lời…':ready?'Đã có giải thích từ Master Bot':error||result?'Thử lại với Master Bot':'Hỏi Master Bot'}</button>}
    {busy&&<div role="status" aria-live="polite" className={styles.masterBotStream}>
      <p className={styles.masterBotProgress}>Master Bot đang trả lời…</p>
      {!bot&&partial&&<p className={styles.masterBotText}>{partial}</p>}
    </div>}
    {bot&&(busy||explanation)&&<MasterBotText text={busy?partial:message}/>}
    {(error||stale)&&<p role="alert">{bot?<HighlightedText text={error||'Dữ liệu đã thay đổi. Mở lại đối chiếu để giải thích.'}/>:error||'Dữ liệu đã thay đổi. Mở lại đối chiếu để giải thích.'}</p>}
    {explanation&&<div role="status">{!bot&&<h3>Master Bot</h3>}{!ready&&<p>Master Bot chưa có kết quả hợp lệ. Bạn có thể thử lại.</p>}<>{!bot&&<p className={styles.masterBotText}>{explanation.summary_vi}</p>}</>
      {!bot&&reminder&&<p>{reminder}</p>}
    </div>}
    {bot&&autoStart&&disabled&&!busy&&!result&&!error&&<p><HighlightedText text="Chờ xử lý hiện tại hoàn tất rồi chọn giải thích lại."/></p>}
    {bot&&!autoStart&&!busy&&!result&&!error&&<p><HighlightedText text="Dữ liệu đã thay đổi. Chọn lại cặp hồ sơ để giải thích."/></p>}
  </section>;
  return bot?(bot.container?createPortal(content,bot.container):null):content;
}
