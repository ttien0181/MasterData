import type { PairBaseline } from '@/lib/workflowContracts';
import { baselineLabels, number } from '@/lib/pairReview';
import styles from './DecisionWorkspace.module.css';

export default function PairAgentEvidence({ baseline }: { baseline: PairBaseline }) {
  const rule=baseline.rule_conclusion || baseline.decision;
  const ruleLabel=rule==='REVIEW'?'Cần duyệt':baselineLabels[rule] || 'Chờ đối chiếu';
  const embedding=(['name','address'] as const).map(field=>{
    const evidence=baseline.embedding_evidence?.[field];
    const scored=evidence?.status==='OK'&&typeof evidence.cosine==='number'&&Number.isFinite(evidence.cosine);
    return {label:field==='name'?'Tên':'Địa chỉ',scored,
      text:scored?evidence.cosine!.toFixed(3).replace('.',','):evidence?.status==='ERROR'?'Lỗi':evidence?.status==='OUT_OF_SCOPE'?'Ngoài phạm vi':'Chưa có'};
  });
  const hasEmbedding=embedding.some(field=>field.text!=='Chưa có');
  const combined=baseline.embedding_enabled===false?'Theo quy tắc':baseline.agent_disagreement?'Chưa thống nhất':!baseline.coordinator_version?'Chưa có kết quả':
    ['BLOCKED','NOT_MERGEABLE','NON_MATCH'].includes(baseline.decision)?'Không đủ điều kiện gộp':
    baseline.decision==='INSUFFICIENT_EVIDENCE'?'Chưa đủ căn cứ':baseline.decision==='MERGEABLE'?'Đề xuất gộp':'Cần duyệt';
  return <section className={styles.agentEvidence} aria-label="Kết quả đối chiếu">
    <section><h3>Theo quy tắc</h3><p>Điểm: {Number.isFinite(baseline.score)?number(baseline.score):'—'} · {ruleLabel}</p></section>
    <section><h3>So sánh tên và địa chỉ</h3>
      {baseline.embedding_enabled===false?<p>Đã tắt trong phiên này</p>:hasEmbedding?embedding.map(field=><p key={field.label}>{`${field.label}: ${field.text}`}</p>):<p>Chưa có kết quả</p>}
      {embedding.some(field=>field.scored)&&<small>Tương đồng −1 đến 1, không phải xác suất.</small>}
    </section>
    <section><h3>Tổng hợp</h3><p>{combined}</p></section>
  </section>;
}
