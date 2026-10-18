import type {DataProfile} from './workflowContracts';
export const metricNumber=(value?:number|null)=>value!=null&&Number.isFinite(value)&&value>=0?value.toLocaleString('vi-VN',{maximumFractionDigits:1}):'—';
export function metricPercent(n?:number|null,d?:number|null){return n!=null&&d!=null&&Number.isFinite(n)&&Number.isFinite(d)&&d>0&&n>=0&&n<=d?`${(100*n/d).toLocaleString('vi-VN',{maximumFractionDigits:1})}%`:'—';}
export function completenessAverage(profile?:DataProfile|null){
 if(!profile?.record_count)return '—';
 const counts=['name','phone','email','address','dob','government_id'].map(key=>profile.field_completeness?.[key]?.present);
 if(counts.some(n=>n==null||n<0||n>profile.record_count))return '—';
 return metricPercent(counts.reduce<number>((sum,n)=>sum+n!,0),profile.record_count*counts.length);
}
export function metricExplanation(label:string,value:string,meaning:string,advice=''){
 return `${label}: ${value==='—'?'chưa có số liệu':value}. ${meaning}${advice?` ${advice}`:''}`;
}
