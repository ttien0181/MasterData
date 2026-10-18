import { groupLabel, number } from '@/lib/pairReview';
import MetricHelp from './MetricHelp';
import styles from './DecisionOverview.module.css';
export const groupColor = (key: string) => ({ A_STRONG:'#059669', A_CONTACT:'#0ea5e9', B_SUPPORT:'#d97706', B_CONFLICT:'#e11d48', A:'#10b981', B:'#3b82f6', C:'#f59e0b', D:'#f43f5e', BLOCKED:'#64748b' }[key] || '#8b5cf6');
export default function PairDistribution({groups,total}: {groups:{key:string;total:number}[];total:number}) {
  let offset=0;
  return <figure data-bot-metric className={styles.distribution} aria-label="Biểu đồ phân bố Nhóm">
    <svg viewBox="0 0 160 160" role="img" aria-label={groups.map(g=>`${groupLabel(g.key)}: ${number(g.total)}`).join(', ') || 'Không có cặp'}>
      <circle cx="80" cy="80" r="62" fill="none" stroke="var(--line,#e2e8f0)" strokeWidth="16"/>
      {groups.filter(g => g.total > 0).map(g=>{const value=total?Math.min(100,g.total/total*100):0;const start=offset;offset+=value;const gap=groups.filter(item=>item.total>0).length>1 ? 1.2 : 0;return <circle key={g.key} cx="80" cy="80" r="62" fill="none" stroke={groupColor(g.key)} strokeWidth="16" pathLength="100" strokeDasharray={`${Math.max(0,value-gap)} ${100-Math.max(0,value-gap)}`} strokeDashoffset={-start} transform="rotate(-90 80 80)"><title>{groupLabel(g.key)}: {number(g.total)}</title></circle>;})}
      <text x="80" y="78" textAnchor="middle" fill="currentColor" fontSize="19" fontWeight="700">{number(total)}</text>
      <text x="80" y="100" textAnchor="middle" fill="currentColor" fontSize="12">cặp hồ sơ</text>
    </svg><MetricHelp label="Phân bố nhóm đối chiếu" message={`Biểu đồ phân bố ${number(total)} cặp hồ sơ theo nhóm thông tin đối chiếu. Phần lớn hơn thể hiện nhiều cặp hơn; đây là số cặp, không phải số khách hàng. Bấm từng nhóm bên cạnh để lọc danh sách và xem ý nghĩa nhóm nhé.`}/>
  </figure>;
}
