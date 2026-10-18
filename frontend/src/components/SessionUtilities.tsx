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
  return <section className="workflow-panel"><h2>Lịch sử và quy định</h2><h3>Các tệp đã xuất</h3>{action.error && <p role="alert">{action.error}</p>}
    {!exports.data ? <ReadNotice error={exports.error} retry={exports.retry} /> : exports.data.items.length ? exports.data.items.map(e => <p key={e.id}>{new Date(e.created_at).toLocaleString('vi-VN',{timeZone:'Asia/Bangkok'})} · {e.row_count} dòng <button disabled={action.busy || !can(user, 'export_data')} onClick={() => void action.run(() => downloadResolutionExport(w.run_id, e.id))}>Tải lại CSV</button></p>) : <p>Chưa có tệp đã xuất.</p>}
    <h3>Lịch sử thao tác</h3>{can(user, 'read_audit') && <AuditTrail runId={w.run_id} />}
    <h3>Tách và gộp lại nhóm</h3><p>Bạn có thể tách hoặc gộp lại các nhóm ở bước 3 khi có quyền phù hợp. Hãy xem trước ảnh hưởng trước khi xác nhận thay đổi.</p>
    <h3>Chính sách</h3><button onClick={async () => { const result = await action.run(getPolicyYaml); if (result) setPolicy(result.yaml_content); }}>Xem chính sách hiện tại</button>{policy && <pre>{policy}</pre>}<p>Các quy định hiện tại được hiển thị để tham khảo. Phiên này chưa hỗ trợ mô phỏng thay đổi quy định.</p>
  </section>;
}
