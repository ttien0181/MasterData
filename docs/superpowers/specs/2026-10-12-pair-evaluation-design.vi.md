# Đánh giá cặp bằng luật, mô hình và LLM hỗ trợ người duyệt

Ngày: 2026-10-12. Trạng thái: thiết kế để người dùng duyệt; chưa triển khai.

## Mục tiêu và các lựa chọn đã được người dùng xác nhận

Triển khai đủ năm bước: luật xử lý ràng buộc/mẫu rõ ràng; mô hình đánh giá bằng chứng mềm đã kiểm định; LLM chỉ xem cặp mơ hồ có thông tin hữu ích; đề xuất cùng người/khác người/thiếu căn cứ kèm bằng chứng; người duyệt quyết định, backend giữ ràng buộc và cannot-link.

- VALID chỉ mô tả đúng định dạng. Giấy tờ khác số chỉ chặn cứng khi hai bên đã xác minh, cùng loại, cùng phạm vi cấp và thuộc hợp đồng định danh duy nhất được hỗ trợ.
- Cho phép chọn thêm TC tổng hợp từ thư mục benchmark đã cấp, mỗi TC dùng dữ liệu tối đa 10k. Train/dev/test độc lập theo thực thể, có cả nhãn khớp và không khớp. Phạm vi kiểm định phải được công bố.
- LLM được xem tên/địa chỉ cần đối chiếu và tín hiệu so sánh. Không gửi đầy đủ điện thoại, email, số giấy tờ; không gửi DOB nguyên giá trị, raw_fields, UUID nội bộ hoặc nhãn ground truth.
- Tính lại phiên `3449137c-8f4d-41cc-9152-fa12768aa355` bằng phiên bản đánh giá mới. Giữ kết quả cũ để kiểm toán, quyết định đã lưu và cannot-link. Xung đột với quyết định cũ phải được báo riêng.
- Deny tiếp tục là cannot-link, kể cả liên kết gián tiếp. Bulk chỉ thay đổi cặp UNDECIDED/DEFERRED; Accept thủ công bỏ qua và báo cặp bị chặn.
- Dùng Compose hiện có; không tạo bất kỳ file Compose mới. Giữ TanStack Query, trạng thái bốn tab, cache Group/Subgroup và phân trang 10 cặp.

## Bằng chứng từ code và phiên hiện có

Phiên có 79.364 cặp: A=34, C=0, D=418; 50.659 bị SAME_TYPE_GOV_ID_CONFLICT, 1.623 bị PERSON_ORG_MISMATCH, 26.630 bị NO_VALID_IDENTITY_ANCHOR. Hai nhánh cuối của matcher trả Group/Subgroup rỗng trước classifier; API gom chúng thành UNCLASSIFIED. Artifact SCORE ghi decision_semantics_v2=false.

Các điểm đã xác minh:

- `pipeline/matching.py`: ràng buộc chạy trước scoring; evidence floor chạy trước grouping. A1 che mẫu A3 cụ thể hơn; C2_PHONE_NAME không thể chạy tới.
- `pipeline/policy_engine.py`: đọc YAML subgroups để kiểm tra eligibility; không dùng predicates để phân nhóm theo ưu tiên trong YAML.
- `pipeline/decision_semantics.py`: đã có điều kiện giấy tờ được xác minh và namespace, nhưng phiên hiện có dùng semantics cũ. Không mặc định bật toàn bộ engine v2.
- `pipeline/standardization.py`: cần nối hợp đồng metadata xác minh từ nguồn vào normalized snapshot; không suy trạng thái xác minh từ số ký tự hoặc VALID.
- `pipeline/scoring_engine.py`: có Splink adapter và kiểm tra model/manifest/hash; chưa tìm thấy artifact model đã được kiểm định và duyệt trong các đường dẫn đã khảo sát.
- `benchmark/docs/evaluation_protocol.md`: bộ 1.200 nhãn hiện có chỉ dành cho test; không được huấn luyện, calibration hay tune trên bộ này.
- `llm/gateway.py`, `llm/policy.py`: Gemini chính, OpenRouter dự phòng; hiện chỉ có ADDRESS_COMPARE và EXPLANATION. Explanation cấm khuyến nghị gộp/từ chối. Cần purpose mới, giữ giới hạn cho purpose cũ.
- `workflow_jobs.py`, `resolution_service.py`: DECIDE của phiên dừng trước annotation địa chỉ. LLM hỗ trợ cặp phải có job riêng, không dựa vào annotation lúc tạo nhóm.
- `workflow_review.py`, `workflow_bulk.py`, `workflow_finalize.py`: hiện dùng baseline/pinned profile ở nhiều bước. Phải chuyển đồng bộ sang bộ đọc đánh giá có hiệu lực; chỉ đổi UI sẽ không sửa logic.

