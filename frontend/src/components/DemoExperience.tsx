'use client';
import Link from 'next/link';
import {useCallback,useEffect,useRef,useState,type CSSProperties,type ReactNode} from 'react';
import {createPortal} from 'react-dom';
import {usePathname} from 'next/navigation';
import {enterProductDemo,setCsrfToken} from '@/lib/api';
import {useAppUser} from './AppUserContext';
import {useMasterBot} from './MasterBotProvider';
import {HighlightedText} from './MasterBotText';
import styles from './DemoExperience.module.css';
import DemoPreparedExamples from './DemoPreparedExamples';

type Hint={anchor:string;title:string;text:string;bot:string;event?:string};
const hints:Record<string,Hint[]>={
 overview:[
  {anchor:'new-upload',title:'Bắt đầu hoặc tiếp tục làm việc',text:'Mở một phiên đã có hoặc chọn Tải tệp mới để bắt đầu với dữ liệu của bạn.',bot:'Ở Tổng quan, bạn có thể mở lại phiên làm việc hoặc chọn Tải tệp mới để bắt đầu. Mỗi phiên giữ riêng dữ liệu và các quyết định của bạn.'},
  {anchor:'sample-library',title:'Thử với dữ liệu mẫu',text:'Kho dữ liệu mẫu có các tệp giả lập để tải về và thực hành.',bot:'Nếu chưa có tệp để thử, bạn có thể tải dữ liệu trong Kho dữ liệu mẫu. Đây là dữ liệu giả lập để thực hành quy trình.'},
  {anchor:'session-search',title:'Tìm lại phiên làm việc',text:'Dùng Tìm phiên để tra cứu và mở nhanh phiên cần tiếp tục.',bot:'Bạn có thể dùng Tìm phiên để mở nhanh một phiên trước đó. Khi vào từng bước, mình sẽ hướng dẫn ngay tại màn hình đó nhé.'}],
 upload:[
  {anchor:'upload-files',title:'Chuẩn bị dữ liệu',text:'Ở trang tải mới, hướng dẫn tự nạp bộ CSV mẫu. Nếu đã có dữ liệu, hệ thống giữ nguyên dữ liệu của bạn.',bot:'Khi bạn chạy hướng dẫn ở trang tải mới, mình tự chuẩn bị bộ CSV mẫu để bạn thực hành, không cần chọn tệp trên máy. Nếu đã có dữ liệu đang chuẩn bị, mình sẽ giữ nguyên dữ liệu của bạn. Hệ thống kiểm tra dữ liệu trước khi tạo phiên.'},
  {anchor:'upload-confirm',title:'Chọn cách đối chiếu rồi bắt đầu',text:'Embedding mặc định tắt. Bạn có thể bật thêm so sánh AI. Khi dữ liệu đã được kiểm tra, chọn Tạo phiên.',bot:'Embedding mặc định tắt để xử lý nhanh theo thông tin nghiệp vụ. Bạn có thể bật để bổ sung so sánh tên và địa chỉ bằng AI. Khi dữ liệu sẵn sàng, chọn Tạo phiên để đi đến Bước 1.'}],
 general:[{anchor:'window-main',title:'Khám phá màn hình',text:'Bạn có thể xem thông tin tại đây, rồi dùng thanh điều hướng để tiếp tục công việc.',bot:'Bạn có thể xem lại thông tin trên màn hình này rồi chọn mục cần làm tiếp. Mình luôn sẵn sàng giải thích các chỉ số khi bạn bấm vào chúng.'}],
 welcome:[
  {anchor:'demo-embedding',title:'Chọn cách trải nghiệm',text:'Mặc định tắt so sánh AI để xử lý nhanh. Bạn có thể bật nếu muốn thử thêm cách so sánh tên và địa chỉ.',bot:'Mình đã chuẩn bị bốn tệp dữ liệu mẫu cho bạn. Chế độ demo mặc định tắt Embedding để xử lý nhanh; bạn có thể bật nếu muốn thử thêm so sánh tên và địa chỉ bằng AI.'},
  {anchor:'demo-start',title:'Bắt đầu với dữ liệu mẫu',text:'Bấm Bắt đầu trải nghiệm để tạo phiên riêng và đi đến Bước 1.',bot:'Khi sẵn sàng, bạn bấm Bắt đầu trải nghiệm nhé. Mình sẽ cùng bạn kiểm tra dữ liệu, đối chiếu hồ sơ và xuất kết quả.'}],
 data:[
  {anchor:'data-quality',title:'Hiểu chất lượng dữ liệu',text:'Bấm vào một chỉ số để xem ý nghĩa. Hệ thống tự đối chiếu hồ sơ khi kiểm tra dữ liệu xong.',bot:'Mình đang kiểm tra dữ liệu từ bốn nguồn và tìm những hồ sơ có thể thuộc cùng một khách hàng. Bạn thử xem các chỉ số chất lượng dữ liệu nhé.'},
  {anchor:'open-decisions',title:'Xem các cặp cần đối chiếu',text:'Khi đối chiếu hoàn tất, bấm ở đây để sang Bước 2. Trong lúc chờ bạn vẫn có thể xem dữ liệu.',bot:'Khi đối chiếu hoàn tất, bạn có thể sang Bước 2 để xem những cặp hồ sơ cần quyết định. Mình sẽ tiếp tục đồng hành cùng bạn.'}],
 decisions:[
  {anchor:'pair-compare',title:'Xem hai hồ sơ cạnh nhau',text:'Chọn Đối chiếu ở một cặp để xem tên, số điện thoại, giấy tờ và địa chỉ.',bot:'Bạn thử mở một cặp hồ sơ để kiểm tra các thông tin giống và khác nhau. Hai hồ sơ gần giống chưa chắc đã thuộc cùng một khách hàng nhé.'},
  {anchor:'pair-explain',title:'Thử nhờ Bot giải thích',text:'Bấm để nhận giải thích dựa trên thông tin thực tế của cặp này.',bot:'Nếu cần gợi ý, bạn có thể nhờ mình giải thích. Mình sẽ chỉ ra các thông tin cần kiểm tra; quyết định gộp vẫn do bạn lựa chọn.'},
  {anchor:'pair-decision',title:'Bạn quyết định',text:'Đồng ý gộp khi đủ căn cứ; chọn Không gộp nếu là hai khách hàng khác nhau. Bạn có thể thử một vài cặp.',bot:'Bạn hãy quyết định theo thông tin đã đối chiếu. Khi Đồng ý gộp, hệ thống sẽ tự cập nhật nhóm khách hàng phía sau.',event:'pair-decision'},
  {anchor:'step-clusters',title:'Khám phá nhóm khách hàng',text:'Sang Bước 3 để chọn thông tin đại diện. Phiên demo có sẵn nhóm mẫu để bạn khám phá ngay.',bot:'Bạn có thể sang Bước 3 để chọn thông tin đại diện. Ở phiên demo, mình đã chuẩn bị sẵn một số nhóm mẫu; bạn vẫn có thể quyết định thêm các cặp khác nhé.'}],
 comparison:[
  {anchor:'pair-comparison',title:'Kiểm tra từng thông tin',text:'So sánh từng dòng và chú ý các ô khác nhau. Bạn có thể đóng bảng này để nhờ Bot giải thích hoặc đưa ra quyết định.',bot:'Đây là thông tin thực tế của hai hồ sơ. Hãy chú ý điện thoại, giấy tờ và ngày sinh để tránh gộp nhầm người.'},
  {anchor:'comparison-close',title:'Quay lại danh sách',text:'Bạn có thể đóng bảng khi đã xem xong, rồi chọn giải thích hoặc quyết định cho cặp hồ sơ.',bot:'Khi đã xem xong, bạn có thể đóng bảng đối chiếu để tiếp tục. Nếu còn phân vân, hãy nhờ mình giải thích nhé.'}],
 clusters:[
  {anchor:'group-open',title:'Mở một nhóm khách hàng',text:'Chọn Đối chiếu để xem các hồ sơ trong nhóm và thử chọn thông tin muốn giữ.',bot:'Bước 3 tập hợp các hồ sơ của một khách hàng. Ở phiên demo có sẵn một số nhóm mẫu; bạn hãy mở một nhóm để chọn thông tin muốn giữ nhé.'},
  {anchor:'step-golden',title:'Xem kết quả đại diện',text:'Sau khi kiểm tra và lưu thông tin, sang Bước 4 để xem kết quả và xuất CSV.',bot:'Khi đã kiểm tra thông tin, bạn có thể sang Bước 4 để xem hồ sơ đại diện và thử xuất CSV.'}],
 group:[
  {anchor:'source-choice',title:'Chọn thông tin muốn giữ',text:'Bấm vào ô của hồ sơ nguồn. Ô có viền đậm là thông tin được giữ lại cho khách hàng.',bot:'Bạn thử chọn thông tin muốn giữ ở từng dòng nhé. Ô có viền đậm sẽ trở thành thông tin đại diện của khách hàng.'},
  {anchor:'golden-save',title:'Lưu thông tin đã chọn',text:'Nếu đã thay đổi lựa chọn, bấm Lưu để áp dụng. Sau đó bạn có thể đóng nhóm và sang Bước 4.',bot:'Nếu vừa thay đổi ô thông tin, bạn nhớ lưu lựa chọn trước khi xuất dữ liệu nhé.',event:'golden-save'}],
 golden:[
  {anchor:'golden-details',title:'Xem lại kết quả',text:'Mở chi tiết để xem thông tin đại diện và số hồ sơ nguồn của khách hàng.',bot:'Đây là các hồ sơ đại diện của khách hàng. Bạn có thể xem chi tiết trước khi chọn dữ liệu để xuất CSV.'},
  {anchor:'golden-select',title:'Chọn hồ sơ cần xuất',text:'Bạn có thể chọn từng dòng hoặc Chọn tất cả. Lựa chọn được giữ khi chuyển trang.',bot:'Bạn hãy chọn những hồ sơ muốn xuất. Chọn tất cả sẽ chọn toàn bộ kết quả, kể cả các trang chưa mở.'},
  {anchor:'golden-export',title:'Tải kết quả CSV',text:'Bấm Xuất CSV để tải các hồ sơ đã chọn. Bạn có thể mở tệp để xem kết quả.',bot:'Khi đã chọn hồ sơ, bấm Xuất CSV để tải kết quả. Đây là dữ liệu từ chính những lựa chọn bạn vừa thực hiện.',event:'golden-export'}],
};

