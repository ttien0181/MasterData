'use client';
const kinds:Record<string,string>={CUSTOM:'Ghi lý do riêng',DOCUMENTS_VERIFIED:'Đã đối chiếu giấy tờ gốc',CONTACT_HISTORY_CONFIRMED:'Đã đối chiếu lịch sử liên hệ',DATA_ENTRY_ERROR_CONFIRMED:'Đã xác nhận lỗi nhập liệu',NAME_CHANGE_CONFIRMED:'Đã xác nhận thay đổi họ tên'};
export const reviewReason=(kind:string,note:string)=>note.trim()?(kind==='CUSTOM'?note.trim():`${kind}: ${note.trim()}`):'';
export default function ReviewReason({kind,note,onKind,onNote,label,kindLabel}:{kind:string;note:string;onKind:(value:string)=>void;onNote:(value:string)=>void;label:string;kindLabel:string}) {
 return <><label>{kindLabel}<select aria-label={kindLabel} value={kind} onChange={e=>onKind(e.target.value)}>{Object.entries(kinds).map(([code,title])=><option key={code} value={code}>{title}</option>)}</select></label><label>{label}<textarea aria-label={label} value={note} maxLength={kind==='CUSTOM'?500:498-kind.length} onChange={e=>onNote(e.target.value)}/></label><small>Ghi nguồn hoặc bằng chứng đã đối chiếu cho các bản ghi đang chọn.</small></>;
}
