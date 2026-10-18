import type { Stage } from "./AppShell";

const pageCopy: Record<Stage, { title: string; description: string }> = {
  overview: {
    title: "Tổng quan dữ liệu",
    description: "Theo dõi dữ liệu đầu vào, các đề xuất gộp và công việc đang chờ.",
  },
  import: {
    title: "Nạp dữ liệu",
    description:
      "Tải một hoặc nhiều file để tìm dòng thông tin trùng trên toàn bộ dữ liệu.",
  },
  auto: {
    title: "Chờ gộp",
    description:
      "Danh sách gồm đề xuất tự động và các cặp bạn đã chọn hoặc xác nhận. Xem trước file kết quả để kiểm tra các nhóm trước khi gộp và xuất CSV.",
  },
  review: {
    title: "Cặp cần xem xét",
    description:
      "Lọc theo nhóm lý do, so sánh từng cặp nghi trùng và xác nhận gộp hoặc không gộp.",
  },
  no_merge: {
    title: "Không gộp",
    description:
      "Các cặp được xác định khác nhau hoặc bạn đã từ chối gộp. Dòng thông tin nguồn vẫn được giữ lại.",
  },
  history: {
    title: "Lịch sử xuất",
    description:
      "Tải lại CSV và xem các dòng thông tin trước, sau xử lý trong lần nạp này.",
  },
  audit: {
    title: "Lịch sử thao tác",
    description: "Xem các thao tác đã được ghi nhận trong đợt xử lý này.",
  },
  policy: {
    title: "Chính sách",
    description:
      "Cấu hình quy tắc khớp, điểm số và các tham số đối chiếu tự động.",
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
