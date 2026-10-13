'use client';
import {useCallback,useState} from 'react';
import {getUnresolvedRecords} from '@/lib/workflowApi';
import type {ClusterGeneration} from '@/lib/workflowContracts';
import {valueText} from '@/lib/pairReview';
import {useSessionRead} from './useSessionRead';
import {CachedReadStatus,Pager,ReadNotice} from './WorkflowReads';

export default function UnresolvedRecords({runId,generation:g}:{runId:string;generation:ClusterGeneration}) {
 const [cursor,setCursor]=useState<string|null>(null);
 const load=useCallback((signal:AbortSignal)=>getUnresolvedRecords(runId,new URLSearchParams({limit:'25',...(cursor?{cursor}:{})}),signal),[runId,cursor,g.revision,g.source_decision_revision,g.stale]);
 const read=useSessionRead(runId,['unresolved-records',g.revision,g.source_decision_revision,g.stale],{cursor},load);
 return <section aria-label="Danh sách bản ghi chưa giải quyết">{!read.data?<ReadNotice error={read.error} retry={read.retry}/>:<><CachedReadStatus read={read}/><p>{read.data.total} bản ghi chưa giải quyết · tối đa 25 bản ghi/trang.</p><div className="workflow-scroll"><table><thead><tr><th>Bản ghi</th><th>Nguồn</th><th>Mã bản ghi</th></tr></thead><tbody>{read.data.items.map(record=><tr key={record.record_id}><td>{valueText(record.raw_fields.name || record.normalized_fields.name || record.external_id || record.record_id)}</td><td>{record.source_system}</td><td>{record.external_id || record.record_id}</td></tr>)}</tbody></table></div>{!read.data.items.length&&<p>Không có bản ghi chưa giải quyết trong trang này.</p>}<Pager cursor={cursor} next={read.data.next_cursor} onPage={setCursor} busy={read.fetching&&read.previous}/></>}</section>;
}
