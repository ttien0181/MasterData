import { groupLabel, number } from '@/lib/pairReview';
import styles from './DecisionOverview.module.css';
export const groupColor = (key: string) => ({ A:'#10b981', B:'#3b82f6', C:'#f59e0b', D:'#f43f5e', BLOCKED:'#64748b' }[key] || '#8b5cf6');
export default function PairDistribution({groups,total}: {groups:{key:string;total:number}[];total:number}) {
  let offset=0;
  return <figure className={styles.distribution} aria-label="Biểu đồ phân bố Group">
    <svg viewBox="0 0 160 160" role="img" aria-label={groups.map(g=>`${g.key}: ${number(g.total)}`).join(', ') || 'Không có cặp'}>
      <circle cx="80" cy="80" r="62" fill="none" stroke="var(--line,#e2e8f0)" strokeWidth="16"/>
      {groups.map(g=>{const value=total?g.total/total*100:0;const start=offset;offset+=value;return <circle key={g.key} cx="80" cy="80" r="62" fill="none" stroke={groupColor(g.key)} strokeWidth="16" pathLength="100" strokeDasharray={`${value} ${100-value}`} strokeDashoffset={-start} transform="rotate(-90 80 80)"><title>{groupLabel(g.key)}: {number(g.total)}</title></circle>;})}
      <text x="80" y="78" textAnchor="middle" fill="currentColor" fontSize="19" fontWeight="700">{number(total)}</text>
      <text x="80" y="100" textAnchor="middle" fill="currentColor" fontSize="12">cặp bản ghi</text>
    </svg>
  </figure>;
}
