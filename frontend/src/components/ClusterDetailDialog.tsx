'use client';
import {useEffect,useRef,type ReactNode,type KeyboardEvent} from 'react';
import styles from './ClusterWorkspace.module.css';

export default function ClusterDetailDialog({title,close,busy,dirty,children}:{title:string;close:()=>void;busy:boolean;dirty:boolean;children:ReactNode}){
 const dialog=useRef<HTMLDialogElement>(null);
 const heading=useRef<HTMLHeadingElement>(null);
 useEffect(()=>{
  const element=dialog.current!;
  const prior=document.activeElement instanceof HTMLElement?document.activeElement:null;
  const priorOverflow=document.body.style.overflow;
  if(typeof element.showModal==='function')element.showModal();else element.setAttribute('open','');
  document.body.style.overflow='hidden';
  heading.current?.focus({preventScroll:true});
  return()=>{
   if(typeof element.close==='function'&&element.open)element.close();
   document.body.style.overflow=priorOverflow;
   if(prior?.isConnected)prior.focus({preventScroll:true});
  };
 },[]);
 function trapFocus(event:KeyboardEvent<HTMLDialogElement>){
  if(event.key!=='Tab'||!(event.target instanceof Element)||event.target.closest('dialog')!==event.currentTarget)return;
  const controls=Array.from(event.currentTarget.querySelectorAll<HTMLElement>('button, input, select, textarea, summary, a[href], [tabindex]')).filter(element=>element.tabIndex>=0&&!element.matches(':disabled')&&element.getClientRects().length>0);
  const first=controls[0],last=controls.at(-1);
  if(!first){event.preventDefault();heading.current?.focus();return;}
  if(event.shiftKey&&(document.activeElement===first||document.activeElement===heading.current)){
   event.preventDefault();last?.focus();
  }else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first.focus();}
 }
 return <dialog ref={dialog} id="cluster-detail" aria-labelledby="cluster-detail-title" className={styles.detailDialog} onKeyDown={trapFocus} onCancel={event=>{event.preventDefault();if(!busy)close();}}>
  <header className={styles.dialogHeader}><div><h3 id="cluster-detail-title" ref={heading} tabIndex={-1}>{title}</h3><p>{dirty?'Có thay đổi Golden chưa lưu. Bản nháp được giữ khi đóng.':'Đối chiếu thành viên, kiểm tra khác biệt và lựa chọn Golden.'}</p></div><button type="button" aria-label="Đóng chi tiết nhóm" disabled={busy} onClick={close}>Đóng <span aria-hidden="true">×</span></button></header>
  <div className={styles.dialogBody}>{children}</div>
 </dialog>;
}
