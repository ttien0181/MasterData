'use client';
import {useState} from 'react';
import {applyAutomation,mutation,shadowAutomation,stopAutomation} from '@/lib/workflowApi';
import type {AutomationPlan} from '@/lib/workflowContracts';
import {useWorkflow} from './SessionWorkspace';
import {useWorkflowAction} from './useWorkflowAction';
import styles from './DecisionWorkspace.module.css';

const reasons:Record<string,string>={
 NO_CERTIFICATE:'Chưa có chứng nhận chất lượng cho vùng tự động.',AUTOMATION_DISABLED:'Áp dụng tự động chưa được bật.',
 AUTOMATION_STOPPED:'Đã dừng tự động hóa phiên này.',CANARY_BUDGET_EXHAUSTED:'Đã đạt giới hạn thử nghiệm của phiên.',
 CERTIFICATE_EXPIRED:'Chứng nhận đã hết hạn.',CERTIFICATE_PIN_MISMATCH:'Chứng nhận đã thay đổi; cần kiểm định lại.',
 CODE_CHANGED:'Quy tắc đã thay đổi; cần kiểm định lại.',PROFILE_CHANGED:'Cấu hình đã thay đổi; cần kiểm định lại.',
 COHORT_NOT_CERTIFIED:'Mẫu bằng chứng này chưa được chứng nhận.',NO_SAFE_COMPONENTS:'Chưa có cụm đủ điều kiện tự động.',
 NON_PRODUCTION_VALIDATION:'Kiểm định dữ liệu tổng hợp không cấp quyền cho dữ liệu thực tế.',
 OWNER_SCOPE_MISMATCH:'Chứng nhận thuộc chủ dữ liệu khác.',DATA_SCOPE_MISMATCH:'Phạm vi dữ liệu không khớp chứng nhận.',
};

export default function PairAutomation({blocked}:{blocked:boolean}) {
 const {workflow:w}=useWorkflow(),action=useWorkflowAction(),[confirm,setConfirm]=useState<{id:string;token:string}|null>(null);
 const op=w.latest_automation_operation,plan=op?.operation_type==='AUTOMATION_SHADOW'&&op.status==='COMPLETED'?op.result as Partial<AutomationPlan>:null;
 const current=!!plan&&plan.source_decision_revision===w.decision_revision&&plan.source_assessment_id===w.assessment_id;
 const canApply=current&&plan?.can_apply&&!w.automation_stopped&&w.allowed_actions.includes('automation-apply')&&!blocked&&!action.busy&&!w.active_operation;
 const ready=w.workflow_state==='DECIDE_REVIEW'&&!!w.assessment_id;
 if(!ready)return null;
 async function shadow(){setConfirm(null);await action.run(()=>shadowAutomation(w.run_id,{...mutation(w),expected_decision_revision:w.decision_revision}));}
 async function apply(){if(!confirm||!canApply||confirm.id!==op?.operation_id||confirm.token!==plan?.preview_token)return;const result=await action.run(()=>applyAutomation(w.run_id,{...mutation(w),expected_decision_revision:w.decision_revision,preview_operation_id:confirm.id,preview_token:confirm.token}));if(result)setConfirm(null);}
 return <section className={styles.assessmentPanel} aria-label="Tự động hóa có kiểm soát"><div><h3>Tự động hóa có kiểm soát</h3>
 <p>Mục tiêu chính xác 99,5% cho vùng đã kiểm định. Chạy thử chỉ tính kế hoạch, giữ nguyên quyết định.</p>
 {w.automation_stopped?<p>Đã dừng tự động hóa phiên này.</p>:plan&&<><p>{plan.candidate_components??0} cụm có bằng chứng phù hợp · {plan.qualified_components??0} cụm được phép áp dụng · {plan.applicable_pairs??0} cặp.</p><p>Còn tối đa {plan.remaining_component_budget??0} cụm trong lượt thử nghiệm của phiên.</p>{!current&&<p role="alert">Phiên đã thay đổi; chạy thử lại trước khi áp dụng.</p>}{plan.blocked_reasons?.map(reason=><p key={reason}>{reasons[reason]||'Chưa đủ điều kiện chứng nhận; cần kiểm tra lại chất lượng và phạm vi dữ liệu.'}</p>)}</>}
 {op?.operation_type==='AUTOMATION_APPLY'&&op.status==='COMPLETED'&&<p>Đã áp dụng tự động {Number(op.result.applied_components)||0} cụm · {Number(op.result.applied_pairs)||0} cặp. Bạn vẫn có thể đối chiếu và sửa quyết định.</p>}
 {w.active_operation?.operation_type.startsWith('AUTOMATION_')&&<p role="status">Đang xử lý tự động hóa trên server.</p>}
 {op?.status==='FAILED'&&<p role="alert">Tác vụ chưa hoàn tất. Đối chiếu phiên và chạy thử lại trước khi áp dụng.</p>}
 <p>Các quyết định đã lưu được giữ lại khi dừng. Việc tạo nhóm và xuất dữ liệu vẫn theo quy trình hiện có.</p>
 {action.error&&<p role="alert">{action.error}</p>}</div><div className={styles.actionButtons}>
 <button disabled={blocked||action.busy||!w.allowed_actions.includes('automation-shadow')} onClick={()=>void shadow()}>Chạy thử tự động hóa</button>
 <button disabled={!canApply} onClick={()=>op&&plan?.preview_token&&setConfirm({id:op.operation_id,token:plan.preview_token})}>Áp dụng tự động</button>
 <button disabled={action.busy||w.automation_stopped||!w.allowed_actions.includes('automation-stop')} onClick={()=>void action.run(()=>stopAutomation(w.run_id,mutation(w)))}>Dừng tự động hóa phiên này</button>
 {confirm&&<div role="dialog" aria-label="Xác nhận áp dụng tự động"><p>Áp dụng kế hoạch đã kiểm định cho {plan?.qualified_components??0} cụm, {plan?.applicable_pairs??0} cặp. Các cặp còn lại tiếp tục cần người duyệt.</p><button disabled={!canApply||confirm.id!==op?.operation_id||confirm.token!==plan?.preview_token} onClick={()=>void apply()}>Xác nhận áp dụng {plan?.qualified_components??0} cụm</button><button onClick={()=>setConfirm(null)}>Đóng</button></div>}
 </div></section>;
}
