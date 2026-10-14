'use client';
import {useState} from 'react';
import type {GoldenField,PageRead,PreviewMember} from '@/lib/workflowContracts';
import {fieldLabels,valueText} from '@/lib/pairReview';
import styles from './DecisionWorkspace.module.css';

type Props={members:PreviewMember[];golden?:Record<string,GoldenField>;totalMemberCount?:number;
 loadPage?:(cursor:string)=>Promise<PageRead<PreviewMember>>};
export default function GroupMembersTable({members,golden,totalMemberCount,loadPage}:Props) {
 const total=totalMemberCount??members.length;
 const [offset,setOffset]=useState(0),[page,setPage]=useState(members.slice(0,25));
 const [busy,setBusy]=useState(false),[error,setError]=useState('');
 const fields=['name','phone','email','dob','government_id','address'];
 const different=new Set(fields.filter(field=>new Set(page.map(m=>valueText(m.fields[field])).filter(v=>v!=='—')).size>1));
 async function change(target:number){
  if(busy)return;setBusy(true);setError('');
  try{const next=loadPage?await loadPage(String(target)):{items:members.slice(target,target+25)};
   setPage(next.items);setOffset(target);
  }catch(cause){setError(cause instanceof Error?cause.message:'Không đọc được trang thành viên.');}
  finally{setBusy(false);}
 }
 return <>
  <p>Hiển thị {page.length} / {total} thành viên</p>
  <div className={styles.tableScroll}><table aria-label="Đối chiếu thành viên nhóm"><thead><tr><th>Thông tin</th>{page.map(m=><th key={m.record_id}>{valueText(m.name)||m.record_id}<small>{m.source_system} · {m.external_id||m.record_id}</small></th>)}{golden&&<th>Golden dự kiến</th>}</tr></thead><tbody>{fields.map(field=><tr key={field}><th scope="row">{fieldLabels[field]}{different.has(field)&&<small>Có giá trị khác nhau</small>}</th>{page.map(m=><td key={m.record_id} className={different.has(field)?styles.diffCell:undefined}>{valueText(m.fields[field])}</td>)}{golden&&<td>{valueText(golden[field]?.value)}</td>}</tr>)}</tbody></table></div>
  {total>25&&<nav aria-label="Trang thành viên"><button disabled={busy||offset===0} onClick={()=>void change(offset-25)}>Trang thành viên trước</button><button disabled={busy||offset+page.length>=total||!loadPage&&members.length<total} onClick={()=>void change(offset+25)}>Trang thành viên tiếp</button></nav>}
  {busy&&<p role="status">Đang đọc trang thành viên…</p>}{error&&<p role="alert">{error}</p>}
 </>;
}
