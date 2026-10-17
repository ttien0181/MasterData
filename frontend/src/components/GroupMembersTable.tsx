'use client';
import {useState} from 'react';
import type {GoldenField,PageRead,PreviewMember} from '@/lib/workflowContracts';
import {fieldLabels,valueText} from '@/lib/pairReview';
import tableStyles from './GroupMembersTable.module.css';

type Props={members:PreviewMember[];golden?:Record<string,GoldenField>;goldenLabel?:string;totalMemberCount?:number;
 loadPage?:(cursor:string)=>Promise<PageRead<PreviewMember>>};
export default function GroupMembersTable({members,golden,goldenLabel='Golden dự kiến',totalMemberCount,loadPage}:Props) {
 const total=totalMemberCount??members.length;
 const [offset,setOffset]=useState(0),[page,setPage]=useState(members.slice(0,25));
 const [busy,setBusy]=useState(false),[error,setError]=useState('');
 const [onlyDifferent,setOnlyDifferent]=useState(false);
 const fields=['name','phone','email','dob','government_id','address'];
 const different=new Set(fields.filter(field=>new Set(page.map(m=>valueText(m.fields[field]))).size>1));
 const visibleFields=onlyDifferent?fields.filter(field=>different.has(field)):fields;
 async function change(target:number){
  if(busy)return;setBusy(true);setError('');
  try{const next=loadPage?await loadPage(String(target)):{items:members.slice(target,target+25)};
   setPage(next.items);setOffset(target);
  }catch(cause){setError(cause instanceof Error?cause.message:'Không đọc được trang thành viên.');}
  finally{setBusy(false);}
 }
 return <>
  <div className={tableStyles.toolbar}><div><p>Hiển thị {page.length} / {total} thành viên</p><small>{different.size} trường khác nhau trên trang này</small></div><label><input type="checkbox" checked={onlyDifferent} onChange={event=>setOnlyDifferent(event.target.checked)}/> Chỉ hiện trường khác nhau</label></div>
  <p className={tableStyles.legend}><span aria-hidden="true"/> Nền vàng: giá trị khác nhau hoặc thiếu dữ liệu giữa các thành viên đang hiển thị.</p>
  <div className={tableStyles.membersTable} tabIndex={0} role="region" aria-label="Bảng so sánh cuộn ngang" data-scroll-key="group-members"><table aria-label="Đối chiếu thành viên nhóm"><thead><tr><th scope="col">Thông tin</th>{page.map(m=><th scope="col" key={m.record_id}>{valueText(m.name)||m.record_id}<small>{m.source_system} · {m.external_id||m.record_id}</small></th>)}{golden&&<th scope="col">{goldenLabel}</th>}</tr></thead><tbody>{visibleFields.map(field=><tr key={field}><th scope="row">{fieldLabels[field]}{different.has(field)&&<small>Khác nhau</small>}</th>{page.map(m=><td key={m.record_id} className={different.has(field)?tableStyles.diffCell:undefined}>{valueText(m.fields[field])==='—'?<span className={tableStyles.missing}>Thiếu dữ liệu</span>:valueText(m.fields[field])}</td>)}{golden&&<td>{valueText(golden[field]?.value)}</td>}</tr>)}</tbody></table></div>
  {onlyDifferent&&!different.size&&<p role="status">Không có trường khác nhau giữa các thành viên trên trang này.</p>}
  {total>25&&<nav aria-label="Trang thành viên"><button disabled={busy||offset===0} onClick={()=>void change(offset-25)}>Trang thành viên trước</button><button disabled={busy||offset+page.length>=total||!loadPage&&members.length<total} onClick={()=>void change(offset+25)}>Trang thành viên tiếp</button></nav>}
  {busy&&<p role="status">Đang đọc trang thành viên…</p>}{error&&<p role="alert">{error}</p>}
 </>;
}
