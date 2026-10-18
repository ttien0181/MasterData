'use client';
import { useCallback, useEffect, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import { reassessPairs, requestPairAdvice, getOperation, mutation, getPair } from '@/lib/workflowApi';
import type { BulkPreview, BulkRequest, BulkScope, Decision, PairFilter, PairRead } from '@/lib/workflowContracts';
import { adviceSkipLabel, decisionLabels, groupLabel, groups, number, subgroupLabel } from '@/lib/pairReview';
import { tabReadiness } from '@/lib/workflowNavigation';
import { useWorkflow } from './SessionWorkspace';
import { useWorkflowAction } from './useWorkflowAction';
import { usePageSelection } from './usePageSelection';
import { CachedReadStatus, ReadNotice } from './WorkflowReads';
import PageCheckbox from './PageCheckbox';
import PairReviewRow from './PairReviewRow';
import DecisionOverview from './DecisionOverview';
import PairComparison from './PairComparison';
import {useMasterBot} from './MasterBotProvider';
import PairExplanation from './PairExplanation';
import PairBulkConfirmation from './PairBulkConfirmation';
import PairCompletion from './PairCompletion';
import PairConflicts from './PairConflicts';
import PairAmendment from './PairAmendment';
import PairAcceptReason from './PairAcceptReason';
import ClusterPreviewDialog from './ClusterPreviewDialog';
import PairClusterReview from './PairClusterReview';
import styles from './DecisionWorkspace.module.css';
import { useSessionRead } from './useSessionRead';
import { useStoredState } from './SessionCache';
import { useTabView } from './useTabView';
import { usePairReviewCache } from './usePairReviewCache';
import { usePairGroupView } from './usePairGroupView';
import { usePairReviewQueue } from './usePairReviewQueue';
import { scopeKey } from '@/lib/pairReviewQueue';
type Intent={body:BulkRequest;label:string};
export default function DecisionWorkspace() {
  const {workflow:w}=useWorkflow(),router=useRouter(),action=useWorkflowAction();
  const groupView=usePairGroupView(w.run_id),{filter,offset,setOffset,search,setSearch}=groupView;
  const [operationId,setOperationId]=useStoredState(w.run_id,'pairs:operation','');
  const bot=useMasterBot();
  const [botRequest,setBotRequest]=useState<{pair:PairRead;revision:string;serial:number}|null>(null);
  const botRevision=JSON.stringify([w.run_id,w.workflow_revision,w.decision_revision,w.assessment_id]);
  const [reviewCluster,setReviewCluster]=useState('');
  const [conflictsOpen,setConflictsOpen]=useState(false),[amending,setAmending]=useState<PairRead|null>(null);
  const [accepting,setAccepting]=useState<PairRead|null>(null),[clusterPreview,setClusterPreview]=useState<boolean|null>(null);
  const [comparison,setComparison]=useStoredState(w.run_id,'pairs:comparison',''),[bulkIntent,setBulkIntent]=useState<Intent|null>(null);
  const selection=usePageSelection(filter,`${w.run_id}:${w.assessment_id || "legacy"}:${w.decision_revision}`),ready=tabReadiness(w.workflow_state,'decisions').ready;
  const {read,summary,prefetchGroup,updatedPair,fresh}=usePairReviewCache(w,filter,offset,ready);
  const review=usePairReviewQueue(updatedPair);
  const view=useTabView(w.run_id,`decisions:${filter.group || 'ALL'}`,!!read.data);
  const operationLoad=useCallback((signal:AbortSignal)=>getOperation(w.run_id,operationId,signal),[w.run_id,operationId]);
  const operation=useSessionRead(w.run_id,['operation',operationId,w.workflow_revision],{},operationLoad,!!operationId);
  const running=w.active_operation?.operation_id===operationId?w.active_operation:operation.data;
  const items=read.data?.items.filter(p=>JSON.stringify([p.baseline,p.left_record?.raw_fields,p.right_record?.raw_fields]).toLocaleLowerCase().includes(search.toLocaleLowerCase())) || [];
  const total=read.data?.total || 0,loadedOffset=read.data?.loadedOffset || 0,pageCount=Math.max(1,Math.ceil(total/10));
  useEffect(()=>{
    if(fresh && read.data && offset>0 && offset>=read.data.total){setOffset(Math.floor(Math.max(0,read.data.total-1)/10)*10);setComparison('');}
  },[read.data?.total,fresh,offset,setOffset,setComparison]);
  const selectedCount=selection.all?Math.max(0,total-selection.count):selection.count;
  const currentAssessment=!!read.data&&!read.previous&&!read.error&&read.data.assessmentId===w.assessment_id;
  const canWrite=(w.allowed_actions.includes('decide-pair')||w.allowed_actions.includes('queue-decisions'))&&!action.busy&&review.pending<100&&currentAssessment;
  const canBulk=(w.allowed_actions.includes('bulk-decisions')||w.allowed_actions.includes('queue-decisions'))&&!action.busy&&review.pending<100&&currentAssessment;
  const scopeLabel=`${number(selectedCount)} cặp đã chọn${selection.all?' trong bộ lọc':''}`;
  const scopeInvalid=!selectedCount;
  const acceptGroup=!selectedCount&&!!filter.group&&total>0;
  const summaryFresh=!!summary.data&&!summary.previous&&!summary.error&&summary.data.decision_revision===w.decision_revision&&(summary.data.assessment_id??null)===(w.assessment_id??null);
  const summaryGroup=summary.data?.groups.find(item=>item.key===filter.group);
  const acceptCounts=filter.subgroup?summaryGroup?.subgroups.find(item=>item.key===filter.subgroup):filter.group?summaryGroup:summary.data;
  const noBulkAccept=summaryFresh&&acceptCounts?.manual_available===0;
  const acceptUnavailableMessage=noBulkAccept?(acceptCounts?.unresolved===0?'':filter.group==='B_SUPPORT'?'Chưa đủ thông tin đối chiếu để đồng ý gộp. Cần bổ sung dữ liệu hoặc đối chiếu.':'Không có cặp đủ điều kiện Đồng ý gộp trong phạm vi này.'):'';
  function change(key:keyof PairFilter,value:string) {if(groupView.change(key,value)){selection.clear();setComparison('');}}
  function page(target:number) {if(target===offset){read.retry();return;}setOffset(target);setComparison('');}
  function scope():BulkScope {return selection.scope();}
  function preview(decision:Decision) {if(decision==='merge'&&noBulkAccept)return;const wholeGroup=decision==='merge'&&acceptGroup;setBulkIntent({body:{...mutation(w),expected_decision_revision:w.decision_revision,decision,scope:wholeGroup?{filter:{...filter}}:scope(),auto_only:false},label:wholeGroup?`${filter.subgroup?subgroupLabel(filter.subgroup):groupLabel(filter.group)} · ${number(total)} cặp`:scopeLabel});}
  function bulk(preview?:BulkPreview,reason?:string) {if(!bulkIntent)return;if(review.queue.add({key:`scope:${scopeKey(bulkIntent.body.scope)}`,label:`${bulkIntent.body.decision==='merge'?'Đồng ý gộp':'Không gộp'} · ${bulkIntent.label}`,assessmentId:w.assessment_id??null,decision:bulkIntent.body.decision,scope:bulkIntent.body.scope,reason,reviewGroupRecordIds:bulkIntent.body.review_group_record_ids,reviewGroupToken:bulkIntent.body.review_group_token,previewToken:preview?.preview_token,expectedDecisionRevision:preview?.decision_revision ?? bulkIntent.body.expected_decision_revision})){setBulkIntent(null);selection.clear();}}
  function decide(pair:PairRead,decision:Decision,reason?:string) {
    if(decision==='merge'&&pair.eligibility.reason_required&&!reason){setAccepting(pair);return;}
    if(review.queue.add({key:`pair:${pair.pair_id}`,label:`${decision==='merge'?'Đồng ý gộp':decision==='deny'?'Không gộp':decision==='no_merge'?'Không gộp':'Hoãn'} · ${pair.left_record?.raw_fields.name || pair.pair_id}`,assessmentId:w.assessment_id??null,decision,reason,pair:{id:pair.pair_id,revision:pair.revision}}))setAccepting(null);
  }
  useEffect(()=>{const latest=[...review.items].reverse().find(item=>item.operationId);if(latest?.operationId)setOperationId(latest.operationId);},[review.items,setOperationId]);
  const [syntheticScope,setSyntheticScope]=useState(false);
  const [toolsOpen,setToolsOpen]=useStoredState(w.run_id,'pairs:assessment-tools-open',false);
  const refreshedAdvice=useRef('');
  useEffect(()=>{if(running?.operation_type==='PAIR_ADVICE'&&running.status==='COMPLETED'&&refreshedAdvice.current!==running.operation_id){refreshedAdvice.current=running.operation_id;read.retry();summary.retry();}},[running?.status,running?.operation_id]);
  async function evaluate(kind:'rules'|'llm'){const result=await action.run(()=>kind==='rules'?reassessPairs(w.run_id,{...mutation(w),...(syntheticScope?{model_scope:'synthetic-benchmark' as const}:{})}):requestPairAdvice(w.run_id,{...mutation(w),limit:20}));if(result){setOperationId(result.operation_id);selection.clear();}}
  const compareLoad=useCallback((signal:AbortSignal)=>getPair(w.run_id,comparison,signal),[w.run_id,comparison,w.decision_revision,w.assessment_id,w.workflow_revision,w.advice_revision]);
  const comparisonRead=useSessionRead(w.run_id,['pair-detail',comparison,w.decision_revision,w.assessment_id,w.workflow_revision,w.advice_revision],{},compareLoad,!!comparison,false);
  const currentPair=comparisonRead.data;
  return <section ref={view} className={`workflow-panel ${styles.workspace}`} aria-label="Các cặp hồ sơ khách hàng">
    <header className={styles.pageHeading}><h1>Các cặp hồ sơ khách hàng</h1></header>
    <DecisionOverview onGroup={key=>change('group',key)} prefetchGroup={key=>{const target=groupView.destination(key);prefetchGroup(key,target.filter,target.offset);}} />
    <div className={styles.heading}><h2>Danh sách cặp hồ sơ</h2><details className={styles.toolsDisclosure} open={toolsOpen} onToggle={e=>setToolsOpen(e.currentTarget.open)}><summary>Công cụ đánh giá</summary><section className={styles.assessmentPanel} aria-label="Đánh giá thông tin đối chiếu"><div><h3>{w.assessment_id?'Đánh giá thông tin đối chiếu mới':'Đang hiển thị đánh giá lịch sử'}</h3>{summary.data?.assessment?.model?.status==='EVALUATED'?<p>Mô hình đã kiểm định trên dữ liệu tổng hợp; chưa được chứng nhận cho dữ liệu thực tế.</p>:<p>Mô hình: {summary.data?.assessment?.model?.status==='OUT_OF_SCOPE'?'Chưa áp dụng vì chưa xác nhận phạm vi dữ liệu kiểm thử':'Chưa áp dụng hoặc chưa có mô hình đạt kiểm định'}.</p>}<label><input type="checkbox" checked={syntheticScope} onChange={e=>setSyntheticScope(e.target.checked)}/> Phiên dùng dữ liệu kiểm thử tổng hợp — cho phép mô hình trong phạm vi này</label></div><div className={styles.actionButtons}><button disabled={action.busy||!!review.pending||!w.allowed_actions.includes('reassess-pairs')} onClick={()=>void evaluate('rules')}>Đánh giá lại thông tin đối chiếu</button><button disabled={action.busy||!!review.pending||!w.assessment_id||!w.allowed_actions.includes('assess-with-ai')} onClick={()=>void evaluate('llm')}>Nhờ AI hỗ trợ đối chiếu</button></div></section></details></div>


    {action.error&&!bulkIntent&&<p className={styles.notice} role="alert">{action.error}</p>}
    {!ready?<p>Phân loại chưa sẵn sàng. Theo dõi tiến độ phiên.</p>:<>

      <CachedReadStatus read={summary}/>{!summary.data&&summary.error&&<ReadNotice error={summary.error} retry={summary.retry}/>}
      <section id="decisions-filters" className={styles.reviewTools} aria-label="Bộ lọc và thao tác duyệt">

      <section className={styles.scopeBar} aria-label="Thao tác theo phạm vi"><div className={styles.actionButtons}><button aria-label={acceptGroup?`Đồng ý gộp ${filter.subgroup?'nhóm đã chọn':groupLabel(filter.group)}`:'Đồng ý gộp theo phạm vi'} className={styles.accept} title={acceptUnavailableMessage||undefined} disabled={noBulkAccept || !canBulk || scopeInvalid&&!acceptGroup || review.queue.has(`scope:${scopeKey(acceptGroup?{filter}:scope())}`)} onClick={()=>preview('merge')}>{acceptGroup?'Gộp cả nhóm':'Đồng ý gộp'}</button><button aria-label="Không gộp theo phạm vi" className={styles.deny} disabled={!canBulk || scopeInvalid || review.queue.has(`scope:${scopeKey(scope())}`)} onClick={()=>preview('deny')}>Không gộp</button></div><strong>{number(selectedCount)} cặp đã chọn</strong><div className={styles.selectionTools}><button disabled={!total} onClick={()=>selection.selectAll()}>Chọn tất cả ({number(total)})</button>{!!selectedCount&&<button onClick={selection.clear}>Bỏ chọn</button>}</div></section></section>
      {!canWrite&&read.data&&<p className={styles.notice}>{review.pending>=100?'Hàng chờ đã đủ 100 yêu cầu; hãy chờ các yêu cầu trước hoàn tất.':w.active_operation?'Đang có tác vụ thay đổi phiên đánh giá; hãy chờ bước này hoàn tất.':'Phiên đã chuyển bước hoặc tài khoản không có quyền duyệt.'}</p>}
      {!!review.items.length&&<section className={styles.operation} aria-label="Hàng chờ duyệt">{review.items.some(i=>['FAILED','NEEDS_REVIEW'].includes(i.status))&&<p role="alert">Có yêu cầu chưa thành công. Mở hàng chờ để xem lỗi và đối chiếu.</p>}<details className={styles.queueDisclosure} data-view-key="review-queue"><summary><h3>Hàng chờ duyệt · {number(review.pending)} yêu cầu chưa hoàn tất</h3><span>{review.items.filter(i=>i.status==='SENDING'||i.status==='RUNNING').length?'Đang xử lý · ':''}{review.items.filter(i=>['FAILED','NEEDS_REVIEW'].includes(i.status)).length?`${review.items.filter(i=>['FAILED','NEEDS_REVIEW'].includes(i.status)).length} yêu cầu lỗi · `:''}Xem chi tiết</span></summary><ul>{review.items.map(item=><li key={item.id}><strong>{item.label}</strong> · {{WAITING:'Chờ gửi',SENDING:'Đang lưu',RUNNING:'Đang xử lý',DONE:'Đã hoàn tất',FAILED:'Lỗi cần đối chiếu',NEEDS_REVIEW:'Cần xác nhận hoặc đối chiếu',CANCELLED:'Đã hủy'}[item.status]}{item.error&&<p role="alert">{item.error}</p>}{item.status==='WAITING'&&<button onClick={()=>review.queue.cancel(item.id)} aria-label={`Hủy yêu cầu ${item.label}`}>Hủy yêu cầu</button>}{['FAILED','NEEDS_REVIEW'].includes(item.status)&&<><button onClick={()=>void review.queue.reconcile(item.id)}>Kiểm tra kết quả lưu</button>{(!item.request||item.receiptAbsent)&&<button onClick={()=>void review.queue.resume(item.id)}>Xác nhận gửi tiếp</button>}<button onClick={()=>review.queue.dismiss(item.id)}>Đã xem</button></>}</li>)}</ul></details></section>}
      {running&&<section className={styles.operation} role="status"><details data-view-key="review-operation"><summary><strong>{running.status==='COMPLETED'?'Đã hoàn thành':running.status==='FAILED'?'Tác vụ thất bại':'Đang xử lý'} · {running.operation_type==='ASSESS_PAIRS'?'Đánh giá lại thông tin đối chiếu':running.operation_type==='PAIR_ADVICE'?'AI hỗ trợ đối chiếu':'Thao tác hàng loạt'}</strong> · {number(Number(running.progress.processed || 0))} / {number(Number(running.progress.total || 0))} cặp</summary>{running.operation_type==='BULK_DECISIONS'&&<p>Áp dụng {number(Number(running.progress.applied ?? running.progress.accepted ?? 0))} · Bị chặn {number(Number(running.progress.blocked || 0))} · Giữ quyết định cũ {number(Number(running.progress.skipped || 0))}</p>}{running.operation_type==='PAIR_ADVICE'&&running.status==='COMPLETED'&&<><p>Đã đánh giá {number(Number(running.result.assessed || 0))} cặp · Chưa có kết quả {number(Number(running.result.unavailable || 0))} cặp. Các quyết định được giữ nguyên.</p><p>Bỏ qua theo điều kiện: {Object.entries((running.result.skipped || {}) as Record<string,number>).map(([key,value])=>`${adviceSkipLabel(key)}: ${number(value)}`).join(' · ') || '0'}</p></>}{running.operation_type==='BULK_DECISIONS'&&running.status==='COMPLETED'&&Number(running.progress.applied ?? running.progress.accepted ?? 0)===0&&<p>Không có cặp nào được thay đổi. Kiểm tra phạm vi, quyết định đã có và lý do bị chặn.</p>}</details></section>}
      {operation.error&&<ReadNotice error={operation.error} retry={operation.retry}/>}
      {!read.data?read.error?<ReadNotice error={read.error} retry={read.retry}/>:<section id="decisions-pairs" className={`${styles.list} ${styles.loadingRows}`} aria-busy="true"><p role="status">Đang đọc {filter.group?groupLabel(filter.group):'danh sách cặp'}…</p>{Array.from({length:10},(_,index)=><div key={index} aria-hidden="true"/>)}</section>:<><div className={styles.cacheStatus} role="status">{read.previous?'Đang đọc trang đã chọn…':read.fetching||!fresh?'Đang cập nhật dữ liệu…':''}</div>{read.error&&<ReadNotice error={read.error} retry={read.retry}/>}<section id="decisions-pairs" className={styles.list} aria-busy={read.fetching}><div className={styles.listHeader}><div><h3>Các cặp hồ sơ cần đối chiếu</h3><p>{number(total)} cặp theo bộ lọc · 10 cặp/trang</p></div><button disabled={read.fetching} onClick={()=>{read.retry();summary.retry();}}>Làm mới</button></div>
        <div className={`workflow-toolbar ${styles.filters} ${styles.searchToolbar}`}>
<label>Tìm hồ sơ <input value={search} onChange={e=>setSearch(e.target.value)} placeholder="Họ tên, điện thoại, mã hồ sơ"/></label>
      </div>
        <div className={styles.tableScroll} data-scroll-key="pairs" tabIndex={0} role="region" aria-label="Bảng cặp hồ sơ"><table aria-label="Danh sách cặp hồ sơ"><thead><tr><th className={styles.selectionCell}><PageCheckbox ids={items.map(p=>p.pair_id)} selected={selection.selected} onSelect={ids=>{selection.selectPage(ids);}} onDeselect={selection.deselectPage}/></th><th>Hai hồ sơ khách hàng</th><th>Phân nhóm</th><th>Điểm đối chiếu</th><th>Trạng thái</th><th>Thao tác</th></tr></thead><tbody>{items.map(p=><PairReviewRow key={p.pair_id} pair={p} selected={selection.selected(p.pair_id)} toggle={()=>{selection.toggle(p.pair_id);}} canWrite={canWrite&&!review.queue.has(`pair:${p.pair_id}`)} pending={review.items.find(item=>item.key===`pair:${p.pair_id}`&&['WAITING','SENDING','RUNNING'].includes(item.status))?.status} busy={action.busy} decide={d=>void decide(p,d)} compare={()=>setComparison(p.pair_id)} explain={bot?.canExplain?()=>{bot.show();setBotRequest(previous=>({pair:p,revision:botRevision,serial:(previous?.serial??0)+1}));}:undefined}/>)}</tbody></table></div>
        {!items.length&&<div className={styles.empty}><p>{total?'Không có cặp khớp nội dung tìm trong trang.':filter.resolution==='REQUIRED'?`Không còn cặp cần duyệt${filter.group?` trong nhóm ${groupLabel(filter.group)}`:''}.`:'Không có cặp khớp bộ lọc.'}</p>{!total&&!!filter.resolution&&!!summary.data?.total&&<button onClick={()=>change('resolution','')}>Xem tất cả{filter.group?` ${groupLabel(filter.group)}`:' cặp'}</button>}</div>}
        <div className={styles.pagination}><span>{total?`${number(loadedOffset+1)}–${number(loadedOffset+read.data.items.length)} / ${number(total)} cặp`:'0 cặp'} · Trang {Math.floor(loadedOffset/10)+1} / {pageCount}</span><nav aria-label="Phân trang cặp"><button disabled={!loadedOffset || read.fetching&&read.previous} onClick={()=>page(0)}>Trang đầu</button><button disabled={!loadedOffset || read.fetching&&read.previous} onClick={()=>page(Math.max(0,loadedOffset-10))}>Trang trước</button><button disabled={!read.data.next_cursor || read.fetching&&read.previous} onClick={()=>page(loadedOffset+10)}>Trang tiếp</button><button disabled={loadedOffset+10>=total || read.fetching&&read.previous} onClick={()=>page((pageCount-1)*10)}>Trang cuối</button></nav></div>
      </section></>}
    </>}
    {ready && <PairCompletion workflow={w} pending={review.pending} attention={review.attention} busy={action.busy} required={()=>change('resolution','REQUIRED')} conflicts={()=>setConflictsOpen(true)} finalize={setClusterPreview} preview={()=>setClusterPreview(false)}/>}
    {botRequest&&<PairExplanation pair={botRequest.pair} autoStart={botRequest.revision===botRevision} requestKey={botRequest.serial}/>}
    {comparison&&!currentPair&&<ReadNotice error={comparisonRead.error} retry={comparisonRead.retry}/>}
    {conflictsOpen&&<PairConflicts pending={review.pending} attention={review.attention} close={()=>setConflictsOpen(false)} compare={id=>{setConflictsOpen(false);setComparison(id);}} amend={pair=>{setConflictsOpen(false);setAmending(pair);}}/>}
    {amending&&<PairAmendment pair={amending} close={()=>setAmending(null)} saved={updatedPair}/>}
    {currentPair&&<PairComparison runId={w.run_id} pair={currentPair} reviewGroup={currentPair.baseline.primary_group==='D'&&!['ACCEPTED','NON_MATCH','REJECTED'].includes(currentPair.decision)&&canWrite&&canBulk?()=>{setComparison('');setReviewCluster(currentPair.pair_id);}:undefined} amend={w.allowed_actions.includes('amend-pair')&&!['UNDECIDED','DEFERRED'].includes(currentPair.decision)&&!action.busy&&!review.queue.has(`pair:${currentPair.pair_id}`)&&currentAssessment?()=>{setComparison('');setAmending(currentPair);}:undefined} close={()=>setComparison('')}/>}
    {accepting&&<PairAcceptReason pair={accepting} close={()=>setAccepting(null)} disabled={!canWrite} confirm={reason=>decide(accepting,'merge',reason)}/>}
    {clusterPreview!==null&&<ClusterPreviewDialog partial={clusterPreview} pending={review.pending} attention={review.attention} close={()=>setClusterPreview(null)} created={()=>router.push(`/app/sessions/${encodeURIComponent(w.run_id)}/clusters`)} reviewPair={id=>{setClusterPreview(null);setReviewCluster(id);}}/>}
    {reviewCluster&&<PairClusterReview acceptPair={pair=>{setReviewCluster('');decide(pair,'merge');}} reviewLargeGroup={w.allowed_actions.includes('preview-clusters')?()=>{setReviewCluster('');setClusterPreview(false);}:undefined} pairId={reviewCluster} canWrite={canBulk&&canWrite&&!review.pending&&!review.attention} close={()=>setReviewCluster('')} accept={cluster=>{setReviewCluster('');setBulkIntent({body:{...mutation(w),expected_decision_revision:cluster.decision_revision,decision:'merge',scope:{pair_ids:cluster.pending_pair_ids},auto_only:false,review_group_record_ids:cluster.records.map(r=>r.record_id),review_group_token:cluster.review_scope_token},label:`${cluster.records.length} hồ sơ · ${cluster.pending_pair_ids.length} cặp trong cụm`});}}/>}
    {bulkIntent&&<PairBulkConfirmation body={bulkIntent.body} label={bulkIntent.label} queued busy={false} error="" close={()=>setBulkIntent(null)} confirm={bulk}/>}
  </section>;
}
