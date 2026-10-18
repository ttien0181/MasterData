import {fieldLabels} from './pairReview';
export function masterBotReminder(fields?:string[]) {
 const labels=[...new Set((fields??[]).filter(Boolean))].map(field=>(fieldLabels[field]??field).toLocaleLowerCase('vi'));
 if(!labels.length)return '';
 const list=labels.length===1?labels[0]:`${labels.slice(0,-1).join(', ')} và ${labels.at(-1)}`;
 return `Bạn nhớ kiểm tra lại ${list} trước khi quyết định gộp nhé.`;
}
