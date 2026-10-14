import type {PairBaseline} from './workflowContracts';
import {fieldLabels,reasonLabel} from './pairReview';

/** Short rule evidence, never a probability or a replacement for full evidence. */
export function pairEvidenceSummary(pair:PairBaseline):string {
 if(['NOT_MERGEABLE','BLOCKED','NON_MATCH'].includes(pair.decision))return reasonLabel(pair.reason || 'PAIR_MERGE_BLOCKED');
 if(pair.decision==='INSUFFICIENT_EVIDENCE')return 'Chưa đủ bằng chứng nhận dạng; giữ riêng tạm thời.';
 const evidence=pair.field_evidence || [];
 const labels=(fields:string[])=>fields.slice(0,2).map(field=>(fieldLabels[field] || field).toLocaleLowerCase('vi')).join(', ');
 const anchors=evidence.filter(e=>['phone','email','government_id'].includes(e.field)&&['EX','NX'].includes(e.state)).map(e=>e.field);
 const conflicts=evidence.filter(e=>e.state==='CF').map(e=>e.field);
 if(pair.unverified_identifier_relation==='DIFFERENT_VALUE'&&!conflicts.includes('government_id'))conflicts.push('government_id');
 const parts:string[]=[];
 if(pair.review_support)parts.push(reasonLabel('REVIEW_SUPPORT_'+pair.review_support.kind));
 if(anchors.length)parts.push(`Khớp ${labels(anchors)}`);
 if(conflicts.length)parts.push(`${parts.length?'m':'M'}âu thuẫn ${labels(conflicts)}${conflicts.length>2?` và ${conflicts.length-2} trường khác`:''}`);
 if(!parts.length)parts.push(reasonLabel(pair.reason || 'NON_HARD_CONFLICT'));
 return parts.join('; ')+'.';
}
