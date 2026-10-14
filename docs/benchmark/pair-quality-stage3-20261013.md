# Giai đoạn 3 — Cân bằng chất lượng phân loại cặp (13/10/2026)

## Kết quả triển khai

Mục tiêu người dùng xác nhận: **precision 99,5% cho vùng tự động hóa**, đồng thời giảm công đọc dữ liệu. Giai đoạn này bổ sung thu hồi có điều kiện cho người duyệt và một phép đo tự động hóa ở chế độ shadow. Không dùng điểm quy tắc như xác suất; kết quả benchmark không kích hoạt quyền tự động gộp.

- Assessment mới **person-review-assessment-v4**, support policy **person-review-support-v1**. Giữ kết luận gốc, điểm, trạng thái từng trường và lý do thiếu bằng chứng; thêm bằng chứng dẫn tới REVIEW.
- Thu hồi khi có tổ hợp cụ thể: tên đầy đủ khớp + DOB đầy đủ; tên phù hợp + cùng giấy tờ cá nhân chưa xác minh trong miền tương thích; tên phù hợp + contact lệch một ký tự/đảo hai ký tự liền nhau + DOB hoặc địa chỉ có số nhà/tên đường; DOB đầy đủ + địa chỉ chi tiết khớp. Tên xung đột vẫn thuộc D và bắt buộc lý do của người duyệt.
- Giấy tờ chưa xác minh chỉ hỗ trợ khi thuộc VN CCCD/CMND với namespace rõ, hoặc miền tùy chỉnh có metadata tính duy nhất danh tính ở cả hai phía. Mã dùng chung/miền thiếu không tự trở thành bằng chứng cá nhân. ID khác nhau trong namespace có thể so sánh chặn thu hồi thiếu neo; ID thuộc hai namespace tường minh khác nhau không chặn riêng tổ hợp tên/DOB. Phân nhóm D lịch sử vẫn được xử lý thận trọng và có thể yêu cầu lý do.
- DOB một phần, chỉ tên, địa chỉ thành phố/phường, contact dùng chung/lịch sử/không hợp lệ và xung đột xác minh không được nâng tùy tiện lên REVIEW. Hai khóa sinh ứng viên mới tên–DOB và DOB–đường có giới hạn bucket 30/20.
- Sau Accept, bộ kiểm tra cụm tính lại support từ dữ liệu hiện tại cho từng cặp. Marker lưu sẵn, cặp chưa duyệt, cạnh còn thiếu, Deny/Không gộp/cannot-link không cấp phép gộp bắc cầu. Giữ giới hạn kích thước/nguồn/ràng buộc và audit hiện có.
- UI giải thích ngắn bằng đúng trường hỗ trợ, có nhãn cần duyệt và giữ toàn bộ đối chiếu. Duyệt theo cụm của giai đoạn 2 tiếp tục dùng xác nhận/token/hàng đợi hiện có.

## Đo trước/sau trên dữ liệu đã cung cấp

Baseline là snapshot workspace sau giai đoạn 2, gồm các thay đổi chưa commit; không dùng cây sạch ở HEAD. Chạy matcher/sinh ứng viên/guard trước khi mở nhãn CERTAIN. Kiểm tra hash nguồn và truth; trước/sau dùng cùng raw-record hash. Hash mã nguồn, evaluator, profile và cluster policy đi kèm từng case. Không đưa nhãn ownership vào matcher.

Phạm vi: PERSON/UNKNOWN trong các dòng đã nạp. Trường loại thực thể rỗng được tính UNKNOWN như runtime (TC5 có 898 dòng như vậy). Mọi cặp cùng thực thể có nhãn trong phạm vi này nằm trong mẫu số, kể cả cặp chưa được sinh làm ứng viên. “Bao phủ duyệt” là tỷ lệ cặp trùng tới hàng đợi bắt buộc; không phải recall đầu ra sau người duyệt.

| Bộ | Dòng đã nạp | Mọi cặp trùng có nhãn trong phạm vi | Cặp cần duyệt trước → sau | Cặp trùng cần duyệt trước → sau | Cặp khác người cần duyệt trước → sau | Bao phủ duyệt trước → sau | Cặp trùng còn bị bỏ sót khi sinh ứng viên |
|---|---:|---:|---:|---:|---:|---:|---:|
| TC1 | 10,022 | 4,166 | 4,166 → 4,166 | 4,166 → 4,166 | 0 → 0 | 100.00% → 100.00% | 0 |
| TC2 | 10,000 | 0 | 1 → 4 | 0 → 0 | 1 → 4 | — → — | 0 |
| TC3 | 9,992 | 4,135 | 1 → 106 | 0 → 95 | 1 → 11 | 0.00% → 2.30% | 1,607 |
| TC4 | 10,014 | 0 | 2,460 → 2,461 | 0 → 0 | 2,460 → 2,461 | — → — | 0 |
| TC5 | 10,010 | 8,898 | 4,608 → 6,612 | 4,064 → 6,063 | 474 → 479 | 45.67% → 68.14% | 606 |
| TC6 | 12,000 | 1,238 | 1,168 → 1,261 | 1,145 → 1,238 | 23 → 23 | 92.49% → 100.00% | 0 |
| TC8 | 12,000 | 339 | 339 → 344 | 336 → 339 | 3 → 5 | 99.12% → 100.00% | 0 |
| TC13 | 12,000 | 236 | 972 → 987 | 223 → 232 | 749 → 755 | 94.49% → 98.31% | 0 |

