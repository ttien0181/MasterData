import { ApiError } from './api';
import { mutation } from './workflowApi';
import type { BulkScope, Decision, OperationRead, PairRead, WorkflowRead } from './workflowContracts';

export type ReviewIntent = {id:string;key:string;label:string;assessmentId:string|null;decision:Decision;
  status:'WAITING'|'SENDING'|'RUNNING'|'DONE'|'FAILED'|'CANCELLED';error?:string;operationId?:string;
  pair?:{id:string;revision:number};scope?:BulkScope};
type Services={workflow:()=>Promise<WorkflowRead>;pair:(id:string,body:ReturnType<typeof mutation>&{decision:Decision;expected_pair_revision:number})=>Promise<PairRead>;
  bulk:(body:ReturnType<typeof mutation>&{decision:Decision;expected_decision_revision:number;scope:BulkScope;auto_only:boolean})=>Promise<OperationRead>;
  operation:(id:string)=>Promise<OperationRead>;onPair:(pair:PairRead)=>void;refresh:()=>Promise<void>;
  alive:()=>boolean;onError:(error:unknown)=>void;delay?:()=>Promise<void>};
const pending=(item:ReviewIntent)=>['WAITING','SENDING','RUNNING'].includes(item.status);
export const scopeKey=(scope:BulkScope)=>JSON.stringify('pair_ids' in scope?{pair_ids:[...scope.pair_ids].sort()}:
  {filter:Object.fromEntries(Object.entries(scope.filter).filter(([,v])=>v).sort()),excluded_pair_ids:[...(scope.excluded_pair_ids||[])].sort()});

// One executor per user/session; it survives workspace tab changes through SessionUIStore.
// Acknowledged server writes are never rolled back by a failed refresh or automatically retried.
export class PairReviewQueue {
  private items:ReviewIntent[]=[];
  private listeners=new Set<()=>void>();
  private working=false;
  constructor(private services:Services){}
  subscribe=(listener:()=>void)=>{this.listeners.add(listener);return()=>{this.listeners.delete(listener);};};
  snapshot=()=>this.items;
  private change(id:string,patch:Partial<ReviewIntent>){this.items=this.items.map(item=>item.id===id?{...item,...patch}:item);this.emit();}
  private warn=(event:BeforeUnloadEvent)=>{event.preventDefault();event.returnValue='';};
  private emit(){if(typeof window!=='undefined'){window.removeEventListener('beforeunload',this.warn);if(this.items.some(pending))window.addEventListener('beforeunload',this.warn);}this.listeners.forEach(listener=>listener());}
  has(key:string){return this.items.some(item=>item.key===key&&pending(item));}
  add(intent:Omit<ReviewIntent,'id'|'status'>){
    if(this.has(intent.key)||this.items.filter(pending).length>=100)return false;
    this.items=[...this.items.filter(item=>pending(item)||item.status==='FAILED').slice(-99),{...intent,id:crypto.randomUUID(),status:'WAITING'}];
    this.emit();void this.pump();return true;
  }
  cancel(id:string){if(this.items.find(item=>item.id===id)?.status==='WAITING')this.change(id,{status:'CANCELLED'});}
  dismiss(id:string){this.items=this.items.filter(item=>item.id!==id||pending(item));this.emit();}
  private delay(){return this.services.delay?.()??new Promise<void>(resolve=>setTimeout(resolve,1800));}
  private async pump(){
    if(this.working)return;this.working=true;
    try{
      while(this.items.some(item=>item.status==='WAITING')){
        if(!this.services.alive()){this.items=this.items.map(item=>pending(item)?{...item,status:'CANCELLED'}:item);this.emit();break;}
        const item=this.items.find(item=>item.status==='WAITING')!;
        try{
          const w=await this.services.workflow();
          if(!this.services.alive())continue;
          if(!this.items.some(next=>next.id===item.id&&next.status==='WAITING'))continue;
          if((w.assessment_id??null)!==item.assessmentId)throw new Error('Phiên bản đánh giá đã đổi. Hãy đối chiếu và gửi lại thao tác.');
          if(w.active_operation?.operation_type==='BULK_DECISIONS'){await this.delay();continue;}
          if(!w.allowed_actions.includes(item.pair?'decide-pair':'bulk-decisions'))throw new Error('Phiên đang ở bước khác hoặc bạn không còn quyền duyệt.');
          this.change(item.id,{status:'SENDING'});
          const revision={...mutation(w),request_id:item.id};
          if(item.pair){
            const result=await this.services.pair(item.pair.id,{...revision,expected_pair_revision:item.pair.revision,decision:item.decision});
            if(this.services.alive())this.services.onPair(result);
          }else{
            let op=await this.services.bulk({...revision,expected_decision_revision:w.decision_revision,decision:item.decision,scope:item.scope!,auto_only:false});
            this.change(item.id,{status:'RUNNING',operationId:op.operation_id});
            await this.services.refresh().catch(()=>undefined);
            while(['QUEUED','RUNNING'].includes(op.status)&&this.services.alive()){await this.delay();op=await this.services.operation(op.operation_id);}
            if(op.status!=='COMPLETED')throw new Error(`Tác vụ chưa hoàn tất: ${op.error_code||op.status}. Kiểm tra tiến độ trước khi gửi lại.`);
          }
          this.change(item.id,{status:'DONE'});
          if(this.services.alive())await this.services.refresh().catch(()=>undefined);
        }catch(error){
          this.services.onError(error);
          this.change(item.id,{status:'FAILED',error:error instanceof ApiError&&error.status===409?
            'Cặp hoặc phiên đã thay đổi; chưa áp dụng yêu cầu này. Đối chiếu dữ liệu mới rồi thao tác lại.':error instanceof Error?error.message:'Không xác nhận được kết quả. Kiểm tra dữ liệu trước khi gửi lại.'});
          await this.services.refresh().catch(()=>undefined);
        }
      }
    }finally{this.working=false;}
  }
}
