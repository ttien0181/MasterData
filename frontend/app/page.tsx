import Link from "next/link";
import LandingActions from "@/components/LandingActions";
import ThemeToggle from "@/components/ThemeToggle";
import "./landing.css";

export default function Home() {
  return <main className="landing">
    <header className="landing-nav"><Link href="/" className="landing-brand"><span>M</span><strong>Master<span>One</span></strong></Link><nav aria-label="Điều hướng trang chủ"><a href="#quy-trinh">Quy trình</a><a href="#kiem-soat">Kiểm soát</a><ThemeToggle className="landing-theme-toggle" /><LandingActions compact /></nav></header>
    <section className="landing-hero">
      <div className="landing-copy"><p className="landing-eyebrow"><span /> NỀN TẢNG PHÂN GIẢI DỮ LIỆU KHÁCH HÀNG</p><h1>Một góc nhìn rõ ràng<br />cho mọi bản ghi <em>khách hàng.</em></h1><p className="landing-lead">Từ những file CSV rời rạc đến danh sách cần kiểm tra, cụm chờ gộp và một bản dữ liệu cuối cùng có thể truy vết.</p><LandingActions /><div className="landing-hero-note">Nạp dữ liệu · Đối chiếu · Duyệt · Xuất CSV</div></div>
      <div className="landing-visual" aria-label="Minh họa các bản ghi nguồn được đối chiếu và gộp"><div className="landing-visual-top"><span>MASTERONE / RESOLUTION</span><span className="landing-live"><i /> WORKSPACE</span></div><div className="landing-visual-body"><div className="landing-source"><b>01</b><span>CRM</span><strong>Nguyễn Minh Anh</strong><small>Hồ sơ khách hàng</small></div><div className="landing-source"><b>02</b><span>ERP</span><strong>Nguyễn M. Anh</strong><small>Hồ sơ giao dịch</small></div><div className="landing-source"><b>03</b><span>BILL</span><strong>Nguyễn Minh Anh</strong><small>Hồ sơ thanh toán</small></div><div className="landing-visual-line" /><div className="landing-result"><span>ĐỐI CHIẾU CÓ KIỂM SOÁT</span><strong>Một khách hàng.<br />Thông tin được chọn rõ ràng.</strong><div><i /> Từng quyết định đều có thể xem lại</div></div></div><div className="landing-visual-bottom"><span>CSV → REVIEW → GOLDEN RECORD</span><span>● ● ●</span></div></div>
    </section>
    <section className="landing-section" id="quy-trinh"><div className="landing-section-head"><p>QUY TRÌNH</p><h2>Mọi quyết định ở đúng chỗ.</h2><span>Giữ nhịp làm việc từ lúc nạp file đến lúc xuất dữ liệu cuối.</span></div><div className="landing-steps"><article><b>01 / NẠP DỮ LIỆU</b><h3>Đưa nhiều nguồn vào cùng một lần nạp</h3><p>Đọc và đối chiếu các bản ghi trong cùng nguồn hoặc giữa các nguồn, không giới hạn vào một cặp file.</p></article><article><b>02 / CẦN REVIEW</b><h3>Đưa nghi vấn cho người phụ trách</h3><p>Xem từng cụm, so sánh các trường và quyết định có đưa sang Chờ gộp hay giữ riêng.</p></article><article><b>03 / CHỜ GỘP</b><h3>Chọn giá trị, rồi xuất CSV</h3><p>Kiểm tra thông tin của từng cụm. Hệ thống chỉ cho xuất khi các lựa chọn bắt buộc đã hoàn tất.</p></article></div></section>
    <section className="landing-control" id="kiem-soat"><div><p>KIỂM SOÁT & TRUY VẾT</p><h2>Dữ liệu cuối cùng có lời giải thích.</h2><span>Những bản ghi chưa được duyệt vẫn được giữ riêng. Khi có xung đột, bạn thấy chính xác item cần xử lý trước khi xuất CSV.</span></div><LandingActions /></section>
    <footer className="landing-footer"><strong>MasterOne</strong><span>Customer data operations</span><span>© 2026 MasterOne</span></footer>
  </main>;
}
