'use client';
import Link from 'next/link';
import type {WorkflowRead} from '@/lib/workflowContracts';
import {blockerLabel} from './SessionWorkspace';
import styles from './DecisionWorkspace.module.css';
export default function PairCompletion({workflow:w,pending,attention,busy,required,conflicts,finalize,preview}:{workflow:WorkflowRead;pending:number;attention:number;busy:boolean;required:()=>void;conflicts:()=>void;finalize:(partial:boolean)=>void;preview?:()=>void}) {
 return <section id="decisions-completion" className={styles.completion} aria-label="Chuyển sang tạo nhóm"><div><h3>Đồng ý gộp → Chờ tạo nhóm</h3>{!!pending&&<p role="status">{pending} yêu cầu đang lưu</p>}{!!attention&&<p role="alert">{attention} yêu cầu cần kiểm tra</p>}</div><Link scroll={false} className="workflow-button" href={`/app/sessions/${encodeURIComponent(w.run_id)}/clusters`}>Sang Bước 3</Link></section>;
}
