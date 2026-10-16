import {fieldLabels} from '@/lib/pairReview';
export default function PairEvidenceTags({tags=[]}:{tags?:string[]}){
 const labels=tags.flatMap(tag=>{
  if(tag==='AGENT_DISAGREEMENT')return ['Agent bất đồng'];
  const match=/^FIELD_STATE_(NAME|PHONE|EMAIL|ADDRESS|DOB|GOVERNMENT_ID)_(EX|NX|NA|HS|CL|CF)$/.exec(tag);
  if(!match)return [];
  const suffix=match[2]==='CF'?'mâu thuẫn':['EX','NX'].includes(match[2])?'khớp':'tương đồng';
  return [`${fieldLabels[match[1].toLowerCase()]} ${suffix}`];
 });
 return labels.length?<div className="pair-evidence-tags">{Array.from(new Set(labels)).map(label=><span key={label}>{label}</span>)}</div>:null;
}
