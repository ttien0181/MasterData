'use client';
import type { Decision, PairRead } from '@/lib/workflowContracts';
import { baselineLabels, decisionLabels, groupLabel, groups, number, reasonLabel, subgroupLabel, valueText } from '@/lib/pairReview';
import botStyles from './MasterBot.module.css';
import styles from './DecisionWorkspace.module.css';
import PairEvidenceTags from './PairEvidenceTags';
export default function PairReviewRow({pair:p,selected,toggle,canWrite,busy,pending,decide,compare,explain}: {pair:PairRead;selected:boolean;toggle:()=>void;canWrite:boolean;busy:boolean;pending?:string;decide:(decision:Decision)=>void;compare:()=>void;explain?:()=>void}) {
  const group=p.mvp_group ?? p.review_group ?? p.baseline.primary_group,subgroup=p.review_subgroup ?? p.baseline.subgroup;
  const resolved=!['UNDECIDED','DEFERRED'].includes(p.decision);
  return <tr className={selected?styles.checkedRow:''}><td className={styles.selectionCell}><input type="checkbox" aria-label={`Chọn cặp ${p.pair_id}`} checked={selected} onChange={toggle}/></td>
    <td><div className={styles.pairNames}>{[p.left_record,p.right_record].map((r,i)=><div key={i}><span className={styles.memberMarker}>{i+1}</span><div><strong>{r?.raw_fields.name?valueText(r.raw_fields.name):`Hồ sơ ${i?p.baseline.right_id:p.baseline.left_id}`}</strong><small title={`${r?.source_system || 'Nguồn chưa xác định'} · ${r?.external_id || (i?p.baseline.right_id:p.baseline.left_id)}`}>{r?.source_system || 'Nguồn chưa xác định'}{r?.raw_fields.phone?` · ${valueText(r.raw_fields.phone)}`:''}</small></div></div>)}</div><button data-demo="pair-compare" className={styles.compareButton} aria-label={`Đối chiếu cặp ${p.pair_id}`} onClick={compare}>Đối chiếu</button></td>
    <td><span className={`${styles.classification} ${styles[groups[group || 'UNCLASSIFIED']?.tone || 'neutral']}`}>{group==='BLOCKED'?'Chưa được phép gộp':group?groupLabel(group):'Chưa phân nhóm'}</span><div className={styles.subgroupName}>{subgroupLabel(subgroup)}</div><PairEvidenceTags tags={p.evidence_tags}/></td><td className={styles.score}>{number(p.baseline.score)}</td>
    <td><span className={styles.decision}>{baselineLabels[p.baseline.decision] || p.baseline.decision}</span><div className={styles.reviewStatus}>{p.decision==='ACCEPTED'&&p.automation?.source==='AUTOMATIC'?'Đã chấp nhận tự động':p.decision==='UNDECIDED'&&p.baseline.decision==='INSUFFICIENT_EVIDENCE'?'Giữ riêng tạm thời':decisionLabels[p.decision] || p.decision}</div>
    </td>
    <td>{pending&&<p role="status">{pending==='WAITING'?'Chờ lưu…':'Đang lưu…'}</p>}<div data-demo="pair-decision" className={styles.rowActions}><button className={styles.accept} title={!p.eligibility.manual_allowed?reasonLabel(p.eligibility.blocked_reasons[0] || p.baseline.reason):'Cho phép đưa vào nhóm'} disabled={resolved || busy || !canWrite || !p.eligibility.manual_allowed} aria-label={`Đồng ý gộp cặp ${p.pair_id}`} onClick={()=>decide('merge')}>Đồng ý gộp</button><button className={styles.deny} title="Loại liên kết; ngăn hai hồ sơ cùng nhóm" disabled={resolved || busy || !canWrite} aria-label={`Không gộp cặp ${p.pair_id}`} onClick={()=>decide('deny')}>Không gộp</button>{explain&&<button data-demo="pair-explain" className={botStyles.explainAction} aria-label={`Nhờ Master Bot giải thích cặp ${p.pair_id}`} onClick={explain}>Nhờ Master Bot giải thích</button>}</div></td>
  </tr>;
}