Tổng tám case: **thêm 2.199 cặp trùng tới người duyệt**, thêm **27 cặp khác người**, không thêm cặp chưa có nhãn. Đây là 2.226 nhiệm vụ bổ sung để khôi phục chất lượng, chưa phải 2.199 cặp đã được người dùng xác nhận/gộp. Không lấy tỷ lệ tổng hợp các bộ tổng hợp này để chứng nhận 99,5% trên khách hàng thật.

TC5 thu hồi thêm **1.999 cặp trùng/5 cặp khác người**, bao phủ duyệt **45,67% → 68,14%**. Vẫn còn **606 cặp trùng chưa có ứng viên** và **2.229 cặp ứng viên trùng thiếu bằng chứng**. TC3 chỉ bao phủ **2,30%**, còn **1.607 cặp trùng chưa có ứng viên** và **2.433 cặp ứng viên trùng thiếu bằng chứng**. Các hạn chế này hiện rõ trong báo cáo, không bị biến thành kết luận khác người.

TC2/TC4 không có cặp trùng có nhãn nên recall không xác định. Hàng đợi TC4 vẫn có 2.461 cặp khác người; TC13 có precision hàng đợi khoảng 23,51%. Thu hồi có điều kiện chưa giải quyết được toàn bộ công duyệt ít giá trị của matcher cũ. Tổ hợp DOB/địa chỉ cần tiếp tục đánh giá theo cohort; ở TC8 cả hai cặp mới từ tổ hợp này đều khác người.

| Tổ hợp support trên tám case | Tổng thu hồi | Cùng người theo nhãn | Khác người theo nhãn | Chưa có nhãn |
|---|---:|---:|---:|---:|
| FULL_DOB_DETAIL_ADDRESS | 97 | 81 | 16 | 0 |
| NAME_FULL_DOB | 2127 | 2116 | 11 | 0 |
| NAME_NEAR_CONTACT_ADDRESS | 2 | 2 | 0 | 0 |

**Giới hạn dữ liệu:** TC1–5 là bộ 10k seed7; TC6/8/13 lấy 12k dòng theo SHA256 danh tính nguồn từ bộ 100k, không chọn bằng nhãn. Mẫu làm đổi tần suất contact. TC5 loại 65 dòng cấu trúc/footer/header lặp/dòng lỗi, nạp 10.010 dòng so với manifest 10.019; ánh xạ được 9.801 dòng có nhãn CERTAIN, trong đó 8.457 dòng thuộc phạm vi cá nhân. 70 cặp cần duyệt của TC5 chưa đủ nhãn và được báo riêng, không tính là đúng. Dữ liệu đã được khảo sát ở các giai đoạn trước và trong giai đoạn này; đây là kiểm chứng khám phá, không phải holdout độc lập. Không suy rộng lên 100k/1 triệu bản ghi hoặc dữ liệu khách hàng thật.

## Tự động hóa và mốc 99,5%

Shadow lấy riêng kết luận MERGEABLE nghiêm ngặt, chạy qua bộ guard cụm thật, không ghi DB/quyết định người duyệt. Tất cả kết quả shadow trước/sau giống nhau; phần thu hồi REVIEW không được đưa vào vùng shadow gộp. Mọi case có unsupported_recovery = 0 và constraint_violations = 0. Chưa quan sát cụm gộp sai trên phần có nhãn; TC5 còn một cụm thiếu nhãn.

Gate dùng **cụm nhiều bản ghi đã thử gộp** làm đơn vị, kiểm tra đủ nhãn, ràng buộc, miền dữ liệu production, tính đại diện/holdout và cận dưới Wilson hai phía 95% so với 99,5%. Singleton được giữ riêng không làm phình mẫu “gộp đúng”. Khoảng tin cậy trên dữ liệu đã khảo sát/các cụm có tương quan chỉ mang tính mô tả.

| Bộ | Cụm shadow thử gộp | Cụm đủ nhãn | Cụm gộp sai quan sát | Cận dưới precision cụm | Chứng nhận vùng tự động |
|---|---:|---:|---:|---:|---|
| TC1 | 23 | 23 | 0 | 85.69% | Không đủ |
| TC2 | 0 | 0 | 0 | — | Không đủ |
| TC3 | 0 | 0 | 0 | — | Không đủ |
| TC4 | 0 | 0 | 0 | — | Không đủ |
| TC5 | 299 | 298 | 0 | 98.73% | Không đủ |
| TC6 | 515 | 515 | 0 | 99.26% | Không đủ |
| TC8 | 334 | 334 | 0 | 98.86% | Không đủ |
| TC13 | 219 | 219 | 0 | 98.28% | Không đủ |