## Lựa chọn kỹ thuật

1. Chỉ mở rộng luật: ít phụ thuộc nhưng không đáp ứng mô hình và LLM được yêu cầu.
2. Tái sử dụng Splink và gateway hiện có, bổ sung đánh giá có phiên bản và AI hỗ trợ người duyệt: lựa chọn đề xuất. Có provenance, rollback và tận dụng hợp đồng hiện có.
3. Thay matcher bằng mô hình khác và gateway mới: chưa chọn; mở rộng quá nhiều phạm vi và mất các kiểm tra đang có.

Không nới validation của Splink để hợp thức hóa một model chưa kiểm định. Model benchmark không tự được đánh dấu production_approved cho dữ liệu thực tế. Đánh giá benchmark và quyền bật review assistance được ghi riêng theo phạm vi sử dụng.

## Hợp đồng đánh giá và lưu trữ

Tách bốn thành phần: bằng chứng/ràng buộc của luật; kết quả mô hình mềm; đề xuất LLM; quyết định người duyệt. Chỉ luật và quyết định người duyệt quyết định quyền gộp. Điểm mô hình và LLM không cấp quyền AUTO hoặc tự tạo Deny.

Giữ `WorkflowPair.baseline` và artifact DECIDE cũ bất biến. Bổ sung assessment của phiên và evaluation của cặp có phiên bản; dùng migration tăng dần sau khi kiểm tra schema head thực tế. Không ghi đè những migration đã chạy.

Assessment của phiên chứa: run_id, input hash, policy/evaluator version và hash, profile đánh giá riêng, model/calibrator/report hash, snapshot revision, trạng thái, thống kê, active evaluation pointer. Evaluation của cặp chứa: pair_id, assessment_id, rule_result, constraint codes, nhóm/lý do, soft_result và hash. Bản công bố đã hoàn thành là bất biến; các attempt LLM được lưu riêng, không sửa lịch sử đã công bố.

Một bộ đọc chung chọn assessment có hiệu lực; nếu chưa có thì đọc baseline cũ. UI, count/filter, preview bulk, worker bulk, eligibility, completion blockers và cluster snapshot đều dùng cùng assessment_id. Không viết lại engine selection/profile cũ của phiên.

Model result gồm raw match weight/probability, xác suất sau calibration nếu có, bằng chứng theo trường và thông tin phiên bản. Phân biệt rõ rule score với xác suất; tên nhãn UI phải công bố phạm vi benchmark tổng hợp.

## Luật và taxonomy đầy đủ

Metadata verification có giá trị verified/unverified/unknown và namespace/type tường minh. Thiếu metadata có nghĩa unknown. VALID được giữ riêng. Mapping nguồn và boolean parsing phải tường minh; không ép chuỗi 'false' thành True. Trạng thái verified chỉ nhận từ metadata nguồn được cấu hình đáng tin; LLM hoặc similarity không được cấp verified.

Một classifier duy nhất dùng bằng chứng của luật, có ưu tiên mẫu cụ thể trước mẫu chung. Các mẫu nhiều điều kiện có thể được lưu trong matched_subgroups; primary subgroup có thứ tự ổn định, không lệch với mô tả UI. Xóa hoặc sửa định nghĩa C2 trùng A1; không duy trì nhánh không thể chạy tới. Chuẩn hóa là tag độc lập, không phải Group B cạnh tranh với nhóm bằng chứng.

