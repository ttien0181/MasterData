# Giai đoạn 4 — Tự động hóa có kiểm soát (13/10/2026)

## Kết quả

Người dùng xác nhận mục tiêu **tự động hóa có kiểm soát**, vùng tự động đạt precision **99,5%**, có chạy shadow, truy vết và cơ chế dừng. Đã triển khai luồng vận hành cho mục tiêu này; dữ liệu hiện có chưa được chứng nhận production nên **không bật tự động gộp cho dữ liệu thực tế**.

- **Chạy thử tự động hóa**: tác vụ worker tính kế hoạch cụm qua guard thật, không thay đổi quyết định.
- **Áp dụng tự động**: chỉ chấp nhận cả cụm thuộc vùng đã kiểm định và chứng nhận đang hợp lệ. Cần xác nhận một lượt có giới hạn; các cặp khác tiếp tục cần người duyệt. Tác vụ không tự hoàn tất phiên, tạo golden hay xuất dữ liệu.
- **Dừng tự động hóa phiên này**: lưu trạng thái dừng, hủy tác vụ tự động đang chờ/chạy và thu hồi quyền của worker dưới khóa phiên. Người dùng tiếp tục duyệt thủ công ngay, kể cả khi worker offline. Quyết định đã commit được giữ lại; có thể đối chiếu/sửa qua quy trình hiện có.
- Các cặp được chấp nhận tự động có nhãn và provenance riêng: operation, certificate, component/cohort. Audit ghi `WORKFLOW_PAIR_AUTOMATIC_ACCEPTED`, không giả thành quyết định của người duyệt. Sửa thủ công/repair xóa provenance đang có hiệu lực; lịch sử audit/operation giữ lại.

## Điều kiện cấp quyền

`controlled-pair-automation-v1` yêu cầu cặp có assessment v4, kết luận gốc MERGEABLE, thuộc A và vẫn UNDECIDED; không recovery, CF/SH/IV, xung đột ID hay constraint. Matcher được tính lại từ dữ liệu hiện tại. Cụm chỉ hợp lệ nếu **mọi cặp thành viên có bằng chứng trực tiếp đủ điều kiện**, qua các ràng buộc hiện có; không dùng cạnh chưa duyệt, Hoãn, Không gộp, Deny/cannot-link hoặc cạnh thiếu để tự gộp bắc cầu.

Chứng nhận là file do phía vận hành cấu hình, được pin bằng SHA256. Người dùng HTTP không gửi chứng nhận và không hạ mục tiêu chính xác. Nội dung gắn với chủ dữ liệu, phạm vi dữ liệu vận hành, tập nguồn, mã/code identity, profile, phiên bản assessment và thời hạn. Cohort gắn mẫu trạng thái trường/subgroup, tập nguồn và kích thước cụm; kiểm định cụm hai thành viên không cấp quyền cho cụm lớn hơn.

Mỗi cohort cần nhãn đầy đủ cho những cụm đã thử gộp, không vi phạm constraint, và cận dưới Wilson hai phía 95% đạt 99,5%. Áp dụng điều chỉnh Bonferroni theo số cohort trong chứng nhận. Singleton không được tính là một lần gộp đúng. Mẫu nhỏ hoàn hảo, thiếu nhãn, dữ liệu tổng hợp, code/profile/owner/scope không khớp, chứng nhận hết hạn/bị thay hoặc chưa pin đều bị chặn.

**Tính độc lập, đại diện và chất lượng nhãn cần người phụ trách dữ liệu kiểm định.** Metadata production/holdout/representative trong chứng nhận là xác nhận của phía vận hành; phần mềm không chứng minh các điều đó chỉ bằng một file JSON hay các quyết định ACCEPTED cũ. SHA256 ngăn dùng file khác file đã duyệt, không thay thế kiểm tra nhãn hay chứng minh khoa học về chất lượng. Mức 99,5% là điều kiện kiểm định vùng đã khai báo, không phải xác suất riêng của từng cặp.

Giới hạn mặc định **20 cụm mỗi phiên**, cấu hình server tối đa 100; mỗi cụm tối đa 20 thành viên/190 cặp trực tiếp. Hạn mức cộng dồn trên operation đã hoàn tất, không hoàn lại khi sửa quyết định/retry/thay chứng nhận. Lọc cohort được chứng nhận trước hạn mức nên cụm chưa được chứng nhận đứng đầu không che cụm hợp lệ phía sau. Apply là giao dịch nguyên tử cho cả lượt, không chấp nhận một phần cụm.

