'use client';
import {useState} from 'react';
import type {ClusterGeneration,OperationRead} from '@/lib/workflowContracts';
import UnresolvedRecords from './UnresolvedRecords';
export default function GenerationStatus({generation:g,operation,runId,compact=false}:{generation?:ClusterGeneration|null;operation?:OperationRead|null;runId?:string;compact?:boolean}) {
 const [open,setOpen]=useState(false);
 const operationLabel=operation?({QUEUED:'Chờ tạo nhóm',RUNNING:'Đang tạo nhóm',COMPLETED:'Tác vụ đã chạy xong',FAILED:'Tạo nhóm thất bại',RETRYABLE_FAILED:'Chưa thành công, có thể thử lại',CANCELLED:'Đã hủy'}[operation.status] || operation.status):'';
 if(!g&&!operation)return null;
 return <section className="workflow-panel" aria-label="Độ phủ kết quả">{g&&<><h3>{g.publication_status==='PARTIAL'?'Kết quả một phần (PARTIAL)':'Đủ độ phủ (COMPLETE)'} · phiên bản {g.revision}</h3>{!compact&&<><p>{g.covered_records} / {g.total_records} hồ sơ · {g.multi_record_groups} nhóm nhiều hồ sơ · {g.singletons} nhóm một hồ sơ.</p><p>{g.unresolved_records} hồ sơ chưa giải quyết.</p></>}{g.stale&&<p role="status">Kết quả đã cũ, chỉ đọc. Quyết định hoặc phiên đánh giá đã thay đổi; cần tạo lại nhóm để cập nhật. Không thể sửa hồ sơ đại diện từ phiên bản này.</p>}{!g.stale&&g.read_only&&<p role="status">Phiên bản chỉ đọc.</p>}{g.unresolved_records>0&&runId&&<details open={open} onToggle={event=>setOpen(event.currentTarget.open)}><summary>Hồ sơ chưa giải quyết</summary>{open&&<UnresolvedRecords key={`${g.revision}:${g.source_decision_revision}:${g.stale}`} runId={runId} generation={g}/>}</details>}</>}{operation&&<p>Lần tạo nhóm gần nhất: {operationLabel}{operation.error_code&&` · ${operation.error_code}`}</p>}</section>;
}