Mọi cặp có nhóm và lý do, kể cả trường hợp bị chặn hoặc thiếu căn cứ:

| Nhóm | Subgroup/lý do điển hình |
|---|---|
| Có điểm neo nhận dạng | Tên + điện thoại; tên + email; tên + giấy tờ; tên + điện thoại + DOB tương thích |
| Cần đối chiếu thêm | Có tín hiệu mềm nhưng thiếu điểm neo; thiếu dữ liệu; liên hệ dùng chung; tín hiệu còn mơ hồ |
| Có mâu thuẫn cần kiểm tra | Tên/DOB/liên hệ/giấy tờ chưa xác minh khác; nhiều trường xung đột |
| Bị ràng buộc chặn gộp | Cannot-link; khác loại thực thể; giấy tờ đã xác minh xung đột theo hợp đồng định danh |
| Có bằng chứng không khớp | Mẫu luật được kiểm định đủ căn cứ không khớp; không suy kết luận này từ việc chỉ thiếu điểm neo |

Mô hình/LLM bổ sung recommendation, không tự đổi nhóm bằng chứng của luật hoặc quyết định người duyệt. Nhóm taxonomy không đồng nghĩa quyền Accept. Lỗi tính toán/policy ngoài phạm vi được hiển thị thành trạng thái và lý do cụ thể, không giả thành kết quả đã đánh giá.

## Huấn luyện, calibration và kiểm định

Dùng dữ liệu đầu vào/normalized snapshot phù hợp từ các TC được phép, không học từ scored_pairs hay quyết định Accept hàng loạt. Kiểm tra đầy đủ mapping nhãn và provenance; cặp không có nhãn được giữ unknown. Nhãn synthetic chỉ chứng minh phạm vi synthetic, không thay gold thực tế đã được con người phân xử.

Chia theo thực thể trước sinh cặp. Quét trùng thực thể/bản ghi giữa TC và các tập bằng ID nguồn cùng fingerprint dữ liệu; không chỉ thêm prefix TC rồi tuyên bố độc lập. Ghi seed, source hash, record/pair count, tỷ lệ nhãn và lịch sử sử dụng từng tập. Nếu không chứng minh được độc lập hoặc thiếu một lớp nhãn thì không phát hành model.

Fit Splink trên train; calibration và lựa chọn vùng mơ hồ trên dev; giữ test tách biệt tới khi cấu hình đã đóng băng. Sinh candidate và so sánh bằng pipeline thật. Features không có entity_id, label, pair ID, source-derived gold hoặc output matcher cũ. Giữ null là thiếu dữ liệu, không coi là xung đột. Giữ score/model/calibrator trong namespace riêng.

Báo candidate recall, pair precision/recall, false positive/negative, calibration, lượng cần duyệt, độ phủ recommendation, runtime và giới hạn mẫu. So sánh rules với rules+model trên cùng gold và pair universe. Unknown không thành negative; không suy pair accuracy thành cluster accuracy.

Không đặt trước các ngưỡng 0.9/0.1 hay accuracy tùy ý để gọi là 'đã kiểm định'. Ngưỡng routing phải được lựa chọn trên dev, đóng băng cùng artifact và báo rõ trade-off trên test. Phiên bản đầu chỉ trợ giúp/xếp hạng người duyệt; chưa cấp quyền AUTO. Báo cáo không đạt hoặc không đủ bằng chứng làm model không khả dụng, UI báo nguyên nhân, không dùng score mặc định giả xác suất.

Không bật model cho nguồn dữ liệu thực tế ngoài phạm vi đã kiểm định chỉ vì unit test PASS. Artifact phải phân biệt evaluated benchmark scope, review-assistance release scope và production approval. Evidence report được người dùng xem trước khi mở rộng phạm vi.

## Routing và purpose LLM mới

Bổ sung purpose PAIR_ASSESSMENT vào gateway/policy hiện có. Giữ ADDRESS_COMPARE và EXPLANATION với contract hiện tại. Dùng allowlist và validator riêng cho purpose mới; không bỏ kiểm tra chung của provider, model, freshness, quota, budget, retention, circuit hoặc cache.

