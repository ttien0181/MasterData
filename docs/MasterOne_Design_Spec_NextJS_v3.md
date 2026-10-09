# MasterOne — Đặc tả thiết kế và hướng dẫn triển khai (Next.js)

> Tài liệu này đi kèm `MasterOne_UI_UX_Session_Centric_Concept_v2.md`. Bản 2 mô tả **luồng và hành vi**. Tài liệu này mô tả **giao diện cụ thể** (màu, font, kích thước, thành phần) và **cách triển khai bằng Next.js**.

> **V3 — điều chỉnh theo repo và quyết định người dùng (2026-10-07).** Giữ toàn bộ ngôn ngữ hình ảnh, tokens, typography, Mo và phong cách mockup làm chuẩn. Các khối ghi “V3” thay thế nội dung mâu thuẫn bên dưới. Backend hiện tại là nguồn sự thật cho data/status/permission; những API chưa triển khai được ghi là phần sau. Tên sản phẩm: **Phiên phân giải**. GĐ0–3 đã triển khai; CP08 đã kiểm chứng luồng nhiều file; CP09 đã dựng review ba cột; Audit có API/UI cơ bản. Retention, compare/series, export nền và nghiệm thu tổng thể còn lại.

---

## 0. Cách dùng bộ tài liệu

### 0.1. Thứ tự ưu tiên khi các nguồn mâu thuẫn

1. **Tài liệu này** (mục 14 liệt kê các điểm điều chỉnh so với bản 2).
2. **File HTML tham chiếu** cho hình ảnh: bố cục, màu, khoảng cách, trạng thái.
3. **Bản 2** cho hành vi, quy tắc nghiệp vụ, phân quyền.

### 0.2. File HTML tham chiếu

Các file do người dùng cung cấp hiện ở `D:\Downloads`; chưa được chép vào repo. Mockup là nguồn chuẩn về thị giác, không phải nguồn chuẩn về nghiệp vụ. Trong lượt audit, markup đã được rà tĩnh nhưng chưa render bằng browser; trước vòng visual QA cần mở/render từng mockup cạnh ứng dụng.

| File | Nội dung |
|---|---|
| `masterone_ui_light.html` | Main Workspace, Sessions Hub, Review ba cột |
| `masterone_upload_sessions_light.html` | Drawer upload và panel phiên |
| `masterone_upload_replace_light.html` | Replace, file lỗi, draft indicator |
| `masterone_login_light (1).html` | Login |
| `Mo - linh vật MasterOne.html` (tham chiếu trước đó) | Mo, logo và biểu tượng |

### 0.3. Lưu ý quan trọng khi đọc file tham chiếu

- Các file HTML là **bản mô phỏng tĩnh**: khung cố định 1180×690, số liệu giả, dock và top bar được chèn bằng JS. **Không copy làm code sản phẩm.**
- Ứng dụng thật phải **co giãn theo viewport** (xem mục 6.4), không cố định 1180px.
- **V3:** cả light và dark mode đều thuộc phạm vi. Light giữ đúng palette mockup; dark dùng semantic tokens tương đương và phải được QA riêng.

---

## 1. Công nghệ

| Hạng mục | Lựa chọn | Ghi chú |
|---|---|---|
| Framework | Next.js App Router `^16.3.6`, React `^19.3.0`, TypeScript | **V3:** giữ phiên bản và router trong repo; không khởi tạo app mới |
| Style | CSS thuần theo `frontend/app/*.css` và semantic CSS variables | Không thêm Tailwind; tokens được giữ theo mục 3 |
| Font | **Be Vietnam Pro** (400, 500, 600, 700), Vietnamese + Latin | **V3:** chuẩn typography của mockup; thay font Source Sans 3 hiện dùng trong app |
| Primitives | React và HTML hiện có | Tái sử dụng component/repo convention; không thêm Radix chỉ để đổi stack |
| Icon | Inline SVG qua `ShellIcons.tsx` và icon hiện có | Nét tròn, nhất quán với mockup; không cài icon package mới trong GĐ0–1 |
| Biểu đồ | SVG/CSS khi metric thật có trong API | Trend chart chỉ sau backend compare/series contract và GĐ sau |
| State giao diện | React state/hooks hiện có | Panel/draft lớn có thể tách hook/context khi đến feature đó; chưa thêm Zustand |
| Dữ liệu server | `frontend/src/lib/api.ts` fetch wrapper | Poll overview hiện 900ms khi run queued/running; không có SSE cho ResolutionRun hiện tại |
| Bảng, danh sách dài | API offset pagination hiện có (`limit` tối đa 100) | User đã yêu cầu query/bulk backend và nhiều triệu records; chốt backend pagination/cursor + virtualization ở giai đoạn sau |
| Form | HTML form + React state hiện có | Client validation trợ giúp; backend là nguồn quyết định |
| Upload | Hiện tại multipart CSV, giới hạn nhỏ | **V3 target:** staged draft/upload session, replace an toàn, resume/chunk và progress sự kiện; backend design/scale benchmark ở giai đoạn sau |
| Xác thực | Backend cookie session, CSRF, capabilities; email/password hiện có | **V3 target:** SSO/OIDC và password recovery cần provider/workflow contract; health indicator dùng `/health` hiện có rồi xác định health semantics |
| Kiểm thử giao diện | Vitest + Testing Library hiện có | Kiểm tra typecheck/test; visual browser comparison là checkpoint QA, không tuyên bố hoàn tất nếu chưa render |

> **V3:** các đề xuất thư viện ở tài liệu gốc không phải yêu cầu cài đặt. Theo kiến trúc repo hiện có; chỉ thêm thư viện nếu một giai đoạn được duyệt chứng minh cần thiết. Giữ tokens và mockup.

---

## 2. Cấu trúc thư mục

**Cấu trúc V3 trong repo:** `frontend/app/` chứa App Router (`login`, `register`, `app`); `frontend/src/components/` chứa shell, auth, workspace và feature components; `frontend/src/lib/` chứa API/contracts/permissions/adapters; `frontend/app/themes.css`, `globals.css`, `dashboard.css`, `glass.css`, `landing.css` chứa styles hiện có. Không tạo `stores/`, `mocks/` hay route group song song nếu chưa cần.

**Deep link V3:** giữ pattern đang dùng `?run=<id>&view=<stage>` trong `ResolutionWorkspace.tsx` (overview/import/auto/review/history) cho tương thích. Mọi route mới phải thiết kế sau này, không giả sử `/sessions/:id/...` đã tồn tại.

---

## 3. Design tokens

### 3.1. Màu

