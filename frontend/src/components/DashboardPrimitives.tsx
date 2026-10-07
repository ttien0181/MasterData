import type { Stage } from "./AppShell";

const pageCopy: Record<Stage, { title: string; description: string }> = {
  overview: { title: "Tổng quan dữ liệu", description: "Theo dõi các đề xuất phân giải và hoạt động gần đây." },
  import: { title: "Nạp dữ liệu", description: "Tải một hoặc nhiều file để tìm bản ghi trùng trên toàn bộ dữ liệu." },
  auto: { title: "Tự động gộp", description: "Các bản ghi trùng được gom thành một hồ sơ. Danh sách gồm kết quả tự động và kết quả bạn đã xác nhận; kiểm tra thông tin trước khi gộp và xuất CSV." },
  review: { title: "Cần xem xét", description: "Lọc theo nhóm lý do, so sánh từng cặp bản ghi và xác nhận gộp hoặc không gộp." },
  no_merge: { title: "Không gộp", description: "Các cặp được xác định khác nhau hoặc bạn đã từ chối gộp. Bản ghi nguồn vẫn được giữ lại." },
  history: { title: "Lịch sử", description: "Tải lại CSV và xem các bản ghi trước, sau xử lý trong lần nạp này." },
  audit: { title: "Audit phiên", description: "Xem các thao tác đã được ghi nhận trong phiên phân giải này." },
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
