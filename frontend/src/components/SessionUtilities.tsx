'use client';
import { useCallback, useState } from 'react';
import { downloadResolutionExport, getPolicyYaml, getResolutionExports } from '@/lib/api';
import { can } from '@/lib/permissions';
import { useAppUser } from './AppSession';
import { useWorkflow } from './SessionWorkspace';
import { useWorkflowAction } from './useWorkflowAction';
import { ReadNotice, useWorkflowRead } from './WorkflowReads';
import AuditTrail from './AuditTrail';
export default function SessionUtilities() {
  const { workflow: w } = useWorkflow(); const user = useAppUser(); const action = useWorkflowAction(); const [policy, setPolicy] = useState('');
  const load = useCallback(() => getResolutionExports(w.run_id), [w.run_id]); const exports = useWorkflowRead(load);
  return <section className="workflow-panel"><h2>Tiện ích phiên</h2><h3>Lịch sử xuất</h3>{action.error && <p role="alert">{action.error}</p>}
    {!exports.data ? <ReadNotice error={exports.error} retry={exports.retry} /> : exports.data.items.length ? exports.data.items.map(e => <p key={e.id}>{e.created_at} · {e.row_count} dòng <button disabled={action.busy || !can(user, 'export_data')} onClick={() => void action.run(() => downloadResolutionExport(w.run_id, e.id))}>Tải CSV lịch sử {e.id}</button></p>) : <p>Chưa có file đã xuất.</p>}
    <h3>Audit</h3>{can(user, 'read_audit') && <AuditTrail runId={w.run_id} />}
    <h3>Split</h3><p>Phiên workflow v2 chưa hỗ trợ split kết quả. Có thể mở lại phân loại qua thao tác có lý do và quyền phù hợp.</p>
    <h3>Chính sách</h3><button onClick={async () => { const result = await action.run(getPolicyYaml); if (result) setPolicy(result.yaml_content); }}>Xem chính sách hiện tại</button>{policy && <pre>{policy}</pre>}<p>Chính sách hiển thị để tham khảo; mô phỏng không sẵn sàng trên workflow v2.</p>
  </section>;
}