| Token | Giá trị | Dùng cho |
|---|---|---|
| `canvas` | `#e7eef5` | Nền trang bao ngoài |
| `canvas-from` → `canvas-to` | `#f6f9fd` → `#e5edf5` (gradient 160°) | Nền workspace |
| `surface` | `#ffffff` | Thẻ, panel, dock, thanh |
| `surface-subtle` | `#f3f7fb` / `#f8fafc` | Ô con trong thẻ, input nền |
| `line` | `#e0e8f1` | Viền thẻ, đường kẻ (input đăng nhập dùng `#d9e3ee`) |
| `ink` | `#0f2137` | Chữ chính, màn hình của Mo |
| `muted` | `#64748b` | Chữ phụ, nhãn |
| `accent` | `#0891b2` | Màu thương hiệu, nút chính, mục đang chọn |
| `accent-strong` | `#0e7490` | Hover, tai/tay/chân Mo |
| `accent-soft` | `#e0f7fb` | Nền nhấn nhạt, chip, dòng được chọn |
| `accent-line` | `#9fdde9` / `#b8eaf2` | Viền của nền nhấn nhạt |
| `danger` / `danger-soft` | `#e11d48` / `#fff1f3` | Lỗi, nghiêm trọng |
| `warn` / `warn-soft` | `#d97706` / `#fff6e3` (viền `#fbdca0`) | Cần review, cảnh báo |
| `ok` / `ok-soft` | `#059669` / `#e8f8f1` | Hoàn tất, hợp lệ |
| `gold` | `#f5b400` | Golden Record, ăng-ten Mo |
| Màu biểu đồ phụ | `#38bdf8`, `#7dd3fc`, `#94a3b8` | Nguồn thứ 2, 3; "Không trùng" |

Quy tắc dùng màu trạng thái: **luôn kèm icon hoặc chữ**, không dùng màu làm tín hiệu duy nhất.

**V3 — hai theme:** light mode dùng đúng màu trong bảng trên. Dark mode giữ cùng semantic token names (`canvas`, `surface`, `ink`, `muted`, `line`, `accent`, `warn`, `danger`, `ok`, `gold`) nhưng đổi giá trị để đọc rõ trên nền tối. Theme được đặt bằng `html[data-theme="light|dark"]`, lưu lựa chọn người dùng và tôn trọng `prefers-color-scheme` khi chưa có lựa chọn. Không đặt màu trực tiếp trong feature CSS nếu đã có token.

### 3.2. Bo góc

| Dùng cho | Giá trị |
|---|---|
| Thẻ (card) | 14px |
| Khung màn hình, drawer, panel | 18px |
| Pill top bar | 16px |
| Dock | 20px, mục trong dock 14px |
| Input, nút | 12px (input nhỏ 10px) |
| Chip / badge trạng thái | 20px (badge tròn), 9px (chip vuông nhỏ) |
| Thanh tiến độ | 4px (cao 8px), 5px (cao 10px) |
| Nút tròn | 50% |

### 3.3. Đổ bóng

| Token | Giá trị |
|---|---|
| `shadow-card` | `0 6px 20px rgba(15,33,55,.08)` |
| `shadow-float` (panel, drawer trượt) | `0 14px 40px rgba(15,33,55,.16)` |
| `shadow-login` | `0 20px 50px rgba(15,33,55,.12)` |
| Nút chính | `0 8px 18px rgba(8,145,178,.28)` |
| Vòng chọn | `0 0 0 3px` hoặc `4px` màu `accent-soft` (dòng được chọn, focus input) |

### 3.4. Khai báo trong repo (CSS variables)

```css
/* frontend/app/themes.css — repo source of truth for theme values */
:root, html[data-theme="light"] {
  color-scheme: light;
  --bg: #e7eef5; --surface: #fff; --ink: #0f2137;
  --ink-3: #64748b; --line: #e0e8f1;
  --primary: #0891b2; --primary-hover: #0e7490;
  --primary-bg: #e0f7fb; --amber: #d97706;
  --green: #059669; --red: #e11d48;
}
html[data-theme="dark"] {
  color-scheme: dark;
  --bg: #0b1526; --surface: #152844; --ink: #edf3ff;
  --ink-3: #b5c6df; --line: rgb(222 236 255 / 20%);
  --primary: #22d3ee; --primary-hover: #67e8f9;
  --primary-bg: #164e63; --amber: #ffd282;
  --green: #80e5b3; --red: #ff9398;
}
```

**V3:** đây là ví dụ semantic tối thiểu, không phải file mới. Giữ các token đầy đủ hiện có trong `frontend/app/themes.css`; light accent phải khớp `#0891b2`. Các lớp `dashboard.css`, `glass.css`, `landing.css` dùng biến này, không thêm Tailwind `@theme`.

Số liệu (KPI, bảng, tiến độ) dùng `tabular-nums` để không rung khi cập nhật.

---

## 4. Typography

Font: **Be Vietnam Pro** (400, 500, 600, 700; Vietnamese + Latin), dùng qua `next/font/google` và CSS variable theo convention hiện tại. Dấu tiếng Việt phải hiển thị đúng ở mọi cỡ. Nguồn trong repo hiện là Source Sans 3; GĐ0 chuyển sang font tham chiếu và xác nhận build/font loading.

| Vai trò | Cỡ / độ đậm | Ví dụ |
|---|---|---|
| Tiêu đề lớn (đăng nhập, headline) | 34px / 700, line-height 1.18 | "Một nguồn dữ liệu. Một sự thật." |
| Tiêu đề màn hình, Hub | 22px / 700 | "Phiên phân tích" |
| Tiêu đề form | 27px / 700 | "Đăng nhập" |
| Tiêu đề drawer, panel | 17px / 700 | "Nạp dữ liệu" |
| Số KPI | 26px / 700 | `124.380` |
| Tên thực thể, tên phiên | 13-14px / 600-700 | |
| Nội dung | 13px / 400-500 | |
| Chữ phụ, meta | 11-12px / 400 | `CRM + ERP · 06/10 09:31` |
| **Nhãn mục (section label)** | **11px / 600, in hoa, giãn chữ `.08em`, màu `muted`** | "XEM NHANH" |
| Nút | 14-15px / 600 | |

Không dùng cỡ chữ nhỏ hơn 11px. Định dạng số theo Việt Nam: `124.380`, phần thập phân dùng dấu phẩy (`0,93`).

---

## 5. Hình ảnh thương hiệu

### 5.1. Logo
Đầu Mo (viewBox `26 10 148 140`) + chữ "MasterOne" 21-28px / 700, `letter-spacing: -.01em`. Có biến thể nền sáng và nền tối (thân Mo đổi qua biến `--mo-body`, `--mo-stroke`).

### 5.2. Linh vật Mo
**V3:** repo đặt component Mo tại `frontend/src/components/MoMark.tsx`. Tái sử dụng component này hoặc bổ sung hình thể từ mockup vào cùng convention; không tạo `components/brand/` song song. Chỉ dùng Mo cho nhận diện thương hiệu: logo, login, favicon/avatar; không biến Mo thành status indicator.

