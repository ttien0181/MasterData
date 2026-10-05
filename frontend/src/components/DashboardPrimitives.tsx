import type { Stage } from "./AppShell";

const pageCopy: Record<Stage, { title: string; description: string }> = {
  overview: { title: "Tổng quan dữ liệu", description: "Theo dõi các đề xuất phân giải và hoạt động gần đây." },
  import: { title: "Nạp dữ liệu", description: "Tải một hoặc nhiều file để tìm bản ghi trùng trên toàn bộ dữ liệu." },
  auto: { title: "Chờ gộp", description: "Kiểm tra các cụm đã được đề xuất hoặc bạn đã duyệt trước khi xuất CSV." },
  review: { title: "Cần review", description: "Đối chiếu các bản ghi cần người xác nhận trước khi quyết định gộp hay không." },
  history: { title: "Lịch sử", description: "Tải lại CSV và xem các bản ghi trước, sau xử lý trong lần nạp này." },
};

export function PageHeader({ stage }: { stage: Stage }) {
  const copy = pageCopy[stage];
  return <div className="page-header">
    <div>
      <h1 className="page-title">{copy.title}</h1>
      <p className="page-desc">{copy.description}</p>
    </div>
  </div>;
}
