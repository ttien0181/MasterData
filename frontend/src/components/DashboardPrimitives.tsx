import type { Stage } from "./AppShell";

const pageCopy: Record<Stage, { title: string; description: string }> = {
  overview: {
    title: "Tổng quan dữ liệu",
    description: "Theo dõi các đề xuất phân giải và hoạt động gần đây.",
  },
  import: {
    title: "Nạp dữ liệu",
    description:
      "Tải một hoặc nhiều tệp để tìm hồ sơ trùng trên toàn bộ dữ liệu.",
  },
  auto: {
    title: "Tự động gộp",
    description:
      "Các hồ sơ trùng được gom thành một hồ sơ. Danh sách gồm kết quả tự động và kết quả bạn đã xác nhận; kiểm tra thông tin trước khi gộp và xuất CSV.",
  },
  review: {
    title: "Cần xem xét",
    description:
      "Lọc theo nhóm lý do, so sánh từng cặp hồ sơ và xác nhận gộp hoặc không gộp.",
  },
  no_merge: {
    title: "Không gộp",
    description:
      "Các cặp được xác định khác nhau hoặc bạn đã từ chối gộp. Hồ sơ nguồn vẫn được giữ lại.",
  },
  history: {
    title: "Lịch sử",
    description:
      "Tải lại CSV và xem các hồ sơ trước, sau xử lý trong lần nạp này.",
  },
  audit: {
    title: "Lịch sử thao tác trong phiên",
    description: "Xem các thao tác đã được ghi nhận trong phiên phân giải này.",
  },
  policy: {
    title: "Chính sách",
    description:
      "Cấu hình quy tắc khớp, điểm số và các tham số phân giải tự động.",
  },
};

export function PageHeader({ stage }: { stage: Stage }) {
  const copy = pageCopy[stage];
  return (
    <div className="page-header">
      <div>
        <h1 className="page-title">{copy.title}</h1>
        <p className="page-desc">{copy.description}</p>
      </div>
    </div>
  );
}