```tsx
// components/brand/Mo.tsx
export function Mo({ className, headOnly = false }: { className?: string; headOnly?: boolean }) {
  return (
    <svg
      viewBox={headOnly ? "26 10 148 140" : "0 0 200 240"}
      className={className}
      role="img"
      aria-label="Mo, linh vật MasterOne"
    >
      <ellipse cx="100" cy="229" rx="42" ry="5" fill="#0f2137" opacity=".1" />
      <ellipse cx="82" cy="217" rx="16" ry="7" fill="#0e7490" />
      <ellipse cx="118" cy="217" rx="16" ry="7" fill="#0e7490" />
      <ellipse cx="46" cy="178" rx="9" ry="15" fill="#0e7490" transform="rotate(12 46 178)" />
      <ellipse cx="154" cy="178" rx="9" ry="15" fill="#0e7490" transform="rotate(-12 154 178)" />
      <rect x="58" y="148" width="84" height="64" rx="28" strokeWidth="5"
        style={{ fill: "var(--mo-body, #e0f7fb)", stroke: "var(--mo-stroke, #0891b2)" }} />
      <circle cx="92" cy="180" r="11" fill="none" strokeWidth="3.5" style={{ stroke: "var(--mo-stroke, #0891b2)" }} />
      <circle cx="108" cy="180" r="11" fill="none" strokeWidth="3.5" style={{ stroke: "var(--mo-stroke, #0891b2)" }} />
      <circle cx="100" cy="180" r="4.5" fill="#f5b400" />
      <rect x="88" y="138" width="24" height="14" rx="5" fill="#0e7490" />
      <rect x="29" y="84" width="14" height="30" rx="7" fill="#0e7490" />
      <rect x="157" y="84" width="14" height="30" rx="7" fill="#0e7490" />
      <path d="M100 52V32" stroke="#0891b2" strokeWidth="5" strokeLinecap="round" />
      <circle cx="100" cy="24" r="10" fill="#f5b400" />
      <circle cx="96.5" cy="20.5" r="3" fill="#fff" opacity=".7" />
      <rect x="38" y="50" width="124" height="94" rx="36" fill="#0891b2" />
      <rect x="51" y="64" width="98" height="64" rx="26" fill="#0f2137" />
      <ellipse className="mo-eye" cx="80" cy="94" rx="8" ry="11" fill="#22d3ee" />
      <ellipse className="mo-eye" cx="120" cy="94" rx="8" ry="11" fill="#22d3ee" />
      <circle cx="83" cy="89" r="3" fill="#fff" />
      <circle cx="123" cy="89" r="3" fill="#fff" />
      <path d="M89 113q11 9 22 0" fill="none" stroke="#22d3ee" strokeWidth="3.5" strokeLinecap="round" />
    </svg>
  );
}
```

```css
.mo-eye { transform-box: fill-box; transform-origin: center; animation: mo-blink 5s infinite; }
@keyframes mo-blink { 0%,94%,100% { transform: scaleY(1) } 97% { transform: scaleY(.1) } }
@media (prefers-reduced-motion: reduce) { .mo-eye { animation: none } }
```

Ý nghĩa: ăng-ten vàng = Golden Record; hai vòng giao nhau ở ngực, giao điểm vàng = hợp nhất bản ghi trùng; màn hình tối, mắt cyan = nhìn kỹ dữ liệu. Biến thể nền tối: `--mo-body: #123049; --mo-stroke: #22d3ee`. Favicon dùng `headOnly`, tối thiểu 16px.

---

## 6. Khung ứng dụng (App Shell)

### 6.1. Các lớp (từ dưới lên)

| z-index | Lớp |
|---|---|
| 0 | Workspace (nội dung phiên) trên nền gradient |
| 10 | Top bar, hai nút tròn, dock, thanh trạng thái |
| 30 | Panel phiên, Drawer nạp dữ liệu |
| 40 | Sessions Hub, Comparison (toàn màn hình) |
| 50 | Dialog (Export), popover, toast |

### 6.2. Top bar
Hai khối nổi, cách mép trên 14px, cách hai bên 16px.
- **V3 — Khối trái**: giữ style mockup; hiển thị logo Mo + MasterOne, tên phiên phân giải đã lưu, nguồn/file và status lấy từ API. Nếu chưa có phiên, dùng empty label; không hiển thị ngày/số liệu mẫu.
- **V3 — Khối phải**: theme toggle, role/email hoặc avatar và logout theo khả năng hiện tại. Chỉ hiển thị search/notifications nếu có chức năng/API thật.
- Chip phiên mở selector/Hub theo component đang có; khi Session panel được đưa vào, mở rộng đúng theo mockup mà không đổi URL contract hiện tại.
- Khi xem phiên cũ: chip đổi thành "Đang xem phiên cũ" + nút "Quay lại phiên hiện tại".

### 6.3. Nút tròn
46px, nền `surface`, viền **1.5px `accent`**, icon `accent`, bóng `shadow-card`, đặt ở **giữa chiều cao**, cách mép 16px, có chú thích 11px/600 màu `muted` bên dưới.
- **Trái:** icon upload, "Nạp dữ liệu". Chấm draft chỉ hiện khi backend trả một upload-session/draft còn mở.
- **Phải:** icon phiên, "Phiên". Mở danh sách phiên hiện có; mở panel/hub theo trạng thái component, không dùng dữ liệu mẫu.

### 6.4. Dock
- Nổi, **giữa đáy**, cách đáy 58px (trên thanh trạng thái). Nền `surface`, viền `line`, bo 20px, padding 6px, khoảng cách giữa mục 4px, bóng `shadow-card`.
- Mục: icon 16-18px + nhãn 13px/600, padding `10px 16px`, bo 14px. Mục thường: chữ `muted`. **Mục đang chọn: nền `accent`, chữ trắng.**
- Mục có số lượng hiển thị badge (nền `warn-soft`, chữ `warn`, 11px; trên mục đang chọn: nền trắng 25%, chữ trắng).
- **V3:** nội dung dock khớp góc nhìn có thật trong repo: Tổng quan, Nạp dữ liệu, Chờ gộp, Cần review, Lịch sử CSV. Không thêm tab Comparison đến khi backend mục 17 được xây.
- **Khi Panel phiên đang mở**, dock dịch sang trái để căn giữa phần màn hình còn lại: `left = (100% − 400px) / 2`.

### 6.5. Thanh trạng thái toàn cục
Cố định đáy, cao 46px, nền `surface`, viền trên `line`, padding ngang 22px.
- Nhãn "SỰ KIỆN / TRẠNG THÁI PHIÊN" 12px/700 in hoa.
- **V3:** status/phase và counters chỉ lấy từ run/overview thật. Hiển thị current run; không cộng status cho toàn bộ Hub nếu endpoint chỉ trả trang hiện tại.
- Phiên đang chạy: chấm cyan + tên + phase; xử lý là indeterminate nếu không có progress events có ý nghĩa. Không dùng percent giả.
- Phải: "Xem chi tiết ▾" màu `accent`, mở danh sách chi tiết (mỗi mục dẫn tới phiên và góc nhìn tương ứng).
- Theo dõi phiên hiện đang mở; tổng hợp toàn bộ phiên chỉ khi API query cung cấp số đếm toàn cục.

