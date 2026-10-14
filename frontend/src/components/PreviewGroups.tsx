'use client';
import {useEffect,useRef,useState} from 'react';
import type {ClusterPreview,PreviewGroup} from '@/lib/workflowContracts';
import {getPreviewGroups,getReviewGroupMembers} from '@/lib/workflowApi';
import GroupMembersTable from './GroupMembersTable';
import styles from './DecisionWorkspace.module.css';

const statuses:Record<string,string>={AUTO_READY:'Đủ điều kiện',REVIEW_APPROVED:'Đã duyệt',SINGLETON:'Bản ghi riêng',DRAFT:'Dự kiến'};
export default function PreviewGroups({runId,operationId,result,stale}:{runId:string;operationId:string;result:ClusterPreview;stale:boolean}) {
 const [groups,setGroups]=useState(result.groups),[next,setNext]=useState(result.groups_next_cursor??null);
 const [history,setHistory]=useState<string[]>([]),[cursor,setCursor]=useState('0'),[busy,setBusy]=useState(false),[error,setError]=useState('');
 const [selected,setSelected]=useState<PreviewGroup|null>(null);
 const alive=useRef(true);useEffect(()=>{alive.current=true;return()=>{alive.current=false;};},[]);
 async function page(target:string,back=false){
  if(busy)return;setBusy(true);setError('');
  try{const found=await getPreviewGroups(runId,operationId,new URLSearchParams({cursor:target,limit:'25'}));
   if(alive.current){setGroups(found.items);setNext(found.next_cursor);setSelected(null);setHistory(old=>back?old.slice(0,-1):[...old,cursor]);setCursor(target);if(found.stale)setError('Bản xem trước đã cũ. Tính lại trước khi tạo nhóm.');}
  }catch(cause){if(alive.current)setError(cause instanceof Error?cause.message:'Không đọc được trang nhóm.');}
  finally{if(alive.current)setBusy(false);}
 }
 return <>
  <p>Hiển thị {groups.length} / {result.publication.clusters} nhóm dự kiến.{cursor!=='0'&&` Từ nhóm thứ ${Number(cursor)+1}.`}</p>
  <div className={styles.tableScroll}><table aria-label="Nhóm dự kiến"><thead><tr><th>Nhóm / thành viên</th><th>Loại nhóm</th><th>Trạng thái</th><th>Đối chiếu</th></tr></thead><tbody>{groups.map((group,index)=><tr key={group.id??index}><td>{group.display_name||group.member_record_ids.join(' / ')}{group.preview_members&&<small>{group.preview_members.map(m=>`${m.source_system} · ${m.external_id||m.record_id}`).join(' / ')}</small>}</td><td>{group.singleton?'Singleton':'Nhiều bản ghi'} · {group.member_count??group.member_record_ids.length} bản ghi</td><td>{statuses[group.status]||group.status}</td><td>{group.preview_members&&<button aria-label={`Đối chiếu nhóm ${group.display_name}`} onClick={()=>setSelected(group)}>Xem thành viên &amp; Golden</button>}</td></tr>)}</tbody></table></div>
  {!groups.length&&<p>{result.publication.clusters?'Không có nhóm trong trang này.':'Chưa có nhóm đủ điều kiện.'}</p>}
  {(next||history.length>0)&&<nav aria-label="Trang nhóm dự kiến"><button disabled={busy||!history.length} onClick={()=>void page(history[history.length-1],true)}>Trang nhóm trước</button><button disabled={busy||!next} onClick={()=>{if(next)void page(next);}}>Trang nhóm tiếp</button></nav>}
  {busy&&<p role="status">Đang đọc trang nhóm…</p>}{error&&<p role="alert">{error}</p>}
  {selected?.preview_members&&<section aria-label="Đối chiếu nhóm đã chọn"><h4>{selected.display_name}</h4>{stale&&<p>Kết quả cũ, chỉ đọc.</p>}<GroupMembersTable key={selected.id??selected.member_record_ids.join('|')} members={selected.preview_members} golden={selected.golden_fields} totalMemberCount={selected.member_count??selected.member_record_ids.length} loadPage={selected.fingerprint?cursor=>getReviewGroupMembers(runId,operationId,selected.fingerprint!,new URLSearchParams({cursor,limit:'25'})):undefined}/></section>}
 </>;
}
