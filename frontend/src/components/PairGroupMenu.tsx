'use client';
import {useState} from 'react';
import type {PairFilter, PairSummary} from '@/lib/workflowContracts';
import {groupLabel, groups, number, subgroupLabel} from '@/lib/pairReview';
import styles from './DecisionWorkspace.module.css';

// Unresolved includes optional review (Group C), unlike the required-review count.
// Keep the legacy total only when an older API omits the unresolved counter.
const remaining=(item?:{total:number;unresolved?:number})=>item?.unresolved??item?.total??0;

export default function PairGroupMenu({summary,filter,onGroup,onSubgroup,prefetchGroup}:{summary?:PairSummary|null;filter:PairFilter;onGroup:(group:string)=>void;onSubgroup:(group:string,subgroup:string)=>void;prefetchGroup:(group:string)=>void}) {
  const [expanded,setExpanded]=useState('');
  const items=summary?.groups.filter(group=>group.key!=='BLOCKED') || [];
  const keys=Array.from(new Set(['A','B','C',...items.map(group=>group.key)]));
  return <div className={styles.groupFilters} role="group" aria-label="Group">
    <button aria-pressed={!filter.group&&filter.resolution==='REQUIRED'} onClick={()=>{setExpanded('');onGroup('');}}><span>Cần duyệt</span><strong>{summary?number(summary.needs_review):'—'}</strong></button>
    {keys.map(key=>{const item=items.find(group=>group.key===key);return <div key={key} className={styles.groupMenu}>
      <div className={styles.groupMenuHeading}>
        <button aria-pressed={filter.group===key} onMouseEnter={()=>prefetchGroup(key)} onFocus={()=>prefetchGroup(key)} onClick={()=>onGroup(key)} className={styles[groups[key]?.tone || 'neutral']}><span>{key==='UNCLASSIFIED'?'Chưa phân nhóm':`Group ${key}`}</span><strong title="Cặp chưa duyệt">{summary?number(remaining(item)):'—'}</strong><span>{groupLabel(key)}</span></button>
        <button className={styles.groupToggle} aria-label={`Subgroup Group ${key}`} aria-expanded={expanded===key} aria-controls={`subgroups-${key}`} onClick={()=>setExpanded(expanded===key?'':key)}>{expanded===key?'▴':'▾'}</button>
      </div>
      {filter.group===key&&filter.subgroup&&<div className={styles.currentSubgroup}>{subgroupLabel(filter.subgroup)}</div>}
      {expanded===key&&<div id={`subgroups-${key}`} className={styles.subgroupMenu} role="group" aria-label={`Subgroup của Group ${key}`}>
        <button aria-pressed={filter.group===key&&!filter.subgroup} onClick={()=>{onSubgroup(key,'');setExpanded('');}}><span>Tất cả Group {key}</span><strong title="Cặp chưa duyệt">{number(remaining(item))}</strong></button>
        {item?.subgroups.map(subgroup=><button key={subgroup.key} aria-label={`${subgroupLabel(subgroup.key)} ${number(remaining(subgroup))}`} aria-pressed={filter.group===key&&filter.subgroup===subgroup.key} onClick={()=>{onSubgroup(key,subgroup.key);setExpanded('');}}><span>{subgroupLabel(subgroup.key)}</span><strong title="Cặp chưa duyệt">{number(remaining(subgroup))}</strong></button>)}
        {!item?.subgroups.length&&<p>Không có Subgroup</p>}
      </div>}
    </div>;})}
  </div>;
}