### 6.6. Responsive

| Độ rộng | Hành vi |
|---|---|
| ≥ 1280px | Đầy đủ như mockup |
| 1024-1279px | Panel phiên rộng 360px; dock rút gọn nhãn nếu chật (chỉ icon cho mục không chọn) |
| < 1024px | Panel, drawer, quick view chuyển thành **bottom sheet**; không dựa vào hover; thanh hành động hàng loạt cố định ở đáy |
| < 640px | Ưu tiên Tổng quan, Cần review (duyệt đơn giản), trạng thái phiên. So sánh nhiều phiên và export gộp có thể giới hạn |

---

## 7. Thành phần

Mọi giá trị lấy từ file HTML tương ứng. Bảng dưới là tóm tắt để agent tìm đúng chỗ.

### 7.1. Card
Nền `surface`, viền 1px `line`, bo 14px, padding `14px 16px`, bóng `shadow-card`.

### 7.2. KPI card
Số 26px/700 `tabular-nums`, nhãn 12px `muted` bên dưới. Lưới 4 cột, khoảng cách 14px. Hai biến thể nhấn:
- **V3 — metric thật:** record đầu vào, file, candidate pairs, merge proposals/items, review items, estimated separate records, exports, source counts. Mọi card ghi rõ đơn vị. Không hiển thị entity count, % chất lượng hoặc duplicates đã gộp vì API chưa có metric đó.
- Nhấn cyan/cam giữ theo token `accent-soft` / `warn-soft` để phân biệt queue; không gắn nhãn “đã merge” cho proposals đang chờ.

### 7.3. Biểu đồ tròn (Donut)
SVG viewBox 100×100, bán kính 38, **nét dày 14**, quay −90° để bắt đầu từ đỉnh. Vòng nền `#edf2f7`. Mỗi đoạn là một `circle` với `stroke-dasharray` và `stroke-dashoffset` âm cộng dồn (chu vi ≈ 238,76). Giữa vòng: phần trăm 17px/700 và nhãn 8px. Bên phải là chú giải: chấm vuông 10px bo 3px, nhãn 12px, số căn phải đậm.
Màu: dùng tokens semantic `ok`, `warn`, `muted`. **V3:** donut workspace chỉ có thể so số item chờ gộp (`counts.auto`) với số item cần review (`counts.review`), và phải gọi là phân bổ hàng đợi, không phải tỷ lệ chính xác. Không vẽ chart nếu status chưa COMPLETED hoặc tổng bằng 0.

### 7.4. Thanh ngang (HBar)
Lưới `54px 1fr 34px`: nhãn 12px, track cao 10px nền `#edf2f7` bo 5px, phần đầy `accent`, giá trị đậm căn phải. Khoảng cách dọc 9px.

### 7.5. Biểu đồ cột
Cột bo góc trên 8px, rộng 44px, giá trị đậm 12px phía trên, nhãn 11px phía dưới. Màu lần lượt `#0891b2`, `#38bdf8`, `#7dd3fc`. Cao tối đa khoảng 118px.

### 7.6. Biểu đồ đường xu hướng (Comparison)
Recharts, nét `accent` 2px, điểm tròn, lưới ngang nét đứt `line`, tooltip dạng card. Dùng ở So sánh (2-5 phiên) và Xu hướng chuỗi (mặc định 12 phiên gần nhất).

### 7.7. Badge / chip trạng thái
Bo 20px, padding `3px 10px`, chữ 12px/600. Bốn biến thể: `ok`, `danger`, `warn`, `accent` (nền soft + chữ đậm cùng tông). Badge phiên: "Hoàn tất", "317 cần review", "Lỗi tại Matching", "Đã lưu trữ" (nền `#eef3f8`, chữ `muted`).

### 7.8. Input
Cao 46px, bo 12px, viền `#d9e3ee`, nền `#fbfdff`, icon trái 18px màu `#8aa0b6`, padding trái 42px. **Focus:** viền `accent` + vòng 4px `accent-soft` + nền trắng. **Lỗi:** viền `danger`, nền `danger-soft`, thông báo 12px `danger` bên dưới (có `role="alert"`).

### 7.9. Nút
Cao 46-48px, bo 12px, chữ 14-15px/600.
- **Primary:** nền `accent`, chữ trắng, bóng nút chính, hover `accent-strong`.
- **Outline:** nền trắng, viền `line`.
- **Disabled:** nền `#edf1f6`, chữ `#94a3b8`.
- **Loading:** đổi nhãn "Đang xác thực...", khóa nút.

### 7.10. Checkbox
20px, bo 6px, viền 1.5px `#b7c4d4`; khi chọn: nền và viền `accent`, dấu ✓ trắng đậm. Là **vùng bấm riêng** trong dòng phiên (click dòng ≠ tick).

### 7.11. Thanh tiến độ
Cao 8px, bo 4px, nền `#e3eaf2`, phần đầy `accent`. Quy ước hiệu ứng ở mục 10.

### 7.12. Dòng phiên (SessionRow)
Padding `10-11px 12-14px`, viền `line`, bo 12px, khoảng cách 7-8px. Dòng đang xem: **viền 1.5px `accent` + vòng 3-4px `accent-soft`**. Phiên đang chạy: nền `#f2fbfd`, viền `#bfe9f1`, có progress indeterminate nếu không có số đo thật. Dòng: checkbox · **tên tùy chỉnh đã lưu** (fallback tạm là filename nếu chưa có name) + meta thật · badge status.

### 7.13. Panel phiên
Cố định bên phải: `top 80px, right 12px, bottom 58px, width 400px`, bo 18px, bóng `shadow-float`. Cấu trúc: header (tên, count chỉ khi backend query trả, nút ⤢ mở Hub, nút X) · tìm kiếm/lọc trạng thái · danh sách theo thời gian · bulk bar khi query/action backend sẵn sàng · chân "Xem tất cả phiên ⤢". **V3:** người dùng chọn backend search/filter/bulk; không bật thao tác bulk giả khi endpoint chưa có.

### 7.14. Sessions Hub
Toàn màn hình (z 40), header 42px logo-tile + tiêu đề 22px + nút X. Pill filters + ô tìm kiếm theo API V3. Lưới `1.3fr 1fr`: danh sách và **Quick view** với tên/meta/KPI thật, nút Mở phiên, Export (theo capability), Compare, Retry (chỉ khi backend status cho phép). Bulk actions chỉ bật qua endpoints V3. **V3:** backend query/filter/bulk và cursor/virtualization contract phải hỗ trợ target data volume; repo hiện chỉ limit/offset, tối đa 100.

