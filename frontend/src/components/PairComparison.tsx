'use client';
import { useState } from 'react';
import type { PairRead } from '@/lib/workflowContracts';
import { fieldLabels, stateLabels, valueText } from '@/lib/pairReview';
import PairReviewDialog from './PairReviewDialog';
import styles from './DecisionWorkspace.module.css';
export default function PairComparison({pair,close}: {pair:PairRead;close:()=>void}) {
  const [normalized,setNormalized]=useState(false),[onlyDifferences,setOnlyDifferences]=useState(false);
  const left=pair.left_record?.[normalized?'normalized_fields':'raw_fields'] || {},right=pair.right_record?.[normalized?'normalized_fields':'raw_fields'] || {};
  const fields=Array.from(new Set([...Object.keys(fieldLabels),...Object.keys(left),...Object.keys(right)] )).filter(k=>!k.startsWith('_'));
  return <PairReviewDialog title="Đối chiếu cặp bản ghi" wide close={close}><div className={styles.compareToolbar}><div className={styles.switch}><button aria-pressed={!normalized} onClick={()=>setNormalized(false)}>Dữ liệu gốc</button><button aria-pressed={normalized} onClick={()=>setNormalized(true)}>Chuẩn hóa</button></div><label><input type="checkbox" checked={onlyDifferences} onChange={e=>setOnlyDifferences(e.target.checked)}/>Chỉ hiện khác biệt</label></div><div className={styles.tableScroll}><table><thead><tr><th>Trường</th><th>Bản ghi 1<small>{pair.left_record?.source_system} · {pair.left_record?.external_id || pair.baseline.left_id}</small></th><th>Bản ghi 2<small>{pair.right_record?.source_system} · {pair.right_record?.external_id || pair.baseline.right_id}</small></th><th>Bằng chứng</th></tr></thead><tbody>{fields.filter(k=>!onlyDifferences || valueText(left[k])!==valueText(right[k])).map(k=><tr key={k}><th>{fieldLabels[k] || k}</th><td className={valueText(left[k])!==valueText(right[k])?styles.diffCell:''}>{valueText(left[k])}</td><td className={valueText(left[k])!==valueText(right[k])?styles.diffCell:''}>{valueText(right[k])}</td><td>{stateLabels[pair.baseline.field_evidence.find(e=>e.field===k)?.state || 'NC'] || 'Chưa đối chiếu'}</td></tr>)}</tbody></table></div><p className={styles.muted}>Đối chiếu hỗ trợ quyết định; điểm quy tắc không phải xác suất. Cặp bị ràng buộc chặn không thể Accept.</p><footer><button onClick={close}>Đóng</button></footer></PairReviewDialog>;
}
