'use client';
import {useState} from 'react';
import type {PairFilter, PairSummary} from '@/lib/workflowContracts';
import {groupLabel, groups, number, subgroupLabel} from '@/lib/pairReview';
import styles from './DecisionWorkspace.module.css';
import {useMasterBot} from './MasterBotProvider';
import {metricPercent} from '@/lib/metricInsights';
import { mvpGroups } from '@/lib/mvpReview';

// Unresolved includes optional review (Nhóm C), unlike the required-review count.
// Keep the legacy total only when an older API omits the unresolved counter.
const remaining=(item?:{total:number;unresolved?:number})=>item?.unresolved??item?.total??0;

export default function PairGroupMenu({summary,filter,onGroup,onSubgroup,prefetchGroup}:{summary?:PairSummary|null;filter:PairFilter;onGroup:(group:string)=>void;onSubgroup:(group:string,subgroup:string)=>void;prefetchGroup:(group:string)=>void}) {
  const bot=useMasterBot();
  const [expanded,setExpanded]=useState('');
  if (filter.taxonomy === 'mvp_v1') return <div className={`${styles.groupFilters} ${styles.mvpGroupFilters}`} role="group" aria-label="Nhóm">
    <button className={styles.mvpAll} aria-pressed={!filter.group} onClick={() => {onGroup('');bot?.explainMetric(`Tất cả: ${number(summary?.total??0)} cặp hồ sơ trong bốn nhóm đối chiếu. Đây là số cặp, chưa phải số khách hàng. Bạn có thể xem danh sách bên dưới để kiểm tra từng cặp nhé.`);}}><span>Tất cả</span><strong>{summary ? number(summary.total) : '—'}</strong></button>
    <div className={styles.mvpGroupGrid}>{Object.entries(mvpGroups).map(([key, item]) => {
      const count = summary?.groups.find(group => group.key === key)?.total ?? 0;
      const percent = summary?.total ? 100 * count / summary.total : 0;
      return <button key={key} className={`${styles.mvpGroup} ${styles[item.tone]}`} data-mvp-group={key} aria-pressed={filter.group === key} onClick={() => {onGroup(key);bot?.explainMetric(`${item.title}: ${number(count)} cặp (${metricPercent(count,summary?.total)}). ${item.description} Nhóm này giúp bạn ưu tiên đối chiếu; kết quả phân nhóm chưa thay thế quyết định của bạn.`);}} onMouseEnter={() => prefetchGroup(key)} onFocus={() => prefetchGroup(key)} title={item.description}>
        <span>{item.title}</span><strong>{summary ? number(count) : '—'}</strong><small>{summary ? `${percent.toLocaleString('vi-VN', {maximumFractionDigits:1})}%` : '—'}</small>
      </button>;
    })}</div>
  </div>;
  const items=summary?.groups.filter(group=>group.key!=='BLOCKED') || [];
  const keys=Array.from(new Set(['A','B','C',...items.map(group=>group.key)]));
  return <div className={styles.groupFilters} role="group" aria-label="Nhóm">
    <button aria-pressed={!filter.group&&filter.resolution==='REQUIRED'} onClick={()=>{setExpanded('');onGroup('');}}><span>Cần duyệt</span><strong>{summary?number(summary.needs_review):'—'}</strong></button>
    {keys.map(key=>{const item=items.find(group=>group.key===key);return <div key={key} className={styles.groupMenu}>
      <div className={styles.groupMenuHeading}>
        <button aria-pressed={filter.group===key} onMouseEnter={()=>prefetchGroup(key)} onFocus={()=>prefetchGroup(key)} onClick={()=>onGroup(key)} className={styles[groups[key]?.tone || 'neutral']}><span>{key==='UNCLASSIFIED'?'Chưa phân nhóm':`Nhóm ${key}`}</span><strong title="Cặp chưa duyệt">{summary?number(remaining(item)):'—'}</strong><span>{groupLabel(key)}</span></button>
        <button className={styles.groupToggle} aria-label={`Nhóm chi tiết Nhóm ${key}`} aria-expanded={expanded===key} aria-controls={`subgroups-${key}`} onClick={()=>setExpanded(expanded===key?'':key)}>{expanded===key?'▴':'▾'}</button>
      </div>
      {filter.group===key&&filter.subgroup&&<div className={styles.currentSubgroup}>{subgroupLabel(filter.subgroup)}</div>}
      {expanded===key&&<div id={`subgroups-${key}`} className={styles.subgroupMenu} role="group" aria-label={`Nhóm chi tiết của Nhóm ${key}`}>
        <button aria-pressed={filter.group===key&&!filter.subgroup} onClick={()=>{onSubgroup(key,'');setExpanded('');}}><span>Tất cả Nhóm {key}</span><strong title="Cặp chưa duyệt">{number(remaining(item))}</strong></button>
        {item?.subgroups.map(subgroup=><button key={subgroup.key} aria-label={`${subgroupLabel(subgroup.key)} ${number(remaining(subgroup))}`} aria-pressed={filter.group===key&&filter.subgroup===subgroup.key} onClick={()=>{onSubgroup(key,subgroup.key);setExpanded('');}}><span>{subgroupLabel(subgroup.key)}</span><strong title="Cặp chưa duyệt">{number(remaining(subgroup))}</strong></button>)}
        {!item?.subgroups.length&&<p>Không có Nhóm chi tiết</p>}
      </div>}
    </div>;})}
  </div>;
}