### 7.15. Drawer nạp dữ liệu
Trượt từ trái, rộng 372-400px, bo 18px, bóng `shadow-float`. Header: **“Tạo phiên phân giải”** + nút X. Thân: tên phiên (persist backend), file/source/schema. Flow V3: tạo draft upload-session trước; validate/stage files; cho replace trước khi commit; chỉ tạo `ResolutionRun` khi xác nhận xử lý. CP2 dùng upload chia khối 8 MiB, có thể tiếp tục; file cũ được giữ tới khi file thay thế hợp lệ. Ứng dụng không đặt trần byte hoặc số dòng cho từng file; giữ tối đa 10 file mỗi phiên. Khả năng thực tế phụ thuộc dung lượng lưu trữ, proxy và database. Chưa benchmark pipeline ở quy mô nhiều triệu dòng.

### 7.16. Bảng thực thể cần review
**V3:** dùng “item/cụm review” (review case hoặc manual proposal), không gọi là entity. Các cột hiển thị member records/source/evidence/reason/action từ API; không hiển thị confidence % vì scoring score không phải xác suất. Audit có endpoint đọc event và màn hình phân trang/lọc; mọi role trừ Viewer được xem audit của run thuộc tài khoản mình, gồm Auditor.

### 7.17. Review ba cột
Lưới `250px 1.4fr 1fr`, khoảng cách 14px, mỗi cột là Card.
- **Hàng đợi:** chip bộ lọc đang áp ("Address ×") + số case; mỗi mục có avatar chữ cái 32px, tên, nguồn, badge %. Mục đang chọn nền `accent-soft`, viền `#9fdde9`.
- **So sánh record:** lưới `70px 1fr 1fr`; **ô khác nhau nền `warn-soft`, chữ `#8a4b00`/600**. Khung kết luận AI nền `ok-soft`: số % lớn 28px màu `ok`, tiêu đề "Khả năng cao là trùng", chip bằng chứng (✓ khớp, ~ gần giống kèm điểm).
- **Golden Record đề xuất:** mỗi field kèm chip nguồn nhỏ (`CRM`, `ERP`). Hai nút: Từ chối (outline) và Duyệt (primary).
- Không hiển thị chuỗi suy luận thô của AI, chỉ hiển thị bằng chứng kiểm chứng được.

> **V3 correction:** thay `%`/“khả năng trùng” bằng status/reason/field evidence thực. Backend pair score không được định nghĩa là xác suất. Đơn vị queue là review case hoặc manual proposal; capability backend quyết định approve/reject/edit. Giữ 3 cột và tokens của mockup.

### 7.18. Export dialog
Giữ hình thức mockup nhưng export nâng cao cần backend (đã chọn). CSV API hiện tại có readiness/preview/execute/download. V3 cần export job status, estimate thực, chunk/split policy, signed-link expiry và concurrency; có thể thêm format nếu được chốt. Không mặc định chọn Golden/duplicates/source rows hoặc cho bulk multi-session export nếu chưa có export contract/policy. Viewer chỉ đọc, không export.

---

## 8. Danh sách màn hình và trạng thái cần làm

| # | Màn hình / trạng thái | V3 status | Tham chiếu/ghi chú |
|---|---|---|---|
| 1 | Login, lỗi, đang xác thực, SSO, recovery, health | ĐIỀU CHỈNH | Giữ mockup; nối API auth thật. SSO/recovery backend cần bổ sung; `/health` hiện tối giản. |
| 2 | Empty state | ĐIỀU CHỈNH | Không có run giả; CTA theo capability. |
| 3 | Workspace tổng quan | ĐIỀU CHỈNH | Chỉ KPI thật từ overview, đúng đơn vị. |
| 4 | Processing | ĐIỀU CHỈNH | Các phase thật; processing indeterminate khi không có số đo. |
| 5 | Run error/retry | ĐIỀU CHỈNH | `RETRYABLE_FAILED`, error code và retry thật; chưa có cancel. |
| 6 | Upload draft/chọn file quy mô lớn | ĐÃ TRIỂN KHAI CP2 | Draft server-side, chunk 8 MiB, không giới hạn byte/dòng ở tầng ứng dụng; tối đa 10 file/phiên. Hiệu năng nhiều triệu dòng chưa benchmark. |
| 7 | Upload progress | ĐÃ TRIỂN KHAI CP2 | Tiến độ truyền thật qua XHR; checksum từng khối và tiếp tục sau khi chọn lại cùng file. |
| 8 | Validate/deduplicate completion | ĐIỀU CHỈNH | Phải là kết quả server; không giả schema/duplicate preflight. |
| 9 | Ready/replace | ĐÃ TRIỂN KHAI CP2 | Validate phía server; file thay thế được stage riêng trước khi promote. |
| 10 | Replacing upload | ĐÃ TRIỂN KHAI CP2 | Giữ file cũ nếu file thay thế chưa tải xong hoặc lỗi validate. |
| 11 | Upload validation error | ĐIỀU CHỈNH | Lỗi thật, có thể chọn lại file; không tạo run lỗi giả. |
| 12 | Draft indicator | ĐÃ TRIỂN KHAI CP2 | Draft lưu server-side; có thể mở lại, tải tiếp, đổi tên hoặc xóa. Retention/expiry chưa có. |
| 13 | Session panel, name, archive | ĐÃ TRIỂN KHAI MỘT PHẦN CP2 | Tên phiên, archive/restore được lưu backend và audit; retention policy và phân quyền theo role rộng hơn cần chốt. |
| 14 | Sessions Hub, search/filter/bulk | ĐÃ TRIỂN KHAI CP2 | Tìm theo tên/file, lọc trạng thái/archive, cursor pagination và bulk archive/restore; chỉ xem phiên thuộc tài khoản. |
| 15 | Review ba cột | ĐIỀU CHỈNH | Giữ visual, bind cases/proposals/evidence thật; bỏ xác suất suy diễn. |
| 16 | Golden, quality evidence, audit | AUDIT ĐÃ CÓ API/UI CƠ BẢN | Audit đã phân trang/lọc và giới hạn cho Steward/Senior Steward/Policy Admin/Auditor trên run của owner; Viewer bị từ chối. Golden/quality vẫn cần nghiệm thu riêng. |
| 17 | Compare 2–5 runs + series trend | CẦN BACKEND BỔ SUNG | Đã chọn backend multi-run/series; metric/grouping contract còn mở. |
| 18 | Large export | CẦN BACKEND BỔ SUNG | Đã chọn; CSV nền/trạng thái/partition và format policy cần chốt. |

Các màn không có mockup phải giữ tokens/typography và được duyệt hình ảnh ở checkpoint riêng. V3 statuses mô tả contract readiness, không có nghĩa các màn/backend đó đã triển khai.

---

## 9. Hành vi cần đúng (tóm từ bản 2)

