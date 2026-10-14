import { mutation } from './workflowApi';
import type { BulkRequest, BulkScope, Decision, DecisionRequest, OperationRead, PairRead, WorkflowRead } from './workflowContracts';

export type ReviewIntent = {
  id:string; key:string; label:string; assessmentId:string|null; decision:Decision; reason?:string;
  status:'WAITING'|'SENDING'|'RUNNING'|'DONE'|'FAILED'|'CANCELLED'|'NEEDS_REVIEW';
  error?:string; operationId?:string; pair?:{id:string;revision:number}; scope?:BulkScope;
  expectedDecisionRevision?:number; previewToken?:string; request?:DecisionRequest|BulkRequest; receiptAbsent?:boolean;
  reviewGroupRecordIds?:string[]; reviewGroupToken?:string;
};
type Services = {
  workflow:()=>Promise<WorkflowRead>;pair:(id:string,body:DecisionRequest)=>Promise<PairRead>;
  bulk:(body:BulkRequest)=>Promise<OperationRead>;operation:(id:string)=>Promise<OperationRead>;
  receipt?:(id:string)=>Promise<OperationRead|null>;
  readPair?:(id:string)=>Promise<Pick<PairRead,'revision'|'assessment_id'>>;
  onPair:(pair:PairRead)=>void;refresh:()=>Promise<void>;alive:()=>boolean;
  onError:(error:unknown)=>void;delay?:()=>Promise<void>;
};
export type QueuePersistence={load:()=>ReviewIntent[];save:(items:ReviewIntent[])=>void;clear:()=>void};
const pending=(item:ReviewIntent)=>['WAITING','SENDING','RUNNING'].includes(item.status);
const attention=(item:ReviewIntent)=>['FAILED','NEEDS_REVIEW'].includes(item.status);
const retained=(item:ReviewIntent)=>pending(item)||attention(item);
const reviewMessage='Không xác nhận được kết quả. Đối chiếu biên nhận trước khi gửi lại.';
const storageMessage='Không lưu được thao tác trong trình duyệt. Hãy kiểm tra bộ nhớ trước khi tiếp tục.';
const storagePrefix='pair-review-queue:v1:';
export const scopeKey=(scope:BulkScope)=>JSON.stringify('pair_ids' in scope?{pair_ids:[...scope.pair_ids].sort()}:
  {filter:Object.fromEntries(Object.entries(scope.filter).filter(([,v])=>v).sort()),excluded_pair_ids:[...(scope.excluded_pair_ids||[])].sort()});

// Persist identifiers and mutation metadata only, never pair/record response data.
export function sessionReviewQueueStorage(userId:string,runId:string):QueuePersistence {
  const key=`${storagePrefix}${JSON.stringify([userId,runId])}`;
  return {
    load:()=>{
      if(typeof window==='undefined')return [];
      try {
        const data=JSON.parse(window.sessionStorage.getItem(key)||'[]') as ReviewIntent[];
        return Array.isArray(data)?data.filter(item=>item&&typeof item.id==='string'&&typeof item.key==='string'&&
          typeof item.label==='string'&&['merge','no_merge','deny','defer'].includes(item.decision)&&retained(item)).slice(0,100):[];
      }catch{return [];}
    },
    save:items=>{
      if(typeof window==='undefined')return;
      const data=items.filter(retained).map(item=>({id:item.id,key:item.key,label:item.label,assessmentId:item.assessmentId,
        decision:item.decision,status:item.status,pair:item.pair,scope:item.scope,error:item.error,operationId:item.operationId,
        expectedDecisionRevision:item.expectedDecisionRevision,previewToken:item.previewToken,request:item.request,
        reviewGroupRecordIds:item.reviewGroupRecordIds,reviewGroupToken:item.reviewGroupToken}));
      if(data.length)window.sessionStorage.setItem(key,JSON.stringify(data));else window.sessionStorage.removeItem(key);
    },
    clear:()=>{if(typeof window!=='undefined')window.sessionStorage.removeItem(key);},
  };
}

export function clearSessionReviewQueues(userId:string){
  if(typeof window==='undefined')return;
  try {
    const keys=Array.from({length:window.sessionStorage.length},(_,index)=>window.sessionStorage.key(index));
    for(const key of keys){
      if(!key?.startsWith(storagePrefix))continue;
      try {if((JSON.parse(key.slice(storagePrefix.length)) as string[])[0]===userId)window.sessionStorage.removeItem(key);}catch{/* Ignore unrelated malformed keys. */}
    }
  }catch{/* Storage can be unavailable when a browser session is already closed. */}
}

