'use client';
import {useEffect,useState} from 'react';
import {prepareDemoExamples} from '@/lib/api';
import {sessionKey,useSessionCache} from './SessionCache';
import styles from './DemoExperience.module.css';

/** Repair older demo sessions too; all rows still come from the real workflow. */
export default function DemoPreparedExamples({runId}:{runId:string}){
 const cache=useSessionCache();
 const [state,setState]=useState<{status:string;sample_groups:number}>({status:'WAITING',sample_groups:0});
 const [error,setError]=useState(''),[attempt,setAttempt]=useState(0);
 useEffect(()=>{
  const controller=new AbortController();let timer:ReturnType<typeof setTimeout>;
  async function prepare(){
   try{
    const result=await prepareDemoExamples(controller.signal);
    if(controller.signal.aborted)return;
    setError('');setState(result);
    if(result.status==='READY'||result.status==='PREPARING')
     await cache.client.invalidateQueries({queryKey:sessionKey(cache.userId,runId)});
    if(controller.signal.aborted)return;
    if(result.status==='WAITING'||result.status==='PREPARING')timer=setTimeout(()=>void prepare(),2000);
   }catch(e){if(!controller.signal.aborted)setError(e instanceof Error?e.message:'Chưa chuẩn bị được kết quả mẫu.');}
  }
  void prepare();
  return()=>{controller.abort();clearTimeout(timer);};
 },[runId,cache.client,cache.userId,attempt]);
 const failed=!!error||state.status==='FAILED';
 return <section className={styles.exampleNotice} data-demo-status aria-label="Kết quả demo" aria-live="polite">
  {failed?<><strong>Chưa hoàn tất kết quả mẫu.</strong><span>{error||'Bạn có thể thử lại tác vụ trong lịch sử phiên.'}</span>{error&&<button onClick={()=>setAttempt(value=>value+1)}>Thử lại</button>}</>:
   state.status==='READY'?<><strong>{state.sample_groups?`${state.sample_groups} nhóm mẫu đã được chuẩn bị sẵn.`:'Kết quả demo đã sẵn sàng.'}</strong><span>Bạn có thể chọn thông tin đại diện và xuất CSV. Các cặp còn lại vẫn chờ bạn đối chiếu; nhóm mẫu không phải quyết định của bạn.</span></>:
   <><strong>Đang chuẩn bị nhóm và hồ sơ đại diện mẫu…</strong><span>Kết quả sẽ tự hiển thị khi xử lý xong. Bạn vẫn có thể khám phá các bước khác.</span></>}
 </section>;
}