- **Click row** chọn quick view; checkbox chọn nhiều. Bulk action chỉ bật sau khi bulk/query endpoints có; không dùng click handler giả.
- **Mở phiên từ Hub:** route hiện tại dùng `?run=&view=`. Mở đúng stage theo API/status/capability; unknown status có fallback, không ánh xạ thành failed.
- **V3 upload flow (CP2):** tạo UploadSession draft → upload/chunk/resume → server validation/schema + duplicate check → replace được stage riêng, giữ file cũ cho tới khi file mới hợp lệ → commit thành `ResolutionRun`, sau đó start pipeline. Các API đã được nối với backend.
- **Đóng drawer:** draft và progress được khôi phục từ backend; upload tiếp tục nếu contract upload-session hỗ trợ. Chấm cam chỉ hiện nếu backend báo draft còn sống. Chính sách hủy/xóa draft và expiry cần chốt.
- **Phiên đang chạy không dừng** khi người dùng xem phiên khác. Processing phase hiện tại chỉ có status/phase coarse; không hiện phần trăm. Upload progress chỉ hiện số đo nhận được từ upload event thật.
- **Compare (đã chọn backend):** chọn 2–5 phiên tương thích; hiển thị metric có contract và delta; Series nhóm phiên theo tiêu chí do người dùng chốt, rồi vẽ trend theo thời gian. Không tự nhóm theo tên file nếu chưa có rule.
- **Export (đã chọn backend):** CSV và job status cho data lớn; partition, optional formats, concurrency và expiry phải theo server policy. Audit mỗi lần export. Các ngưỡng cụ thể còn mở.
- **Quyền:** Viewer chỉ xem, không export. Steward thao tác theo capabilities; các role hiện tại vẫn là VIEWER, STEWARD, SENIOR_STEWARD, POLICY_ADMIN, AUDITOR. Kiểm tra quyền ở server; UI ẩn/disable action và giải thích.
- **Archive/retention:** người dùng yêu cầu rename, archive/restore, retention policy. Giữ lịch sử theo policy đã chốt; role archive và thời hạn còn mở. Không hard-delete từ UI trước khi có policy/API.
- **Audit:** người dùng xác nhận Steward trở lên được xem toàn bộ sự kiện theo phiên. Đã triển khai API phân trang/lọc, kiểm tra owner và capability; Viewer bị từ chối, Steward/Senior Steward/Policy Admin/Auditor được phép. UI hiển thị actor, thời điểm, loại thao tác và chi tiết event.

---

## 10. Hiệu ứng và chuyển động

Nguyên tắc: **hiệu ứng phải phản ánh đúng trạng thái thật.**

| Loại | Hiệu ứng |
|---|---|
| Thanh upload | Sau khi backend cung cấp progress bytes/total: đầy dần, nội suy mượt 300-500ms ease-out. Tốc độ/ETA chỉ hiển thị nếu server/client event có số đo thật |
| Processing | **Indeterminate** vì pipeline chỉ phát coarse phase hiện nay; không hiện % hoặc số tiến độ giả. Sau này chỉ dùng `processed/total` nếu được backend định nghĩa là pipeline-wide progress |
| Stepper | Bước xong: ✓, đường nối tô đầy. Bước đang chạy: vòng sáng nhấp nhô chu kỳ khoảng 2 giây. Bước chờ: đứng yên |
| Chấm "đang chạy" | Nhấp nhô opacity, chu kỳ 1,8 giây |
| Hoàn tất | Thanh chạy tới 100%, chớp xanh lá một lần, ✓, KPI đếm từ 0 **một lần**, rồi workspace mở ra |
| Lỗi | Thanh **dừng đúng chỗ**, chuyển đỏ, tắt chuyển động, bước lỗi có ⚠ |
| Panel, drawer | Trượt vào 200-250ms ease-out; dock dịch ngang khi panel mở |

Tránh: % giả, thanh chạy lùi (giữ giá trị lớn nhất), glow mạnh, nhiều hiệu ứng cùng lúc. Cập nhật số khoảng 4-5 lần/giây rồi nội suy. **Bắt buộc tôn trọng `prefers-reduced-motion`.**

---

## 11. Màn đăng nhập

Tham chiếu `masterone_login_light.html`.
- Bố cục **chia đôi** `1.08fr / 1fr`, khung bo 26px, bóng `shadow-login`, tối đa 1180px, giữa trang.
- **Trái:** nền gradient cyan nhạt `#f4fbfd → #d8f0f6` kèm họa tiết chấm; logo; headline 34px ("Một sự thật." màu `accent`); đoạn mô tả; **minh họa Mo cùng ba thẻ nguồn (CRM, ERP, Billing) nối bằng đường nét đứt vào ngực Mo, kết quả là thẻ Golden Record vàng**; ba mục tin cậy (Bảo mật, Phân quyền, Kiểm toán).
- **V3 — Phải:** dòng health lấy từ `/health` và theme/language control chỉ khi có hỗ trợ; form giữa (tối đa 390px): email, mật khẩu show/hide, **Quên mật khẩu?**, nút đăng nhập và nút SSO doanh nghiệp. `remember me` chỉ khi session backend hỗ trợ persistence preference. Người dùng yêu cầu SSO/OIDC và password recovery; provider/workflow/API chưa tồn tại nên login UI phải phản ánh “chưa cấu hình” cho tới khi có backend, không giả đăng nhập.
- Kiểm tra trường rỗng khi gửi, thông báo lỗi dưới từng ô, xóa lỗi khi gõ lại. **Không báo cụ thể "sai tên" hay "sai mật khẩu"**, dùng một thông báo chung để tránh lộ tài khoản tồn tại.
- Dưới 860px: một cột, ẩn minh họa và ba mục tin cậy.
- Phần xác thực thật (OIDC provider, password reset/token, giới hạn thử/khóa tài khoản) do backend/cấu hình quyết định. UI chỉ phản ánh response. Repo hiện email/password cookie auth, CSRF, rate-limit và `/health` đơn giản; không tự suy ra SSO hoặc quên mật khẩu.

---

## 12. Mô hình dữ liệu và API gợi ý

**V3:** runtime UI không dùng mocks thay API. Adapter đặt trong `frontend/src/lib/` chuyển API contract thành view model. Phân biệt hợp đồng đang có với các endpoint cần thiết kế/bổ sung.

```ts
type RunStatus = "UPLOADED" | "QUEUED" | "RUNNING" | "COMPLETED" |
  "RETRYABLE_FAILED" | "FAILED" | (string & {});
type ProgressView = { mode: "idle" | "indeterminate" | "complete" | "error";
  phase: string; processed: number; total: number };
type ResolutionSessionVM = {
  id: string; name: string; status: RunStatus; phase: string;
  createdAt: string; fileNames: string[]; sourceCounts: Record<string, number>;
  metrics: { records: number; files: number; candidatePairs: number;
    mergeItems: number; reviewItems: number; estimatedSeparate: number; exports: number };
  progress: ProgressView;
  capabilities: { canStart: boolean; canRetry: boolean; canReview: boolean; canExport: boolean };
  errorCode?: string;
};
// Future backend resources, not current API contracts:
type UploadSession = { id: string; name: string; state: "DRAFT" | "UPLOADING" | "VALIDATING" | "READY" | "ERROR" | "COMMITTED";
  files: UploadFileState[]; expiresAt?: string };
type UploadFileState = { id: string; name: string; bytesUploaded: number; bytesTotal: number;
  state: "UPLOADING" | "VALIDATING" | "READY" | "ERROR"; errorCode?: string };
type Series = { id: string; name: string; runIds: string[]; schemaKey: string };
```