Chỉ chọn cặp chưa duyệt/đang hoãn, không bị chặn cứng, có đủ thông tin để đối chiếu và còn mơ hồ sau luật/model. Cặp quá thiếu dữ liệu thành 'Thiếu căn cứ/cần bổ sung', không tự gọi LLM. Nếu model chưa sẵn sàng, chỉ route mẫu mơ hồ theo luật có định nghĩa rõ và công bố trạng thái thiếu model.

Payload có opaque token cho cặp, tên/địa chỉ được giới hạn độ dài, field states, quality/verification, tín hiệu khác DOB/contact/gov_id, bằng chứng và lý do mô hình khi hợp lệ. Không dùng raw_fields wholesale; không gửi số điện thoại/email/giấy tờ nguyên giá trị hoặc ground truth. Nội dung tên/địa chỉ là dữ liệu không đáng tin; prompt không được coi đó là chỉ dẫn.

Kết quả có schema đóng: assessment = LIKELY_SAME_PERSON | LIKELY_DIFFERENT_PERSON | INSUFFICIENT_BASIS; evidence_refs tới các field/tín hiệu đã gửi; reason_codes; explanation_vi; limitations. Không dùng confidence tự khai của LLM làm xác suất. Validator kiểm tra refs, enum, độ dài, dữ liệu nhạy cảm và claim không được hỗ trợ; output sai không được phục vụ như đề xuất hợp lệ.

LLM không sửa baseline, rule evidence, verification, score luật, eligibility hoặc Accept/Deny. Kết quả khác luật vẫn có thể được hiển thị kèm mâu thuẫn để người duyệt xem xét; không nâng quyền gộp.

Job LLM chạy bất đồng bộ với queue/lease/fencing hiện có; không giữ DB lock khi gọi mạng. Không tạo queue riêng. Trạng thái: queued/running/completed/insufficient/failed/budget-limited/stale. Cache theo input/baseline/model/prompt/schema/policy hash; không đọc lẫn giữa các phiên/người dùng. Công bố kiểm tra assessment đang có hiệu lực và revision cặp; stale response bị loại. Quyết định ở cặp khác không làm mất mọi đề xuất hợp lệ không liên quan.

Tái sử dụng Gemini/OpenRouter và các giới hạn đã cấu hình. Purpose mới có giới hạn mỗi phiên tường minh nằm trong tổng ngân sách hiện có; không tự nâng trần hoặc chuyển provider ngoài allowlist. Canary thật dùng benchmark tổng hợp được phép, báo lượng gọi/chi phí; không dispatch toàn bộ phiên khi chưa kiểm chứng routing và ngân sách.

## Tính lại phiên hiện có và tạo nhóm

Reassessment là operation có request_id và expected workflow/decision/assessment revision. Chỉ chủ sở hữu có quyền phù hợp thực hiện; chặn khi có operation xung đột. Chạy trên snapshot chuẩn hóa của phiên, tính lại bằng chứng đã bị hard-stop cũ bỏ qua, giữ universe và pair_id để nối quyết định.

Publish atomically assessment mới sau khi xác minh input/profile/model/policy và quyền/lease. Nếu người dùng thay quyết định khi worker đang chạy thì không tự ghi đè; operation retry/reconcile theo revision. Assessment cũ vẫn có thể đọc; rollback chỉ đổi pointer có audit, không xóa quyết định hay cannot-link.

Giữ ACCEPTED/NON_MATCH/REJECTED/DEFERRED đã lưu. Kiểm tra quyết định Accepted trước đó với ràng buộc mới và cannot-link; xung đột được ghi và chặn hoàn tất cho tới khi xử lý. Không tự sửa quyết định của người dùng.

Bulk preview/job đóng băng cả assessment identity cùng filter/selection và revision. Khi assessment đổi, selection cũ được hủy theo contract cache hiện có. Cặp đã có quyết định không bị bulk sửa lại; cặp bị chặn được skip và báo rõ.