export default function DemoExperience({children,onEnd}:{children:ReactNode;onEnd:()=>void}){
 const user=useAppUser(),pathname=usePathname(),bot=useMasterBot();
 const [enabled,setEnabled]=useState(true),[loaded,setLoaded]=useState(false),[indices,setIndices]=useState<Record<string,number>>({}),[manualGuide,setManualGuide]=useState(false);
 const [target,setTarget]=useState<HTMLElement|null>(null),[dialogKind,setDialogKind]=useState('');
 const [rect,setRect]=useState<{top:number;left:number;width:number;height:number}|null>(null),[completed,setCompleted]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState('');
 const spoken=useRef(''),scrolled=useRef('');
 const demo=!!user.demo,tour=demo||manualGuide,key=`masterone-demo-guide:${user.id}`;
 const step=pathname==='/app/demo'?'welcome':pathname==='/app'?'overview':pathname.startsWith('/app/uploads/')?'upload':pathname.match(/\/sessions\/[^/]+\/(data|decisions|clusters|golden)(?:\/|$)/)?.[1]??'general';
 const runId=pathname.match(/\/sessions\/([^/]+)/)?.[1]??user.demo?.run_id;
 const scope=dialogKind||step,index=indices[scope]??0,hint=hints[scope]?.[index];
 useEffect(()=>{
  function startGuide(){setManualGuide(true);setEnabled(true);setLoaded(true);setIndices({});spoken.current='';scrolled.current='';bot?.showDemoGuide?.();}
  window.addEventListener('masterone:start-guide',startGuide);return()=>window.removeEventListener('masterone:start-guide',startGuide);
 },[bot?.showDemoGuide]);
 useEffect(()=>{
  if(manualGuide&&enabled&&step==='upload')window.dispatchEvent(new Event('masterone:prepare-guide-upload'));
 },[manualGuide,enabled,step]);
 useEffect(()=>{if(!demo)return;try{const stored=JSON.parse(sessionStorage.getItem(key)??'{}');setEnabled(stored.enabled!==false);setIndices(stored.indices??{});setCompleted(!!stored.completed);}catch{}setLoaded(true);},[demo,key]);
 useEffect(()=>{if(demo&&loaded)sessionStorage.setItem(key,JSON.stringify({enabled,indices,completed}));},[demo,loaded,key,enabled,indices,completed]);
 const next=useCallback(()=>setIndices(previous=>({...previous,[scope]:(previous[scope]??0)+1})),[scope]);
 const skip=useCallback(()=>setEnabled(false),[]);
 const replay=useCallback(()=>setIndices(previous=>({...previous,[scope]:0})),[scope]);
 const setDemoGuide=bot?.setDemoGuide;
 useEffect(()=>{
  if(!tour||!loaded||!enabled||!hints[scope]){setDemoGuide?.(null);return;}
  const finished=!hint;
  setDemoGuide?.({id:`${pathname}:${scope}:${index}`,text:hint?.bot??'Bạn đã xem các gợi ý ở màn hình này. Hãy thao tác theo nhu cầu của bạn hoặc chuyển sang bước tiếp theo; mình sẽ tiếp tục hướng dẫn ở đó.',
   progress:finished?'Đã xem hết gợi ý':`${index+1} / ${hints[scope].length} · Không bắt buộc`,
   next:finished?replay:next,nextLabel:finished?'Xem lại hướng dẫn':undefined,skip});
 },[tour,loaded,enabled,scope,hint,pathname,index,next,replay,skip,setDemoGuide]);
 useEffect(()=>()=>setDemoGuide?.(null),[setDemoGuide]);
 useEffect(()=>{if(!tour||!loaded||!enabled||!step){setTarget(null);return;}
  let frame=0;
  function inspect(){
   frame=0;
   const active=Array.from(document.querySelectorAll<HTMLDialogElement>('dialog[open]')).at(-1);
   const kind=active?.querySelector('[data-demo="source-choice"]')?'group':active?.querySelector('[data-demo="pair-comparison"]')?'comparison':'';
   setDialogKind(kind);
   const candidate=hint?Array.from((active??document).querySelectorAll<HTMLElement>(`[data-demo="${hint.anchor}"]`)).find(el=>el.getClientRects().length&&!el.matches(':disabled')):null;
   setTarget(candidate??null);
  }
  function schedule(){if(!frame)frame=requestAnimationFrame(inspect);}
  const observer=new MutationObserver(schedule);observer.observe(document.body,{childList:true,subtree:true,attributes:true,attributeFilter:['open','disabled','data-demo']});inspect();
  return()=>{observer.disconnect();cancelAnimationFrame(frame);};
 },[tour,loaded,enabled,step,hint]);
 useEffect(()=>{if(!target||!hint)return;
  const visit=`${pathname}:${scope}:${index}`;
  if(scrolled.current!==visit){scrolled.current=visit;const r=target.getBoundingClientRect();if(r.top<100||r.bottom>innerHeight-180)target.scrollIntoView({block:'center',inline:'nearest',behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'instant':'smooth'});}
  function measure(){const r=target!.getBoundingClientRect();setRect({top:r.top,left:r.left,width:r.width,height:r.height});}
  measure();const resize=new ResizeObserver(measure);resize.observe(target);window.addEventListener('scroll',measure,{passive:true,capture:true});window.addEventListener('resize',measure);
  return()=>{resize.disconnect();window.removeEventListener('scroll',measure,true);window.removeEventListener('resize',measure);};
 },[target,hint,pathname,scope,index]);
 useEffect(()=>{if(!tour||!enabled||!hint)return;
  function click(event:MouseEvent){const node=(event.target as Element)?.closest?.('[data-demo]');if(node?.getAttribute('data-demo')===hint!.anchor&&!hint!.event)next();}
  function success(event:Event){const action=(event as CustomEvent<string>).detail;if(action==='golden-export')setCompleted(true);if(action===hint!.event)next();}
  document.addEventListener('click',click);window.addEventListener('demo:completed',success);return()=>{document.removeEventListener('click',click);window.removeEventListener('demo:completed',success);};
 },[tour,enabled,hint,next]);
 useEffect(()=>{
  if(!tour||!loaded||!enabled||!hint)return;
  function isBlank(event:MouseEvent){
   const node=event.target;
   return node instanceof Element&&!node.closest('button,a,input,select,textarea,label,summary,[role="button"],[role="link"],[contenteditable="true"],[data-demo],[data-demo-status],[data-demo-guide-control],[data-master-bot]')&&!window.getSelection()?.toString();
  }
  function holdBackdrop(event:MouseEvent){if(isBlank(event))event.stopPropagation();}
  function advance(event:MouseEvent){if(!isBlank(event))return;event.preventDefault();event.stopPropagation();bot?.showDemoGuide?.();next();}
  document.addEventListener('mousedown',holdBackdrop,true);document.addEventListener('click',advance,true);
  return()=>{document.removeEventListener('mousedown',holdBackdrop,true);document.removeEventListener('click',advance,true);};
 },[tour,loaded,enabled,hint,next,bot?.showDemoGuide]);
 // Completion is remembered even when the visual hints were hidden.
 useEffect(()=>{function done(event:Event){if((event as CustomEvent<string>).detail==='golden-export')setCompleted(true);}window.addEventListener('demo:completed',done);return()=>window.removeEventListener('demo:completed',done);},[]);
 async function restart(){setBusy(true);setError('');try{const found=await enterProductDemo();setCsrfToken(found.csrf_token);window.location.assign('/app/demo');}catch(e){setError(e instanceof Error?e.message:'Chưa bắt đầu lại được.');setBusy(false);}}
 const root=target?.closest('dialog[open]')??(typeof document==='undefined'?null:document.body);
 const cardWidth=typeof window==='undefined'?300:Math.min(300,innerWidth-24);
 const above=!!rect&&rect.top>240;
 const left=rect?Math.max(12,Math.min(rect.left,typeof window==='undefined'?rect.left:innerWidth-cardWidth-12)):12;
 const top=rect?(above?Math.max(12,rect.top-220):Math.min(rect.top+rect.height+14,innerHeight-230)):12;
 return <>{demo&&<section className={styles.banner} aria-label="Điều khiển phiên demo" data-demo-status data-guide-active={enabled&&!!hints[scope]}><div><strong>Phiên demo</strong><small>Dữ liệu giả lập · Không ảnh hưởng đến người khác</small>{runId&&<Link href={`/app/sessions/${runId}/data`}>Về phiên demo</Link>}</div><div><button aria-pressed={enabled} onClick={()=>{if(!enabled){setIndices({});spoken.current='';scrolled.current='';}setEnabled(value=>!value);}}>{enabled?'Ẩn hướng dẫn':'Hướng dẫn demo'}</button><button disabled={busy} onClick={()=>void restart()}>Bắt đầu lại</button><button disabled={busy} onClick={onEnd}>Kết thúc demo</button></div>{error&&<p role="alert">{error}</p>}</section>}{demo&&completed&&step==='golden'&&<section className={styles.completed}><strong>Bạn đã hoàn thành một lượt trải nghiệm!</strong><p>Bạn có thể tiếp tục khám phá hoặc bắt đầu lại với dữ liệu mẫu ban đầu.</p><div><button onClick={()=>{setCompleted(false);setEnabled(false);}}>Tiếp tục khám phá</button><button disabled={busy} onClick={()=>void restart()}>Bắt đầu lại demo</button></div></section>}{tour&&!demo&&enabled&&<div className={styles.manualGuideBar} aria-hidden="true"/>}{demo&&runId&&(step==='clusters'||step==='golden')&&<DemoPreparedExamples key={runId} runId={runId}/>}{children}{tour&&loaded&&enabled&&target&&rect&&hint&&root&&createPortal(<><div aria-hidden="true" className={styles.guideRing} style={{top:rect.top-4,left:rect.left-4,width:rect.width+8,height:rect.height+8}}/><aside role="region" aria-label="Gợi ý trải nghiệm" className={styles.guideCard} data-demo-guide-control data-above={above} style={{top,left,width:cardWidth,'--arrow-left':`${Math.min(cardWidth-25,Math.max(20,rect.left+rect.width/2-left))}px`} as CSSProperties}><header><strong>{hint.title}</strong></header><p><HighlightedText text={hint.text}/></p><footer><small>{index+1} / {hints[scope].length} · Không bắt buộc</small></footer></aside></>,root)}</>;
}
