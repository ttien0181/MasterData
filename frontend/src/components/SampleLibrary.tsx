'use client';
import {useEffect,useState} from 'react';
import {downloadSampleFile,getSampleCatalog,type SampleCatalog} from '@/lib/api';
import PairReviewDialog from './PairReviewDialog';
import styles from './DemoExperience.module.css';

export default function SampleLibrary(){
 const [open,setOpen]=useState(false);
 return <><button data-demo="sample-library" className={styles.secondary} onClick={()=>setOpen(true)}>Kho dữ liệu mẫu</button>{open&&<PairReviewDialog title="Kho dữ liệu mẫu" close={()=>setOpen(false)}><SampleFiles/></PairReviewDialog>}</>;
}
export function SampleFiles(){
 const [data,setData]=useState<SampleCatalog|null>(null),[error,setError]=useState(''),[busy,setBusy]=useState(''),[attempt,setAttempt]=useState(0);
 useEffect(()=>{const controller=new AbortController();getSampleCatalog(controller.signal).then(setData).catch(e=>{if(!controller.signal.aborted)setError(e.message);});return()=>controller.abort();},[attempt]);
 async function download(name:string){setBusy(name);setError('');try{await downloadSampleFile(name);}catch(e){setError(e instanceof Error?e.message:'Chưa tải được tệp. Bạn thử lại nhé.');}finally{setBusy('');}}
 return <div className={styles.library}>{error&&<p role="alert">{error} <button onClick={()=>{setError('');setAttempt(n=>n+1);}}>Thử lại</button></p>}{!data?<p role="status">Đang tải danh sách tệp…</p>:<><span className={styles.badge}>Dữ liệu giả lập · {data.version}</span><h3>{data.title}</h3><p>{data.description}</p><p><strong>{data.file_count} tệp · {data.record_count.toLocaleString('vi-VN')} hồ sơ</strong>. Tải cả bốn tệp vào cùng một phiên để thực hành đầy đủ.</p><div className={styles.files}>{data.files.map(file=><article key={file.name}><div><strong>{file.description}</strong><small>{file.source} · {file.record_count} hồ sơ · {file.name}</small></div><button disabled={!!busy} onClick={()=>void download(file.name)}>{busy===file.name?'Đang tải…':'Tải CSV'}</button></article>)}</div><button className={styles.primary} disabled={!!busy} onClick={()=>void download('masterone-du-lieu-mau.zip')}>{busy==='masterone-du-lieu-mau.zip'?'Đang tải…':'Tải cả bộ ZIP'}</button></>}</div>;
}