Finalize và cluster snapshot dùng assessment có hiệu lực, cùng input hash và rule profile đánh giá mới. Tiếp tục kiểm tra cannot-link trên toàn component, kể cả hai bản ghi không có candidate trực tiếp; không chỉ kiểm tra cặp trên màn hình. Không tự tái công bố cluster/Golden của phiên đã chốt; việc mở lại vẫn đi qua luồng reopen hiện có.

## Giao diện và cache

Hiển thị riêng: loại bằng chứng, trạng thái duyệt, kết quả luật, đánh giá mô hình và đề xuất AI. Dùng nhãn tiếng Việt; giải thích vì sao Accept bị chặn. Card nhóm có tổng số và số còn chờ xử lý, không làm người dùng hiểu số baseline là số chưa xử lý.

Mỗi trang 10 cặp; so sánh field bên cạnh nhau và đánh dấu khác/thiếu/mâu thuẫn. AI recommendation mở được bằng chứng/lý do, thời điểm và phiên bản; 'Thiếu căn cứ' có hướng bổ sung dữ liệu. Không tự tick chọn hoặc kích hoạt Accept/Deny khi AI trả kết quả.

Query keys gồm run_id và assessment_id thích hợp; đổi Group/Subgroup dùng cache riêng và giữ filter/page/scroll. Poll trạng thái chỉ cập nhật phần cần thiết; không reset cả bốn tab. Reassessment hoàn tất invalidates đúng dữ liệu/selection bị ảnh hưởng và giữ trạng thái điều hướng có thể phục hồi.

## Kiểm chứng và điều kiện hoàn tất

- Test luật: giấy tờ đúng định dạng nhưng chưa xác minh không chặn cứng; verified cùng namespace/type xung đột bị chặn; unknown metadata; malformed flags; entity mismatch; cannot-link. Metadata không mất qua import/prepare/persistence.
- Test classifier: mỗi cặp có nhóm/lý do; mẫu cụ thể ưu tiên đúng; không còn C2 chết; config/classifier/UI thống nhất; missing khác conflict.
- Test mô hình: provenance/split không leakage, hai lớp nhãn, calibration chỉ dev; artifact/hashes sai bị từ chối; metric undefined không bị gọi PASS; unsupported domain được công bố.
- Test AI: routing không gọi cho hard-blocked/quá thiếu dữ liệu/đã duyệt; payload không lộ contact/gov_id/DOB; schema/ref/hallucination/injection; budget/timeouts/cache/stale/retry; AI on/off giữ cùng rule result/eligibility/decisions.
- Test workflow: reassessment idempotent/atomic/cancelled/stale; giữ quyết định/history/cannot-link; single/bulk/finalize dùng cùng assessment; transitive cannot-link chặn tạo nhóm; không tự đổi Golden.
- Frontend integration/E2E: nhóm mới/count/filter, 10 cặp, cache Group và bốn tab, tiến độ AI, nút duyệt đúng phạm vi, skip reasons, đổi assessment giữa preview và submit.
- QA thực tế trên Compose hiện có: migrations đúng DB, build/backend/frontend/worker healthy; canary giới hạn; tính lại phiên được chỉ định và kiểm tra count/decision invariants. Chỉ tuyên bố chất lượng trong phạm vi báo cáo kiểm định thực sự đã chạy.

## Phạm vi chưa mở rộng

Không tự Accept/Deny theo mô hình/LLM; không thay provider; không dùng bộ test cũ để train; không tuyên bố model tổng quát cho dữ liệu thực tế; không triển khai hạ tầng mới; không tạo Compose mới; không reset DB hoặc lịch sử người dùng.

## Tự rà soát thiết kế

Đã đối chiếu bốn lựa chọn người dùng với contract: VALID/verified; benchmark bổ sung có giới hạn; payload LLM tối thiểu; reassessment giữ quyết định. Đã kiểm tra sự thống nhất giữa eligibility/bulk/finalize/cache, baseline bất biến và không tự gộp. Ngưỡng mô hình và trạng thái kiểm định là đầu ra của quá trình đo có provenance, không phải con số giả định hoặc model đã được chấp thuận sẵn.