| Trạng thái | Endpoint/contract | Mục đích |
|---|---|---|
| Hiện có | `GET/POST /api/v1/mdm/runs`, `GET /{run_id}`, `POST /{run_id}/start`, `GET /{run_id}/overview` | Danh sách offset, multipart upload trực tiếp tạo run, start và overview. |
| Hiện có | `/api/v1/mdm/runs/{id}/items/{bucket}`, review transition, golden-record, explanation | Review queue, decisions, golden override, explanation; server capabilities/ownership bắt buộc. |
| Hiện có | readiness/previews/exports/download/compare | CSV flow hiện tại; compare chỉ source-vs-export của cùng run. |
| Cần bổ sung (đã chọn) | upload-session create, chunk/resume/status/validate/replace/commit | Upload draft trước run; real upload progress; scale target nhiều triệu records. Contract và storage lifecycle cần thiết kế. |
| Cần bổ sung (đã chọn) | list query/cursor/filter, bulk actions; `PATCH` name/archive/restore | Sessions Hub, persisted names, archive/restore/retention. |
| ĐÃ TRIỂN KHAI (phạm vi cơ bản) | audit read/filter/paginate; roles Steward+ | Xem event đã ghi nhận theo run của owner; Viewer bị từ chối. Event lịch sử trước khi bật audit không được dựng lại. |
| Cần bổ sung (đã chọn) | series CRUD/membership + compare 2–5 compatible runs | Metric schema, compatibility rules, grouping và retention của series cần chốt. |
| Cần bổ sung (đã chọn) | export job create/status/download/expiry | Background large export, partition/format/concurrency policy cần chốt. |
| Cần bổ sung | OIDC start/callback + password recovery | Provider, account linking, recovery workflow và email delivery cần chốt. |

Mọi thao tác ghi (review, override, đổi tên, archive, series membership, export) **ghi audit ở server**. Adapter không suy `entityCount`, quality percentage, series membership hoặc fake progress nếu API chưa trả các giá trị đó.

---

## 12b. Truy cập (a11y)

- Mọi icon-only button có `aria-label` (nút tròn, X, ⤢, hiện mật khẩu).
- Focus nhìn thấy rõ: `outline: 3px solid accent; outline-offset: 2px`.
- Drawer, panel, dialog: bẫy focus, đóng bằng `Esc`, trả focus về nút mở.
- Danh sách phiên điều hướng được bằng bàn phím; Space tick checkbox, Enter mở phiên.
- Thông báo lỗi form dùng `role="alert"`; trạng thái tiến độ dùng `aria-live="polite"` và `role="progressbar"` với `aria-valuenow` (bỏ `aria-valuenow` khi indeterminate).
- Tương phản chữ thường tối thiểu 4.5:1. Kiểm tra lại cặp `muted` trên `canvas` và chữ trắng trên `accent` ở cỡ nhỏ.

---

## 13. Thứ tự triển khai

Mỗi giai đoạn **dừng lại để người dùng duyệt** trước khi sang giai đoạn sau.

| GĐ | Công việc | Tiêu chí hoàn thành |
|---|---|---|
| 0 | **Nền giao diện theo repo:** Be Vietnam Pro, light/dark semantic tokens, CSS variables; không tạo Next app hay thêm Tailwind | Font/Vietnamese OK; light tokens khớp spec; dark theme đủ tương phản; không hardcode palette trong page |
| 1 | **Primitives + Mo + login/shell nền:** tái sử dụng convention `frontend/app`, `frontend/src/components`, hiện thực button/card/input/badge/focus, brand, login layout và shell hiện hữu | UI hiển thị đúng style; login email/password dùng API thật; Viewer actions bị giới hạn theo capabilities; responsive/a11y; build/typecheck/test pass |
| 2 | UploadSession draft, chunks/resume/progress/replace, validate, commit thành ResolutionRun; Sessions Hub query/filter/bulk | Draft bền vững, thay file an toàn, không có trần byte/dòng ở tầng ứng dụng, quyền server-side. Benchmark mục tiêu nhiều triệu dòng là tiêu chí riêng trước khi tuyên bố scale support |
| 3 | Run overview, processing/error, KPI, history | Chỉ KPI/status thật; reload/deep-link/retry; không phần trăm giả |
| 4 | Retention/cleanup policy, expiry và role phạm vi admin | Chốt policy trước khi tự purge; không xóa dữ liệu theo thời hạn chưa được phê duyệt |
| 5 | Review, Golden, explanation, audit read | Revision/conflict/decisions đúng; audit đọc theo role; không suy confidence |
| 6 | Series/compare + large export | Compare contract tương thích; export job/partition/expiry; role Viewer không export |
| 7 | Visual/regression QA | Render hai theme mọi màn chính cạnh HTML refs, responsive, keyboard/focus/reduced motion, performance ở volume mục tiêu |

### 13.1. Kết quả checkpoint GĐ0–1 (2026-10-07)

Đã thực hiện trong phạm vi được duyệt: light/dark theme tokens theo spec; Be Vietnam Pro; màn đăng nhập/đăng ký mới có Mo, health indicator và khả năng hiện/ẩn mật khẩu; shell phiên responsive dùng top bar, dock và thanh trạng thái hiện có. Auth vẫn gọi API email/password hiện hữu; health chỉ mang tính thông tin. Nút SSO và khôi phục mật khẩu thể hiện trạng thái chưa cấu hình. Không bổ sung backend API trong checkpoint này.

Các file trọng tâm: `frontend/app/themes.css`, `frontend/app/auth.css`, `frontend/app/session-shell.css`, `frontend/app/layout.tsx`, `frontend/src/components/AuthForm.tsx`, `frontend/src/components/AuthForm.test.tsx`, `frontend/src/lib/api.ts`.

Kiểm tra khi hoàn thành CP01: `npm run lint` pass; Vitest 5 files / 31 tests pass; `npm run build` pass. Chưa thực hiện pixel comparison bằng browser với HTML tham chiếu; việc đó nằm ở GĐ7.

### 13.2. Kết quả checkpoint GĐ2 (2026-10-07)

