export const sessionTabs = [{ id: 'data', label: 'Tổng quan dữ liệu' }, { id: 'decisions', label: 'Phân loại các cặp bản ghi' }, { id: 'clusters', label: 'Các nhóm bản ghi' }, { id: 'golden', label: 'Golden record' }] as const;
export type SessionTab = typeof sessionTabs[number]['id'];
export const sessionPath = (run: string, tab: SessionTab = 'data') => `/app/sessions/${encodeURIComponent(run)}/${tab}`;
export function tabReadiness(state: string, tab: SessionTab, clusterRevision = 0) {
  const ready = tab === 'data' || tab === 'decisions' && ['DECIDE_REVIEW', 'CLUSTER_QUEUED', 'CLUSTER_RUNNING', 'GOLDEN_REVIEW', 'FINALIZED'].includes(state) || ['clusters', 'golden'].includes(tab) && (['GOLDEN_REVIEW', 'FINALIZED'].includes(state) || ['DECIDE_REVIEW','CLUSTER_QUEUED','CLUSTER_RUNNING'].includes(state) && clusterRevision > 0);
  return { ready, reason: ready ? '' : tab === 'decisions' ? 'Bắt đầu phân loại sau khi chuẩn bị dữ liệu xong.' : 'Hoàn tất phân loại và chờ tạo nhóm thành công trước khi xem kết quả.' };
}