// One executor per session/run. Reloaded intents need confirmation; sent writes need receipts.
export class PairReviewQueue {
  private items:ReviewIntent[]=[];
  private listeners=new Set<()=>void>();
  private working=false;
  private disposed=false;
  private checking=new Set<string>();
  constructor(private services:Services,private persistence?:QueuePersistence){
    this.items=(persistence?.load()||[]).filter(retained).map(item=>({...item,
      status:item.status==='FAILED'?'FAILED':'NEEDS_REVIEW',receiptAbsent:false,
      error:item.error||'Thao tác được khôi phục. Hãy kiểm tra và xác nhận trước khi tiếp tục.'}));
    // Defer receipt reads until the controller is registered in the session cache.
    if(this.items.length)void Promise.resolve().then(async()=>{
      if(this.disposed||!this.services.alive())return;
      this.emit();for(const item of this.items)if(item.request||item.operationId)await this.reconcile(item.id);
    });
  }
  subscribe=(listener:()=>void)=>{this.listeners.add(listener);return()=>{this.listeners.delete(listener);};};
  snapshot=()=>this.items;
  private change(id:string,patch:Partial<ReviewIntent>){this.items=this.items.map(item=>item.id===id?{...item,...patch}:item);return this.emit();}
  private warn=(event:BeforeUnloadEvent)=>{event.preventDefault();event.returnValue='';};
  private emit(){
    let saved=true;
    if(!this.disposed)try{this.persistence?.save(this.items);}catch(error){
      saved=false;
      this.items=this.items.map(item=>item.status==='WAITING'?{...item,status:'FAILED',error:storageMessage}:item);
      this.services.onError(error);
    }
    if(typeof window!=='undefined'){
      window.removeEventListener('beforeunload',this.warn);
      if(!this.disposed&&this.items.some(retained))window.addEventListener('beforeunload',this.warn);
    }
    this.listeners.forEach(listener=>listener());
    return saved;
  }
  dispose(clearPersisted=true){
    if(clearPersisted)try{this.persistence?.clear();}catch(error){this.services.onError(error);}
    if(this.disposed)return;
    // Unmount stops this executor; saved SENDING/RUNNING states remain for receipt lookup.
    this.disposed=true;
    this.items=this.items.map(item=>retained(item)?{...item,status:'CANCELLED'}:item);this.emit();
  }
  private isAlive(){if(this.disposed)return false;if(this.services.alive())return true;this.dispose();return false;}
  has(key:string){return this.items.some(item=>item.key===key&&retained(item));}
  add(intent:Omit<ReviewIntent,'id'|'status'|'request'|'receiptAbsent'>){
    if(!this.isAlive()){
      this.items=[...this.items,{...intent,id:crypto.randomUUID(),status:'CANCELLED'}];this.emit();return false;
    }
    if(this.has(intent.key)||this.items.filter(retained).length>=100)return false;
    this.items=[...this.items.filter(retained),{...intent,id:crypto.randomUUID(),status:'WAITING'}];
    if(!this.emit())return false;void this.pump();return true;
  }
  cancel(id:string){if(this.items.find(item=>item.id===id)?.status==='WAITING')this.change(id,{status:'CANCELLED'});}
  dismiss(id:string){this.items=this.items.filter(item=>item.id!==id||pending(item));this.emit();}
  private delay(){return this.services.delay?.()??new Promise<void>(resolve=>setTimeout(resolve,1800));}
  private async refresh(){try{await this.services.refresh();}catch{/* A read failure cannot undo an acknowledged mutation. */}}
  private checkAssessment(item:ReviewIntent,w:WorkflowRead){
    if((w.assessment_id??null)!==item.assessmentId)throw new Error('Phiên bản đánh giá đã đổi. Hãy đối chiếu và gửi lại thao tác.');
  }
  private checkWorkflow(item:ReviewIntent,w:WorkflowRead,allowQueue=false){
    this.checkAssessment(item,w);
    const canQueue=allowQueue&&w.active_operation?.operation_type==='BULK_DECISIONS'&&w.allowed_actions.includes('queue-decisions');
    if(!canQueue&&!w.allowed_actions.includes(item.pair?'decide-pair':'bulk-decisions'))throw new Error('Phiên đang ở bước khác hoặc bạn không còn quyền duyệt.');
    const decisionRevision=item.expectedDecisionRevision??(item.request&&!item.pair?(item.request as BulkRequest).expected_decision_revision:undefined);
    if(!item.pair&&decisionRevision!==undefined&&decisionRevision!==w.decision_revision)throw new Error('Kết quả xem trước đã đổi. Hãy kiểm tra lại phạm vi trước khi gửi.');
  }
  async resume(id:string){
    const item=this.items.find(item=>item.id===id);
    if(!item||!attention(item)||this.checking.has(id)||!this.isAlive())return false;
    if((item.request||item.operationId)&&!item.receiptAbsent)return false;
    this.checking.add(id);
    try {
      const w=await this.services.workflow();if(!this.isAlive())return false;this.checkWorkflow(item,w,true);
      if(item.pair){
        if(!this.services.readPair)throw new Error('Cần đọc lại phiên bản cặp trước khi tiếp tục.');
        const pair=await this.services.readPair(item.pair.id);if(!this.isAlive())return false;
        if(pair.revision!==item.pair.revision||(pair.assessment_id!==undefined&&(pair.assessment_id??null)!==item.assessmentId))
          throw new Error('Cặp hoặc phiên bản đánh giá đã đổi. Hãy đối chiếu và gửi lại thao tác.');
      }
      if(!this.items.some(next=>next.id===id&&attention(next)))return false;
      if(!this.change(id,{status:'WAITING',error:undefined}))return false;void this.pump();return true;
    }catch(error){
      if(this.isAlive()){this.services.onError(error);this.change(id,{status:'FAILED',error:error instanceof Error?error.message:reviewMessage});}
      return false;
    }finally{this.checking.delete(id);}
  }
  async reconcile(id:string){
    const item=this.items.find(item=>item.id===id);
    if(!item||!attention(item)||this.checking.has(id)||!this.isAlive())return;
    if(!item.request&&!item.operationId)return;
    this.checking.add(id);
    try {
      if(!this.services.receipt)throw new Error('Chưa có biên nhận để đối chiếu yêu cầu.');
      let op=await this.services.receipt(item.request?.request_id||item.id);if(!this.isAlive())return;
      // A known operation is already proof of submission; never turn it into a resend.
      if(!op&&item.operationId){op=await this.services.operation(item.operationId);if(!this.isAlive())return;}
      if(!op){this.change(id,{status:'NEEDS_REVIEW',receiptAbsent:true,error:'Máy chủ chưa nhận yêu cầu. Hãy kiểm tra và xác nhận trước khi tiếp tục.'});return;}
      this.change(id,{receiptAbsent:false});await this.finishOperation(item,op);
      if(this.isAlive()){this.change(id,{status:'DONE',error:undefined});await this.refresh();}
    }catch(error){
      if(this.isAlive()){this.services.onError(error);this.change(id,{status:'NEEDS_REVIEW',receiptAbsent:false,error:error instanceof Error?error.message:reviewMessage});}
    }finally{this.checking.delete(id);}
  }
  private async finishOperation(item:ReviewIntent,initial:OperationRead){
    let op=initial;
    if(['QUEUED','RUNNING'].includes(op.status))this.change(item.id,{status:'RUNNING',operationId:op.operation_id});
    while(['QUEUED','RUNNING'].includes(op.status)&&this.isAlive()){
      await this.delay();if(!this.isAlive())return;op=await this.services.operation(op.operation_id);
    }
    if(!this.isAlive())return;
    if(op.status!=='COMPLETED')throw new Error(`Tác vụ chưa hoàn tất: ${op.error_code||op.status}. Kiểm tra tiến độ trước khi gửi lại.`);
    if(item.pair){
      const pair=op.result as unknown as PairRead;
      if(pair.pair_id!==item.pair.id||typeof pair.revision!=='number')throw new Error('Biên nhận chưa có kết quả cặp để đối chiếu.');
      this.services.onPair(pair);
    }
  }
  private async pump(){
    if(this.working||!this.isAlive())return;this.working=true;
    try {
      while(this.items.some(item=>item.status==='WAITING')){
        if(!this.isAlive())break;const item=this.items.find(item=>item.status==='WAITING')!;
        try {
          const w=await this.services.workflow();if(!this.isAlive())break;
          if(!this.items.some(next=>next.id===item.id&&next.status==='WAITING'))continue;
          this.checkAssessment(item,w);
          if(w.active_operation?.operation_type==='BULK_DECISIONS'){await this.delay();continue;}
          this.checkWorkflow(item,w);
          const revision={...mutation(w),request_id:item.id,...(item.reason?{reason:item.reason}:{})};
          const body=item.request||(item.pair?
            {...revision,expected_pair_revision:item.pair.revision,decision:item.decision}:
            {...revision,expected_decision_revision:item.expectedDecisionRevision??w.decision_revision,decision:item.decision,scope:item.scope!,auto_only:false,
              ...(item.previewToken?{preview_token:item.previewToken}:{}),
              ...(item.reviewGroupToken?{review_group_token:item.reviewGroupToken,review_group_record_ids:item.reviewGroupRecordIds}:{})});
          if(!this.change(item.id,{status:'SENDING',request:body,receiptAbsent:false,error:undefined})){
            this.change(item.id,{status:'FAILED',request:item.request,receiptAbsent:item.receiptAbsent,error:storageMessage});continue;
          }
          if(!this.isAlive())break;
          if(item.pair){
            const result=await this.services.pair(item.pair.id,body as DecisionRequest);
            if(!this.isAlive())break;this.services.onPair(result);
          }else{
            const op=await this.services.bulk(body as BulkRequest);if(!this.isAlive())break;
            this.change(item.id,{status:'RUNNING',operationId:op.operation_id});
            await this.refresh();if(!this.isAlive())break;await this.finishOperation(item,op);
          }
          if(!this.isAlive())break;
          this.change(item.id,{status:'DONE'});await this.refresh();
        }catch(error){
          if(!this.isAlive())break;this.services.onError(error);if(!this.isAlive())break;
          const sent=!!this.items.find(next=>next.id===item.id)?.request;
          this.change(item.id,{status:sent?'NEEDS_REVIEW':'FAILED',error:sent?reviewMessage:error instanceof Error?error.message:reviewMessage});
          await this.refresh();
        }
      }
    }finally{this.working=false;}
  }
}
