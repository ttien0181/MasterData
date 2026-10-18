'use client';
import { useState, type ReactNode } from 'react';
import type { PairRead } from '@/lib/workflowContracts';
import { decisionLabels, fieldLabels, stateLabels, valueText } from '@/lib/pairReview';
import PairAgentEvidence from './PairAgentEvidence';
import PairReviewDialog from './PairReviewDialog';
import styles from './DecisionWorkspace.module.css';
export default function PairComparison({pair,close,amend,reviewGroup,explanationPanel}: {pair:PairRead;close:()=>void;runId?:string;amend?:()=>void;reviewGroup?:()=>void;explanationPanel?:ReactNode}) {
  const [normalized,setNormalized]=useState(false),[onlyDifferences,setOnlyDifferences]=useState(false),[extras,setExtras]=useState(false);
  const left={...pair.left_record?.extra_fields,...pair.left_record?.[normalized?'normalized_fields':'raw_fields']},right={...pair.right_record?.extra_fields,...pair.right_record?.[normalized?'normalized_fields':'raw_fields']};
  const priority=(k:string)=>valueText(left[k])!==valueText(right[k])?0:left[k]==null||left[k]===''||right[k]==null||right[k]===''?1:2;
  const fields=Array.from(new Set([...Object.keys(fieldLabels),...(extras?[...Object.keys(left),...Object.keys(right)]:[])] )).filter(k=>!k.startsWith('_')).sort((a,b)=>priority(a)-priority(b));
  function evidence(k:string) {
    const state=pair.baseline.field_evidence.find(e=>e.field===k)?.state;
    if(state&&state!=='NC') return stateLabels[state] || (state==='IV'?'Dữ liệu không hợp lệ':'Không đủ điều kiện đối chiếu');
    if(left[k]==null||left[k]===''||right[k]==null||right[k]==='') return 'Thiếu thông tin';
    const label=valueText(left[k])===valueText(right[k])?'Trùng giá trị':'Khác giá trị';
    return k==='government_id'?`${label} · chưa xác minh`:label;
  }
  return <PairReviewDialog title="Đối chiếu cặp hồ sơ" wide close={close}><p className={styles.comparisonStatus}>{decisionLabels[pair.decision] || pair.decision}</p><PairAgentEvidence baseline={pair.baseline}/><div className={styles.compareToolbar}><div className={styles.switch}><button aria-pressed={!normalized} onClick={()=>setNormalized(false)}>Dữ liệu gốc</button><button aria-pressed={normalized} onClick={()=>setNormalized(true)}>Chuẩn hóa</button></div><label><input type="checkbox" checked={onlyDifferences} onChange={e=>setOnlyDifferences(e.target.checked)}/>Chỉ hiện khác biệt</label><label><input type="checkbox" checked={extras} onChange={e=>setExtras(e.target.checked)}/>Trường bổ sung</label></div><div data-demo="pair-comparison" className={styles.tableScroll}><table><thead><tr><th>Trường</th><th>Hồ sơ 1<small>{pair.left_record?.source_system} · {pair.left_record?.external_id || pair.baseline.left_id}</small></th><th>Hồ sơ 2<small>{pair.right_record?.source_system} · {pair.right_record?.external_id || pair.baseline.right_id}</small></th><th>Thông tin đối chiếu</th></tr></thead><tbody>{fields.filter(k=>!onlyDifferences || valueText(left[k])!==valueText(right[k])).map(k=><tr key={k}><th>{fieldLabels[k] || k}</th><td className={valueText(left[k])!==valueText(right[k])?styles.diffCell:''}>{valueText(left[k])}</td><td className={valueText(left[k])!==valueText(right[k])?styles.diffCell:''}>{valueText(right[k])}</td><td>{evidence(k)}</td></tr>)}</tbody></table></div>{explanationPanel}<footer>{reviewGroup&&<button onClick={reviewGroup}>Đối chiếu nhóm</button>}{amend&&<button onClick={amend}>Sửa quyết định</button>}<button data-demo="comparison-close" onClick={close}>Đóng</button></footer></PairReviewDialog>;
}