## Khóa, kiểm tra lại và lịch sử

Shadow/apply dùng operation, hàng đợi, quyền tài khoản, owner, khóa run, revision, immutable artifact và lease/attempt của worker hiện có. Apply cần token kế hoạch đã niêm phong, đúng run/assessment/decision revision. Worker tính lại cụm và kiểm tra snapshot, quyền, ràng buộc, certificate/code/profile/nguồn và hạn mức trước khi ghi, kiểm tra chứng nhận lần cuối trước publication. Client không thể dùng token giả, preview bị sửa, preview của phiên khác hay preview cũ sau người duyệt đổi quyết định.

Nút dừng hủy operation/task tự động và retire attempt đang chạy; apply retry sau dừng bị từ chối. Nếu publication commit trước khi thao tác dừng lấy được khóa run, dữ liệu đó đã lưu và không bị undo ngầm. Không bật lại trong cùng phiên; tiếp tục xử lý bằng duyệt thủ công hoặc một phiên vận hành mới đã kiểm tra phạm vi.

Migration **0031_pair_automation**, sau 0030, thêm `WorkflowPair.automation` JSON nullable. Test migration dùng Alembic thật, giữ decision/review_reason/revision cũ và không gán provenance tự động cho lịch sử thủ công. Local PostgreSQL đã lên HEAD0031. Không tạo Compose mới, không đổi Compose trong giai đoạn 4, không commit hay reset công việc trước đó. Phiên đã có assessment từ trước cập nhật cần Đánh giá lại bằng chứng để pin đúng implementation/execution identity hiện tại; quyết định đã lưu được giữ nguyên. Dữ liệu/lịch sử cũ vẫn truy cập được, không tự viết lại kết luận lịch sử.

## Công cụ kiểm định và cấu hình

`scripts/pair_automation_certificate.py` nhận các kết quả shadow và CSV ownership `record_id,entity_id`, đếm đúng/sai/thiếu nhãn theo **cả cụm**. Từ chối thành viên mẫu bị lặp/chồng lấn và pooling khác code/profile/owner. Công cụ xuất certificate cùng `.validation.json`, hash pin và trạng thái gate; **không deploy hay bật runtime**. Nên dùng các shadow từ những phiên validation disjoint/được thiết kế độc lập; ID khác nhau không tự chứng minh hai mẫu không cùng thực thể.

Ví dụ tạo artifact để kiểm tra dữ liệu tổng hợp (mặc định không đủ điều kiện production):

```powershell
.venv/Scripts/python.exe scripts/pair_automation_certificate.py --shadows <shadow-operation.json> --owners <certain-owners.csv> --data-scope <population-id> --expires-at <ISO8601-with-timezone> --output <certificate.json>
```

`--scope production --independent-holdout --representative` chỉ dùng sau khi các điều kiện đó được kiểm chứng và người phụ trách chấp nhận trách nhiệm. Exit 2 nghĩa gate chưa đạt, vẫn có artifact để đối chiếu. Cần kế hoạch sampling không thiên lệch, label ở cấp cụm và theo dõi drift để chọn mẫu validation; không lấy mọi Accept trước đây làm mẫu đại diện.

Cấu hình server:

| Biến | Vai trò | Mặc định |
|---|---|---|
| `PAIR_AUTOMATION_ENABLED` | Cho phép route apply khi mọi gate đạt | `false` |
| `PAIR_AUTOMATION_CERTIFICATE_PATH` | File đã được kiểm tra/phê duyệt ở server | Rỗng |
| `PAIR_AUTOMATION_CERTIFICATE_SHA256` | Hash pin của file đó | Rỗng |
| `PAIR_AUTOMATION_DATA_SCOPE` | Phạm vi population đã được kiểm định | Rỗng |
| `PAIR_AUTOMATION_MAX_COMPONENTS_PER_RUN` | Tổng cụm trong lượt canary của phiên | `20` (1–100) |

Đổi code làm chứng nhận cũ mất hiệu lực. Vận hành cần chạy shadow/kiểm định lại rồi duyệt certificate mới, không sửa hash/code identity của certificate cũ để vượt gate. Trong phiên làm việc này các biến bật/certificate chỉ được giả lập trong test cô lập; không cài production certificate vào Docker đang chạy.

## Dữ liệu và bằng chứng

