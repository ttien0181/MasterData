'use client';
import {createContext,useCallback,useContext,useEffect,useMemo,useRef,useState,type ReactNode} from 'react';
import {createPortal} from 'react-dom';
import {usePathname} from 'next/navigation';
import {masterBotGuide} from '@/lib/masterBotGuide';
import MasterBotText,{HighlightedText} from './MasterBotText';
import MoMark from './MoMark';
import styles from './MasterBot.module.css';
export type DemoGuide={id:string;text:string;progress:string;next:()=>void;nextLabel?:string;skip:()=>void};
type BotContext={container:HTMLDivElement|null;canExplain:boolean;show:()=>void;setHasReply:(value:boolean)=>void;rememberReply:(text:string)=>void;introEnabled:boolean;explainMetric:(text:string)=>void;toggleIntro:()=>void;introduce?:(text:string)=>void;setReplyReady?:(value:boolean)=>void;setDemoGuide?:(guide:DemoGuide|null)=>void;showDemoGuide?:()=>void};
const Context=createContext<BotContext|null>(null);
export const useMasterBot=()=>useContext(Context);
export default function MasterBotProvider({children,canExplain,userId='current'}:{children:ReactNode;canExplain:boolean;userId?:string}) {
 const pathname=usePathname();
 const [root,setRoot]=useState<Element|null>(null),[container,setContainer]=useState<HTMLDivElement|null>(null),[dialog,setDialog]=useState('');
 const [open,setOpen]=useState(false),[hasReply,setHasReply]=useState(false),[introEnabled,setIntroEnabled]=useState(true),[loaded,setLoaded]=useState(false);
 const [mode,setMode]=useState<'intro'|'explanation'|'metric'|'guide'>('intro'),[intro,setIntro]=useState(''),[explanation,setExplanation]=useState(''),[metric,setMetric]=useState('');
 const [guide,setDemoGuide]=useState<DemoGuide|null>(null);
 const [sequence,setSequence]=useState(0),[complete,setComplete]=useState(false);
 const [replyReady,setReplyReady]=useState(false);
 const explicitOpen=useRef(false);
 const guideVisit=useRef<{scope:string;childSeen:boolean;childOpen:boolean}|null>(null);
 const [content,setContent]=useState<HTMLDivElement|null>(null),[height,setHeight]=useState<number>();
 useEffect(()=>{
  if(!content||typeof ResizeObserver==='undefined')return;
  const measure=()=>{
   if(!content.getClientRects().length)return;
   const body=content.parentElement!,padding=getComputedStyle(body);
   const next=Math.ceil(content.getBoundingClientRect().height+parseFloat(padding.paddingTop)+parseFloat(padding.paddingBottom)+2);
   setHeight(previous=>previous===next?previous:next);
  };
  const observer=new ResizeObserver(measure);observer.observe(content);measure();
  return ()=>observer.disconnect();
 },[content]);
 const preferenceKey=`masterbot-introductions:${userId}`;
 useEffect(()=>{try{setIntroEnabled(localStorage.getItem(preferenceKey)!=='off');}catch{}setLoaded(true);},[preferenceKey]);
 useEffect(()=>{
  const update=()=>{const active=Array.from(document.querySelectorAll('dialog[open]')).at(-1);setRoot(active??document.body);setDialog(active?.getAttribute('aria-label')??'');};
  update();const observer=new MutationObserver(update);
  observer.observe(document.body,{subtree:true,childList:true,attributes:true,attributeFilter:['open','aria-label']});
  return ()=>observer.disconnect();
 },[]);
 const close=useCallback(()=>{explicitOpen.current=false;setOpen(false);},[]);
 const show=useCallback(()=>{explicitOpen.current=true;setMode('explanation');setSequence(value=>value+1);setComplete(false);setReplyReady(false);setOpen(true);},[]);
 const explainMetric=useCallback((text:string)=>{explicitOpen.current=true;setMetric(text);setMode('metric');setSequence(value=>value+1);setComplete(false);setOpen(true);},[]);
 const rememberReply=useCallback((text:string)=>setExplanation(text),[]);
 const introduce=useCallback((text:string)=>{if(!introEnabled||explicitOpen.current)return;setIntro(text);setMode('intro');setSequence(value=>value+1);setComplete(false);setOpen(true);},[introEnabled]);
 useEffect(()=>{
  if(guide){if(explicitOpen.current)return;setIntro(guide.text);setMode('guide');setSequence(value=>value+1);setComplete(false);setOpen(true);}
  else if(mode==='guide'){setMode('intro');close();}
 },[guide,close,mode]);
 const showDemoGuide=useCallback(()=>{explicitOpen.current=false;setMode('guide');setSequence(value=>value+1);setComplete(false);setOpen(true);},[]);
 const toggleIntro=useCallback(()=>setIntroEnabled(enabled=>{const next=!enabled;try{localStorage.setItem(preferenceKey,next?'on':'off');}catch{}return next;}),[preferenceKey]);
 const activeDialog=root?.isConnected&&root.matches('dialog[open]')?dialog:'';
 useEffect(()=>{
  if(!loaded)return;
  if(!introEnabled){guideVisit.current=null;return;}
  // The old subpage URLs are aliases of the same step, not new screens.
  const scope=pathname.match(/^(\/app\/sessions\/[^/]+\/(?:data|decisions|clusters|golden))(?:\/|$)/)?.[1]??pathname;
  const child=!!activeDialog,previous=guideVisit.current;
  if(!previous||previous.scope!==scope){
   guideVisit.current={scope,childSeen:child,childOpen:child};
  }else{
   const firstChild=child&&!previous.childSeen,leavingChild=previous.childOpen&&!child;
   previous.childSeen||=child;previous.childOpen=child;
   if(!firstChild){if(leavingChild&&!explicitOpen.current)close();return;}
  }
  // Visiting a child still counts when an explicit answer takes precedence.
  if(guide)return;
  if(child&&explicitOpen.current)return;
  explicitOpen.current=false;
  const text=masterBotGuide(pathname,activeDialog);
  if(!text)return;
  setIntro(text);setMode('intro');setSequence(value=>value+1);setComplete(false);setOpen(true);
 },[pathname,activeDialog,introEnabled,loaded,close,!!guide]);
 useEffect(()=>{if(!introEnabled&&mode==='intro')close();},[introEnabled,mode,close]);
 const readyToDismiss=mode==='explanation'&&hasReply?replyReady:complete;
 useEffect(()=>{
  if(!open||!readyToDismiss||mode==='guide')return;
  const timer=setTimeout(close,mode==='explanation'?30000:5000);
  return ()=>clearTimeout(timer);
 },[open,mode,readyToDismiss,sequence,close]);
 const value=useMemo(()=>({container,canExplain,show,setHasReply,rememberReply,explainMetric,introEnabled,toggleIntro,introduce,setReplyReady,setDemoGuide,showDemoGuide}),[container,canExplain,show,rememberReply,explainMetric,introEnabled,toggleIntro,introduce,showDemoGuide]);
 const text=mode==='guide'?guide?.text??'':mode==='intro'?intro:mode==='metric'?metric:explanation;
 return <Context.Provider value={value}>{children}{root&&createPortal(<>{guide&&<button className={styles.skipGuide} data-demo-guide-control onClick={guide.skip}>Bỏ qua hướng dẫn</button>}<aside className={styles.widget} data-master-bot aria-label="Master Bot" onKeyDown={event=>{if(event.key==='Escape'&&open){event.preventDefault();event.stopPropagation();close();}}}>
  <section className={styles.bubble} style={height?{height}:undefined} hidden={!open} aria-label="Trò chuyện với Master Bot">
   <button className={styles.close} aria-label="Thu gọn Master Bot" onClick={close}>×</button>
   <div className={styles.body}><div ref={setContent} className={styles.content}>
    {(mode!=='explanation'||!hasReply)&&(text?<MasterBotText key={`${mode}:${sequence}`} text={text} onComplete={()=>setComplete(true)}/>:<p><HighlightedText text="Mình là Master Bot. Chọn “Nhờ Master Bot giải thích” bên cạnh cặp hồ sơ bạn muốn xem."/></p>)}
    <div ref={setContainer} hidden={mode!=='explanation'}/>
    {guide&&<footer className={styles.guideActions} data-demo-guide-control><small>{guide.progress}{!guide.nextLabel&&<span>Bấm khoảng trống để xem tiếp</span>}</small><button onClick={()=>{showDemoGuide();guide.next();}}>{guide.nextLabel??'Tiếp theo'} <span aria-hidden="true">→</span></button></footer>}
   </div></div>
  </section>
  <button className={styles.character} aria-label={open?'Thu gọn trò chuyện Master Bot':'Mở trò chuyện Master Bot'} aria-expanded={open} onClick={()=>{if(open)close();else{explicitOpen.current=mode!=='intro';setSequence(value=>value+1);setComplete(false);setOpen(true);}}}><MoMark size={88}/></button>
 </aside></>,root)}</Context.Provider>;
}
