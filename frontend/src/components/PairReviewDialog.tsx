'use client';
import { useEffect, useRef, type ReactNode } from 'react';
import styles from './DecisionWorkspace.module.css';
export default function PairReviewDialog({title,close,children,wide=false}: {title:string;close:()=>void;children:ReactNode;wide?:boolean}) {
  const ref=useRef<HTMLDialogElement>(null);
  useEffect(()=>{const dialog=ref.current!;const prior=document.activeElement as HTMLElement | null;if(typeof dialog.showModal==='function')dialog.showModal();else dialog.setAttribute('open','');return()=>{if(typeof dialog.close==='function' && dialog.open)dialog.close();prior?.focus();};},[]);
  return <dialog ref={ref} aria-label={title} className={`${styles.dialog} ${wide?styles.comparison:''}`} onCancel={event=>{event.preventDefault();close();}}><header><h3>{title}</h3><button aria-label="Đóng hộp thoại" onClick={close}>×</button></header>{children}</dialog>;
}