Tám bộ dữ liệu Drive ở giai đoạn 3 đều là tổng hợp đã được khảo sát; chưa có holdout production đại diện. Kiểm tra lại summary shadow của cả tám bộ bằng gate mới: **0/8 đủ điều kiện production**. Đây là kiểm tra gate trên số liệu giai đoạn 3, không phải chạy lại matcher hay đo chất lượng mới. Hash report gốc được lưu, và hash/bytes của matcher, floor, policy, sinh ứng viên, recovery và guard cụm không đổi so với snapshot đầu giai đoạn 4.

Giới hạn giai đoạn 3 vẫn tồn tại: TC3 bao phủ duyệt chỉ 2,30%, TC5 còn 606 cặp trùng bỏ sót khi sinh ứng viên và 2.229 cặp ứng viên trùng thiếu bằng chứng; TC4/TC13 còn nhiều nhiệm vụ khác người. Giai đoạn 4 không sửa recall hoặc chứng minh giảm thời gian cho người dùng thật. Khi có vùng production đạt kiểm định, một lượt áp dụng sẽ thay việc đọc/Accept từng cặp trong các cụm đó; hiện tại chỉ kiểm chứng hành vi route bằng certificate fixture cô lập.

- Backend bản cuối: **1.218 passed, 2 skipped**, exit 0.
- Frontend bản cuối: **218 passed / 31 files**, exit 0; TypeScript `tsc --noEmit` đạt.
- Production build frontend/backend/worker dùng Compose hiện có đạt; hash policy/workflow/repair trong backend đang chạy khớp workspace.
- PostgreSQL HEAD **0031_pair_automation**; backend/frontend/database healthy, worker running sau thử offline.
- Browser bản cuối: **4/4 PASS**, không có pageErrors.
- Gate kiểm tra lại dữ liệu tổng hợp: **0/8 đủ điều kiện**, runtime chưa bật.
- Git diff whitespace check exit 0; mọi thay đổi giữ ở trạng thái chưa commit.

Browser dùng Edge/API/PostgreSQL/worker thật với dữ liệu tổng hợp localhost, không mock:

1. Cụm rõ tạo được shadow; UI/HTTP từ chối apply khi chưa chứng nhận và decision không đổi.
2. Dừng được lưu qua reload, không undo quyết định đã lưu.
3. Cặp recovery giai đoạn 3 không vào vùng tự động.
4. Tắt worker, xếp shadow, dừng ngay: operation CANCELLED, khóa được giải phóng, Không gộp thủ công thành công trước khi khởi động worker lại.

CLI tạo chứng nhận cũng đã chạy trên shadow và ownership của fixture tổng hợp cục bộ: exit 2, chặn vì synthetic/không holdout/không đại diện/mẫu quá nhỏ, không deploy artifact.

Ca positive apply dùng certificate fixture cô lập và worker thật trong bộ test API; không tuyên bố đây là validation precision hoặc canary trên production. Đã kiểm tra replay, publish, budget sau amendment, token giả/plan bị sửa/cross-run/stale, certificate đổi sau enqueue, code/input/role đổi, queued/running stop và manual repair provenance.

Review độc lập: không Critical, hai Important về dừng và thứ tự lọc/hạn mức đã tái hiện RED và sửa GREEN. Provenance trong repair được xử lý như lỗi đúng đắn audit và đã có hồi quy; text assessment/giao diện đã nhất quán với vùng tự động có kiểm định. Kiểm tra thêm phạm vi kích thước cụm được siết với hồi quy. Không còn hạng mục review bị hoãn.

Artifacts: [gate dữ liệu tổng hợp](pair-automation-stage4-gates.json), [kiểm chứng](pair-automation-stage4-validation.json), `frontend/artifacts/pair-automation-stage4/` và script `frontend/scripts/verify-pair-automation.mjs`. Script browser tạm dừng worker để kiểm tra offline và tự khởi động lại trong finally; chỉ chạy trên Compose QA localhost hiện có, bằng tài khoản STEWARD tổng hợp cục bộ.

## Cơ sở tham khảo

[Dedupe — Choosing a Good Threshold](https://docs.dedupe.io/en/latest/how-it-works/Choosing-a-good-threshold.html) phân biệt mẫu informative của active learning với mẫu đại diện để chọn ngưỡng. [Splink — Cluster Evaluation](https://moj-analytical-services.github.io/splink/topic_guides/evaluation/clusters/overview.html) hướng dẫn xem xét chất lượng cụm, không chỉ từng cạnh. Các nguồn này hỗ trợ cách tách validation khỏi thao tác duyệt và kiểm tra đầu ra theo cụm; chúng không chứng nhận precision production cho P-119.
