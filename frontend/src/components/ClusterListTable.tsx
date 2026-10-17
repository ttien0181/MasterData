'use client';
import type {ClusterRead, PageRead} from '@/lib/workflowContracts';
import {number} from '@/lib/pairReview';
import type {usePageSelection} from './usePageSelection';
import PageCheckbox from './PageCheckbox';
import styles from './ClusterWorkspace.module.css';

const statuses:Record<string,string>={AUTO_READY:'Đủ điều kiện',REVIEW_APPROVED:'Đã duyệt',SINGLETON:'Bản ghi riêng',DRAFT:'Dự kiến',CONFIRMED:'Đã xác nhận'};
type Props={data:PageRead<ClusterRead>;clusterId:string;selection:ReturnType<typeof usePageSelection>;
 pageIndex:number;offset:number;busy:boolean;previous:boolean;canConfirm:boolean;
 onOpen:(id:string)=>void;onFirst:()=>void;onPrevious:()=>void;onNext:()=>void;onConfirm:()=>void};

export default function ClusterListTable({data,clusterId,selection,pageIndex,offset,busy,previous,canConfirm,onOpen,onFirst,onPrevious,onNext,onConfirm}:Props){
 const selectedCount=selection.all?data.total-selection.count:selection.count;
 return <section className={styles.listPanel} aria-label="Các nhóm đã tạo" aria-busy={busy}>
  <header className={styles.listHeader}><div><h3>{number(data.total)} nhóm bản ghi</h3><p>Chọn “Đối chiếu” để so sánh thành viên và kiểm tra Golden.</p></div><span className={styles.pageBadge}>Trang {number(pageIndex+1)}</span></header>
  <div className={styles.selectionBar}>
   <fieldset disabled={busy||previous} className={styles.selectionControls}>
    <label><PageCheckbox ids={data.items.map(c=>c.cluster_id)} selected={selection.selected} onSelect={selection.selectPage} onDeselect={selection.deselectPage}/> Chọn trang này</label>
    <button disabled={!data.total} onClick={selection.selectAll}>Chọn tất cả {number(data.total)} nhóm</button>
    <button disabled={!selectedCount} onClick={selection.clear}>Bỏ chọn</button>
   </fieldset>
   <div className={styles.confirmControls}><strong role="status">Đã chọn {number(selectedCount)} nhóm</strong><button className={styles.confirmButton} disabled={busy||previous||!canConfirm||!selectedCount} onClick={onConfirm}>Xác nhận Golden đã chọn</button></div>
  </div>
  <div className={styles.tableScroll} tabIndex={0} role="region" aria-label="Danh sách nhóm có thể cuộn" data-scroll-key="cluster-list"><table className={styles.groupTable} aria-label="Danh sách nhóm bản ghi">
   <thead><tr><th scope="col">Chọn</th><th scope="col">Tên nhóm</th><th scope="col">Số bản ghi</th><th scope="col">Trạng thái nhóm</th><th scope="col">Thao tác</th></tr></thead>
   <tbody>{data.items.map(c=><tr key={c.cluster_id} className={c.cluster_id===clusterId?styles.activeRow:selection.selected(c.cluster_id)?styles.checkedRow:undefined}>
    <td><input type="checkbox" aria-label={`Chọn nhóm ${c.display_name||c.cluster_id}`} disabled={busy||previous} checked={selection.selected(c.cluster_id)} onChange={()=>selection.toggle(c.cluster_id)}/></td>
    <th scope="row"><span className={styles.groupName}>{c.display_name||c.cluster_id}</span><small>{c.singleton?'Bản ghi riêng':'Nhóm nhiều bản ghi'}{c.cluster_id===clusterId?' · Đang đối chiếu':''}</small></th>
    <td><span className={styles.memberCount}>{number(c.member_count)}</span></td>
    <td><span className={`${styles.status} ${['REVIEW_APPROVED','AUTO_READY','CONFIRMED'].includes(c.status)?styles.approved:styles.neutral}`}>{statuses[c.status]||c.status}</span></td>
    <td><button aria-label={`Đối chiếu nhóm ${c.display_name||c.cluster_id}`} aria-pressed={c.cluster_id===clusterId} aria-controls={clusterId?'cluster-detail':undefined} disabled={busy||previous} onClick={()=>onOpen(c.cluster_id)}>{c.cluster_id===clusterId?'Đang xem':'Đối chiếu'} <span aria-hidden="true">→</span></button></td>
   </tr>)}</tbody>
  </table></div>
  {!data.total&&<p className={styles.empty}>Chưa có nhóm để hiển thị.</p>}
  <footer className={styles.pagination}>
   <span aria-live="polite">{busy?'Đang tải trang…':previous?'Chưa tải được trang đã chọn. Có thể quay lại trang trước.':`Hiển thị ${number(data.items.length?offset+1:0)}–${number(offset+data.items.length)} / ${number(data.total)} nhóm`}</span>
   <nav aria-label="Phân trang nhóm"><button disabled={busy||pageIndex===0} onClick={onFirst}>Trang đầu</button><button disabled={busy||pageIndex===0} onClick={onPrevious}>Trang trước</button><span className={styles.pageBadge} aria-current="page">{number(pageIndex+1)}</span><button disabled={busy||previous||!data.next_cursor} onClick={onNext}>Trang tiếp</button></nav>
  </footer>
 </section>;
}
