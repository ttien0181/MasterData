export const sessionTabs = [{ id: 'data', label: 'Tổng quan dữ liệu' }, { id: 'decisions', label: 'Phân loại các cặp bản ghi' }, { id: 'clusters', label: 'Các nhóm bản ghi' }, { id: 'golden', label: 'Golden record' }] as const;
export type SessionTab = typeof sessionTabs[number]['id'];
export const sessionSections: Record<SessionTab, { id: string; label: string; path?: string }[]> = {
  data: [
    { id: 'data-overview', label: 'Phân tích dữ liệu', path: '' },
    { id: 'data-records', label: 'Dữ liệu tải lên', path: '/records' },
  ],
  decisions: [
    { id: 'decisions-summary', label: 'Tổng quan phân loại', path: '' },
    { id: 'decisions-pairs', label: 'Duyệt các cặp bản ghi', path: '/pairs' },
  ],
  clusters: [
    { id: 'clusters-overview', label: 'Tổng quan nhóm', path: '' },
    { id: 'clusters-list', label: 'Danh sách nhóm', path: '/records' },
  ],
  golden: [
    { id: 'golden-overview', label: 'Tổng quan Golden' },
    { id: 'golden-records', label: 'Giá trị và nguồn gốc' },
    { id: 'golden-export', label: 'Chốt và xuất dữ liệu' },
  ],
};
export const sessionPath = (run: string, tab: SessionTab = 'data') => `/app/sessions/${encodeURIComponent(run)}/${tab}`;
export function tabReadiness(state: string, tab: SessionTab, clusterRevision = 0) {
  const ready = tab === 'data' || tab === 'clusters' && ['DECIDE_REVIEW','CLUSTER_QUEUED','CLUSTER_RUNNING'].includes(state) || tab === 'decisions' && ['DECIDE_REVIEW', 'CLUSTER_QUEUED', 'CLUSTER_RUNNING', 'GOLDEN_REVIEW', 'FINALIZED'].includes(state) || ['clusters', 'golden'].includes(tab) && (['GOLDEN_REVIEW', 'FINALIZED'].includes(state) || ['DECIDE_REVIEW','CLUSTER_QUEUED','CLUSTER_RUNNING'].includes(state) && clusterRevision > 0);
  return { ready, reason: ready ? '' : tab === 'decisions' ? 'Bắt đầu phân loại sau khi chuẩn bị dữ liệu xong.' : 'Hoàn tất phân loại và chờ tạo nhóm thành công trước khi xem kết quả.' };
}