Đã bổ sung draft upload-session bền vững; chunk 8 MiB có checksum, tiếp tục upload, validate streaming, replace an toàn, xóa draft và commit idempotent thành `ResolutionRun`. CSV được parse theo dòng và insert theo batch 1.000 record; kiểm tra trùng ID dùng SQLite staging index thay vì giữ toàn bộ ID trong RAM. Ứng dụng không áp trần dung lượng file hoặc số dòng; giới hạn 10 file mỗi phiên vẫn giữ. Local staging dùng `UPLOAD_STAGING_DIR`; nếu cấu hình R2 thì lưu từng chunk ở object storage. Giới hạn vận hành vẫn phụ thuộc disk/R2, proxy, DB và năng lực worker.

Sessions Hub có cursor pagination, tìm tên phiên/tên file, lọc trạng thái/archive, quick view, đổi tên và bulk archive/restore; backend chỉ trả dữ liệu của owner, và thao tác quản lý cần capability `manage_run`. Cập nhật `record_count`/`row_count` sang BigInteger. Chưa benchmark nhiều triệu dòng và pipeline worker hiện tại chưa được xác nhận cho mức tải đó.

Tại thời điểm kết thúc CP2: migration head `0018_upload_sessions`; 25 test backend trọng tâm (migration, upload/resume/replace, file >5 MiB, >10.000 dòng, Hub, paging và RBAC) pass; Vitest 6 files / 32 tests, TypeScript lint, Ruff trên file CP2 và Next production build pass. Full backend suite: 277 pass, 10 fail ở test LLM, scoring/regression và selective unmerge ngoài CP2. Chưa thực hiện pixel comparison browser (GĐ7). **GĐ3 chưa triển khai tại checkpoint CP2; kết quả bổ sung được ghi ở mục 13.3.**

### 13.3. Kết quả checkpoint GĐ3 (2026-10-07)

Hoàn thiện theo dõi trạng thái phiên mà không thay đổi thuật toán phân giải: `GET /mdm/runs` có thể lọc `QUEUED,RUNNING` và trả thêm `phase` theo từng run; bộ lọc và tổng số vẫn giới hạn theo owner. Thanh trạng thái dưới cùng cho biết số phiên đang chạy, mở tối đa 5 phiên gần nhất để quay sang xem một phiên khác, và giữ dấu stale khi không lấy được trạng thái mới.

Polling overview của run đang mở chạy tuần tự mỗi 1,5 giây khi ổn định; khi lỗi mạng, giữ dữ liệu cũ, báo chưa cập nhật và tăng backoff tối đa 30 giây. Danh sách phiên đang chạy chỉ gọi query nhẹ mỗi 5 giây, backoff tối đa 60 giây; không gọi overview cho tất cả run. Phase được ánh xạ sang nhãn tiếng Việt và vẫn không hiển thị phần trăm giả.

Kiểm tra GĐ3: 17 backend test Hub/RBAC pass; Vitest 7 files / 35 tests, TypeScript lint, Ruff file backend thay đổi và Next production build pass. Chưa kiểm tra browser E2E hoặc so pixel mockup; cần thực hiện ở GĐ7. Không thêm migration và không đổi xử lý dữ liệu.

---

## 14. Điều chỉnh so với bản 2 (tài liệu này thắng khi mâu thuẫn)

1. Repo hiện dùng app shell/component/state/router riêng; V3 không tạo route tree hoặc thư viện song song.
2. **Tên session hiển thị là “Phiên phân giải”**; backend sẽ lưu tên tùy chỉnh. Upload/draft/replace xảy ra trước khi `ResolutionRun` được tạo.
3. **Light và dark mode đều bắt buộc.** Giữ màu/font/layout của mockup; thêm dark semantic token mapping.
4. **Viewer chỉ đọc, không export.** Giữ 5 roles/capabilities hiện có; enforcement server-side.
5. Người dùng chọn backend additions cho progress/upload session, replace, draft, names/archive, Hub query/bulk, audit read, multi-run/series compare và large export. Đây là target về sau, không phải API đang tồn tại.
6. Mục tiêu data là nhiều triệu records; giới hạn/throughput/RPO/retention phải chốt và benchmark trước khi tuyên bố scale support.
7. Mo chỉ là brand mark. Không dùng AI score như xác suất nếu backend không định nghĩa probability.

---

## 15. Điểm cần xác nhận với đội kỹ thuật và thiết kế

- Target chính xác nhiều triệu records: max rows/file/run, GB, concurrent sessions, processing target và benchmark dataset.
- Series criteria: tạo thủ công hay grouping rule; schema compatibility; KPI/delta/trend cần dùng.
- Large export: CSV/XLSX/khác, partition row/byte, concurrency, signed-link expiry và retention.
- Archive/retention: ai được archive/restore, thời hạn, cold storage/restore latency, purge policy.
- Audit role: “Steward trở lên” có bao gồm `AUDITOR` không; event categories, filter và retention.
- OIDC provider, account linking, password recovery/email delivery, tự đăng ký hay admin provisioning.
- Cancel processing/draft lifecycle: có hủy worker không; draft expiry và delete/cancel behavior.
- Health indicator: `/health` hiện trả status/env; chốt trạng thái health nào an toàn/đủ ý nghĩa để hiển thị.
- Màn thiếu mockup: Processing, error, Golden/Quality/Audit, multi-run compare, large export; cần review riêng.

---

## 16. Prompt mẫu cho agent (dán vào VS Code)

```text
Bạn là kỹ sư làm việc trong repo MasterOne hiện tại. Dùng docs/MasterOne_Design_Spec_NextJS_v3.md làm spec; giữ nguyên kiến trúc frontend/app (Next App Router), frontend/src/components, frontend/src/lib và CSS hiện có.
Giữ thị giác/tokens/typography của mockup, có cả light/dark. Backend là nguồn thật cho dữ liệu, status và permission. Không dùng fake runtime data; adapter ở frontend/src/lib. Không thêm dependency/framework song song nếu chưa cần.
Thực hiện theo thứ tự mục 13. Ở mỗi checkpoint, chạy kiểm tra phù hợp, ghi file thay đổi và kết quả; dừng chờ duyệt trước khi sang checkpoint kế tiếp.
Mục tiêu scale, upload-session, SSO, audit read, series và export nâng cao chỉ làm trong các giai đoạn sau khi backend contract đã được duyệt.
```

---

## 17. Cách kiểm tra "ra đúng giao diện"

1. Render HTML mockup và ứng dụng ở viewport tương ứng; kiểm tra 1180px cùng mobile breakpoints. Không kết luận pixel QA từ static markup.
2. So màu/typography/layout/radius/shadows; kiểm tra light và dark. Sai khác nhỏ do font rasterization chấp nhận được; token, spacing, size, placement lệch cần sửa.
3. Kiểm tra trạng thái thật: upload/draft errors, run error, archived, Viewer, empty state, long lists; dùng backend fixtures/test data, không tạo số giả.
4. Thử keyboard/focus, reduced-motion, contrast ≥4.5:1; kiểm tra aria-live indeterminate progress.
5. Chạy `npm run lint`, `npm test -- --run` và `npm run build` tại `frontend`; backend tests chỉ ở giai đoạn có backend change.