`eligible=false`, `runtime_activation=false` ở cả tám case. Những kết quả hoàn hảo trên mẫu nhỏ vẫn không đạt cận dưới yêu cầu; TC6 có 515 cụm đúng nhưng cận dưới chỉ 99,26%. Ngoài cỡ mẫu, toàn bộ dữ liệu này là tổng hợp, đã được khảo sát và chưa đại diện production. **Auto-merge thực tế vẫn tắt**, bao gồm mọi cặp được thu hồi. Gate là kết quả kiểm định có thể tái chạy, không phải cấu hình bật auto trong worker.

Để cấp quyền tự động gộp ở giai đoạn vận hành, cần dữ liệu cá nhân thật đại diện, nhãn độc lập/được kiểm tra ở cấp cụm, kiểm định tách khỏi dữ liệu thiết kế quy tắc, theo dõi từng cohort và một cơ chế rollout runtime riêng. Không chỉ lấy các quyết định ACCEPTED trước đây làm mẫu đại diện cho toàn bộ dữ liệu.

## Kiểm chứng và review

- Backend bản cuối: **1.173 passed, 2 skipped**, exit 0.
- Frontend: **211 passed / 30 files**; TypeScript `tsc --noEmit` đạt; production frontend đã build trong Compose hiện có.
- Docker backend/worker bản sau review build exit 0; hash support trong backend đang chạy khớp workspace; backend/frontend/PostgreSQL healthy và worker running.
- Cả 8 bộ trước/sau hoàn tất exit 0, hash nguồn/dữ liệu giống nhau; source identity của các case bản cuối ổn định.
- Bộ trình duyệt bản sau review: **4/4 PASS**, không có pageErrors.
- Git diff whitespace check exit 0.

Bộ chạy browser: `frontend/scripts/verify-pair-quality.mjs`; kết quả/ảnh: `frontend/artifacts/pair-quality-stage3/`. Bốn ca chạy API/PostgreSQL/worker/Edge thật: thu hồi cụm ba người-record rồi Accept đủ cạnh/publish; DOB một phần/địa chỉ chung vẫn riêng; phone đảo số REVIEW rồi Không gộp; xung đột tên cần lý do trước Accept. Không có lỗi JavaScript. Các ca và dữ liệu đều tổng hợp trên localhost.

Review độc lập không phát hiện Critical; hai Important về miền giấy tờ và namespace đã sửa với hồi quy RED/GREEN. Bổ sung fingerprint và các ca email, contact lịch sử và thứ tự contact phụ theo nhận xét Minor. Full suite ban đầu có lỗi metadata khóa chặn mới (đã cập nhật phép chiếu test, không đổi fixture lịch sử) và lỗi PermissionError tạm thời khi Windows đọc/đổi manifest cùng lúc. Case Windows chạy riêng và full suite tiếp theo đều qua; không sửa storage trong phạm vi này.

Không tạo Compose mới, không thêm migration giai đoạn 3, không commit hay ghi đè công việc có trước. Phiên đã có assessment cũ cần đánh giá lại tường minh để nhận v4; lịch sử quyết định được giữ.

Artifact số liệu: [trước](pair-quality-stage3-before.json), [sau](pair-quality-stage3-after.json), [kiểm chứng](pair-quality-stage3-validation.json). Có thể tái chạy evaluator bằng `.venv/Scripts/python.exe scripts/verify_pair_quality.py --datasets <cache> --max-records 12000 --precision-target 0.995 --output <report.json>`; `--source-tree <snapshot/backend/src>` để đo baseline. Backend test dùng môi trường cô lập của `scripts/run_merge_baseline.py`, tránh DB/R2/LLM bên ngoài; các nguồn Excel dùng dependency trong `scripts/requirements-qa.txt`.

## Cơ sở tham khảo

[Splink — Edge Evaluation](https://moj-analytical-services.github.io/splink/topic_guides/evaluation/edge_overview.html) hướng dẫn đánh giá match bằng nhãn và xem cả chất lượng cụm. [Dedupe — Choosing a Good Threshold](https://docs.dedupe.io/en/latest/how-it-works/Choosing-a-good-threshold.html) phân biệt mẫu active learning với mẫu đại diện để chọn ngưỡng. [NIST — Confidence Limits for a Proportion](https://www.itl.nist.gov/div898/handbook/prc/section2/prc241.htm) trình bày khoảng tin cậy tỷ lệ. Các nguồn này là cơ sở tách độ bao phủ, chi phí duyệt và kiểm định vùng tự động; không chứng minh rằng tổ hợp support cụ thể của P-119 đã đạt precision production.
