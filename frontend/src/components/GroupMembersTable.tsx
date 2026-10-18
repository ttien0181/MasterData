'use client';
import {useState,type ReactNode} from 'react';
import type {GoldenField,PageRead,PreviewMember} from '@/lib/workflowContracts';
import {fieldLabels,valueText} from '@/lib/pairReview';
import tableStyles from './GroupMembersTable.module.css';

type Props={members:PreviewMember[];golden?:Record<string,GoldenField>;goldenLabel?:string;totalMemberCount?:number;fields?:string[];externalPagination?:boolean;
 cellSelection?:{selectedSources:Record<string,string|undefined>;disabled:boolean;canChoose:(field:string,recordId:string)=>boolean;onChoose:(field:string,recordId:string)=>void;renderKept:(field:string)=>ReactNode;isManual:(field:string)=>boolean};
 loadPage?:(cursor:string)=>Promise<PageRead<PreviewMember>>};
export default function GroupMembersTable({members,golden,goldenLabel='hồ sơ đại diện dự kiến',totalMemberCount,loadPage,fields=['name','phone','email','dob','government_id','address'],cellSelection,externalPagination=false}:Props) {
 const total=totalMemberCount??members.length;
 const [offset,setOffset]=useState(0),[localPage,setPage]=useState(members.slice(0,25));
 const page=externalPagination?members.slice(0,25):localPage;
 const [busy,setBusy]=useState(false),[error,setError]=useState('');
 const [onlyDifferent,setOnlyDifferent]=useState(false);
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
  <p className={tableStyles.legend}><span className={cellSelection?tableStyles.selectedSwatch:undefined} aria-hidden="true"/> {cellSelection?'Ô viền đậm: thông tin được giữ lại.':'Nền vàng: giá trị khác nhau hoặc thiếu dữ liệu giữa các thành viên đang hiển thị.'}</p>
  <div className={tableStyles.membersTable} tabIndex={0} role="region" aria-label="Bảng so sánh cuộn ngang" data-scroll-key="group-members"><table aria-label="Đối chiếu thành viên nhóm"><thead><tr><th scope="col">Thông tin</th>{page.map(m=><th scope="col" key={m.record_id}>{valueText(m.name)||m.record_id}<small>{m.source_system} · {m.external_id||m.record_id}</small></th>)}{golden&&<th scope="col" className={cellSelection?tableStyles.keptHeader:undefined}>{goldenLabel}</th>}</tr></thead><tbody>{visibleFields.map(field=><tr key={field}><th scope="row">{fieldLabels[field]||field}{different.has(field)&&<small>Khác nhau</small>}</th>{page.map(m=>{
   const selected=cellSelection?.selectedSources[field]===m.record_id;
   const available=cellSelection?.canChoose(field,m.record_id);
   const value=valueText(m.fields[field]);
   return <td key={m.record_id} className={[different.has(field)?tableStyles.diffCell:'',cellSelection?tableStyles.sourceCell:'',selected?tableStyles.selectedCell:''].filter(Boolean).join(' ')}>
    {cellSelection&&available?<button data-demo="source-choice" type="button" className={tableStyles.sourceButton} aria-label={`Giữ ${fieldLabels[field]||field} từ ${m.source_system} (${m.external_id||m.record_id}): ${value}`} aria-pressed={selected} disabled={cellSelection.disabled||busy} onClick={()=>cellSelection.onChoose(field,m.record_id)}><span>{value==='—'?'Thiếu dữ liệu':value}</span>{selected&&<small><span aria-hidden="true">✓ </span>Đang giữ</small>}</button>:<span className={value==='—'?tableStyles.missing:undefined}>{value==='—'?'Thiếu dữ liệu':value}</span>}
   </td>;
  })}{golden&&<td aria-label={cellSelection?`Thông tin giữ lại ${fieldLabels[field]||field}`:undefined} className={cellSelection?[tableStyles.keptCell,cellSelection.isManual(field)?tableStyles.selectedCell:''].join(' '):undefined}>{cellSelection?cellSelection.renderKept(field):valueText(golden[field]?.value)}</td>}</tr>)}</tbody></table></div>
  {onlyDifferent&&!different.size&&<p role="status">Không có trường khác nhau giữa các thành viên trên trang này.</p>}
  {total>25&&!externalPagination&&<nav aria-label="Trang thành viên"><button disabled={busy||offset===0} onClick={()=>void change(offset-25)}>Trang thành viên trước</button><button disabled={busy||offset+page.length>=total||!loadPage&&members.length<total} onClick={()=>void change(offset+25)}>Trang thành viên tiếp</button></nav>}
  {busy&&<p role="status">Đang đọc trang thành viên…</p>}{error&&<p role="alert">{error}</p>}
 </>;
}
