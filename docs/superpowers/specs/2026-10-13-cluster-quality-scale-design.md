# Cải thiện chất lượng và quy mô bước tạo nhóm

Ngày: 13/10/2026. Trạng thái: người dùng đã duyệt thiết kế đầy đủ; chưa triển khai.

## Mục tiêu và quyết định đã có

Người dùng yêu cầu xử lý từng vấn đề còn tồn tại trong tài liệu 03, kiểm tra phối hợp
giữa các thay đổi và toàn luồng phân loại cặp → duyệt → nhóm → golden → công bố/xuất.
Ngày 13/10/2026 người dùng chọn **giữ duyệt trước khi gộp; tối ưu chất lượng và tốc độ
trước**. Tự động gộp không phải tiêu chí thành công của đợt này.

Người dùng đã duyệt toàn bộ thiết kế và chọn triển khai lần lượt trực tiếp trong
phiên này, bao gồm nhóm lớn duyệt đến 100, entity_id ổn định và singleton ảo.

Không tạo thêm bất kỳ file Docker Compose nào. File hiện có là `docker-compose.yml`.
Giữ các thay đổi chưa commit trong checkout. Dữ liệu Drive được đọc/tải vào cache
benchmark riêng; không sửa nguồn, manifest hay đáp án. Không đưa truth/scenario labels
vào feature, phân loại, ưu tiên cạnh, phê duyệt hoặc lựa chọn golden.

Thay đổi được triển khai và kiểm tra theo từng giai đoạn. Các lỗi đã được sửa trong
workspace được kiểm tra hồi quy, không viết lại chỉ vì tài liệu mô tả commit cũ.

## Phương án và căn cứ

**Khuyến nghị: cải tiến tăng dần engine hiện có.** Dùng chung kiểm tra consistency,
graph quyết định, policy survivorship và các reader/iterator cho preview/publication/
repair/export. Giữ operations, immutable artifacts, audit, revision và worker lease.
Ưu điểm: so sánh được trước/sau và giữ luồng duyệt; chi phí là migration tương thích
cho định danh, singleton và snapshot. Đây là phương án được thiết kế bên dưới.

Phương án chỉ sửa golden và vòng lặp nhỏ nhanh hơn nhưng không giải quyết đầy đủ
singleton, streaming và khác biệt giữa các đường tạo nhóm. Thay engine bằng dịch vụ
MDM/Splink hoặc bộ tối ưu ILP có phạm vi tích hợp lớn hơn; chưa có bằng chứng cần thay
toàn engine hay giấy phép để phân hoạch lại những cạnh người dùng đã duyệt theo điểm.

Tham khảo tài liệu gốc, không coi sản phẩm khác là bảo đảm cho dữ liệu P-119:

- [AWS Entity Resolution: transitive matching](https://docs.aws.amazon.com/entityresolution/latest/userguide/transitive-matching.html)
  xử lý liên kết qua nhiều luật. [Giới hạn](https://docs.aws.amazon.com/entityresolution/latest/userguide/transitive-matching-limitations.html)
  nêu nguy cơ gộp sai khi sắp luật không đúng; nối bắc cầu không tự chứng minh danh tính.
- [Splink: clustering](https://moj-analytical-services.github.io/splink/api_docs/linker_clustering.html)
  dùng connected components trên cạnh đạt ngưỡng.
  [Graph metrics](https://moj-analytical-services.github.io/splink/topic_guides/evaluation/clusters/graph_metrics.html)
  hỗ trợ điều tra bridge, density và kích thước; một chỉ số riêng không quyết định chất lượng.
- [Reltio: survivorship](https://docs.reltio.com/en/objectives/resolve-potential-matches/potential-matching-at-a-glance/potential-matching-navigation/design-survivorship-rules)
  tách gộp thực thể khỏi chọn giá trị hiển thị, giữ nguồn của các giá trị và có quy tắc
  theo thuộc tính. Áp dụng nguyên tắc policy theo field và giữ provenance cho P-119.

## 1. Baseline và dữ liệu

Dữ liệu do người dùng cung cấp:
[Google Drive](https://drive.google.com/drive/folders/1ZSRF1p6OY9hCglV95jebNcOZusQ_D1Ws).
README mô tả 21 bundle thuộc TC1–TC6, TC8, TC13; nhóm mới có input P-119 cho
100k/1M. TC1–TC5 dùng input source-native, cần adapter trước khi chạy pipeline P-119.
Đợt này ưu tiên TC6, TC8, TC13 vì trực tiếp kiểm tra các nhận xét của tài liệu 03.

Cache 100k nằm ở `artifacts/cluster-improvements-20261013/datasets`.
Mỗi bundle chỉ hợp lệ sau khi đủ mọi file manifest và tất cả SHA-256 khớp.
Đếm lại tám CSV, kiểm tra mapping input/truth và xác nhận không có thiếu endpoint.
TC8 đánh giá cả JUNK: owner null không được coi là một thực thể chung.

Baseline đã chuẩn bị: [báo cáo trước thay đổi](../../benchmark/cluster-quality-scale-20261013-baseline.md).
Ba bộ 100k đủ 57/57 file khớp hash và 300.000 input/truth source keys khớp.
TC6/100k đã chạy đầy đủ pipeline offline: 770.232 candidate, 101,37 giây;
chưa đo toàn luồng review/publication/export. Các số này không là tiêu chí SLA.

README ghi TC6/1M `input_p119/csv-1.csv` lệch checksum. Nếu kiểm tra lại vẫn lệch,
báo INTEGRITY_FAILED và giữ nguyên dữ liệu; không báo PASS hoặc lấy nó làm đối chứng
quy mô. Có thể dùng TC8/1M và TC13/1M nếu đủ file và hash hợp lệ.

Baseline phải ghi hash code chưa commit, phiên bản thư viện, profile/policy, phần
cứng, phạm vi input và trạng thái review. Tách ba phép đo:

1. Matching/candidate trên quan sát thật, không dùng đáp án để sinh cạnh.
2. Clustering trên fixture review được khai báo rõ để thử guard; không gọi đó là
   quyết định thực của steward hay recall workflow production.
3. API/worker/browser từ upload đến export trong DB và object store QA riêng.

Mỗi giai đoạn có report trước/sau. Chạy 100k đầy đủ trước 1M; không suy throughput
từ các component nhỏ, và không tính precision của đường không tạo nhóm là 100%.

## 2. Golden và guard shared-value

Policy survivorship mới có phiên bản và cấu hình theo field. Mặc định cho bản mới:

1. Loại MISSING/INVALID/PLACEHOLDER khỏi giá trị chính của mọi field, kể cả khi
   metadata verified tồn tại. Không nâng quality lên UNKNOWN khi fallback sang raw.
2. Với phone/email, contact SHARED/shared_risk được lưu trong alternatives cùng
   provenance; không tự chọn làm contact cá nhân chính. Nếu không có contact phù
   hợp, primary để trống và hiển thị lý do để người duyệt chọn.
3. Trên ứng viên hợp lệ: verified → quality → source priority của field → timestamp
   nguồn có múi giờ → tie-break ổn định bằng nguồn/external ID/record ID/value.
4. Source priority theo field có fallback sang thứ tự nguồn chung. Không tự suy đoán
   ERP tốt hơn CRM ở một field; cấu hình mặc định giữ thứ tự nguồn đang có.
5. Thời gian chỉ lấy từ source timestamp có múi giờ. External ID chỉ là tie-break,
   không mang nghĩa recency. Golden mới không dùng fallback thời gian của v1.
6. Manual selection/override vẫn do server kiểm tra và lưu audit. Snapshot/golden
   cũ không bị tính lại âm thầm; thay policy làm stale preview và yêu cầu tạo lại draft.

Shared-value guard chỉ đếm contact SH. DF là khác giá trị, không là bằng chứng shared.
WARN không trở thành cannot-link. Dùng cùng tín hiệu trong preview, nhóm và giải thích.

Kiểm tra: CRM placeholder vs ERP verified valid; SHARED vs personal; tất cả contact
shared; verified invalid; thiếu normalized metadata; PARTIAL/UNKNOWN; timezone khác
nhau; timestamp hỏng; nguồn mới; tie; override; và giá trị xuất CSV.

## 3. Chung một đường tạo nhóm và xử lý cầu nối

Tách rõ conclusion của matcher, quyền gộp và quyết định của người duyệt. Assessment
tiếp tục yêu cầu review cho MERGEABLE. Không mở automation hoặc thay threshold.

Cùng một graph/index và bộ consistency kiểm tra được dùng bởi preview, publication,
group approval và repair. Cờ legacy/v2 chọn policy và hợp đồng evidence, không tạo
thêm một vòng manual grouping với các điều kiện bị sao chép.

Các cạnh ACCEPTED tạo component quyết định ổn định theo record ID. Kiểm tra component
đầy đủ trước khi công bố. Một component có quyết định mâu thuẫn không được silently
phân thành nhóm theo thứ tự weight rồi bỏ một cạnh ACCEPTED. Báo vùng, cặp gây chặn,
quyết định và bằng chứng; steward sửa/reject cạnh rồi xem lại preview. Điểm không
được ghi đè quyết định người dùng. Các gợi ý phân hoạch là draft cần duyệt, không
phải kết quả tối ưu toàn cục được chứng nhận.

Chuỗi thiếu evidence trực tiếp giữa hai đầu được đưa vào cùng một draft group để
người dùng duyệt nhóm với lý do. Exact group approval vẫn gắn fingerprint thành viên,
evidence, quyết định và profile. Nó chỉ miễn yêu cầu direct bridge evidence; không
miễn hard constraints, cannot-link, REJECTED/NON_MATCH, nguồn unique hoặc quyền cặp.
Chỉ phê duyệt các cạnh riêng lẻ chưa cấp ngoại lệ cho mọi cặp bắc cầu.

Kiểm tra mọi cặp ràng buộc trong nhóm; không lấy mẫu đại diện rồi bỏ qua ràng buộc.
Giữ cache kiểm tra trong đúng snapshot, không dùng chung cache giữa chế độ exemption
và chế độ thông thường nếu khóa cache không chứa policy/quyền miễn trừ.

Giới hạn thường vẫn 20. Thêm đường **duyệt nhóm lớn** có lý do và preview cho
21–100 thành viên; ngân sách mỗi union ít nhất C(100,2)=4.950 ở đường này. Tất cả
member/evidence được phân trang. Nhóm >100 hoặc vượt ngân sách không bị chia mỗi
20 dòng: giữ nguyên một vùng cần xử lý chính sách với số thành viên và lý do rõ ràng.

Kiểm tra: hoán vị records/edges/pair IDs và weights bằng nhau/None; chain, star,
false bridge; soft conflict có/không lý do; hard/deny/veto; receipt stale; 19/20/21/
100/101; ngân sách; và tương đương preview/publication/repair trong cùng snapshot.

## 4. Vùng publication và công việc lặp

Vùng đang chờ phụ thuộc vào cạnh cần quyết định và membership thực, không vào tất
cả candidate yếu. Giữ nguyên cả nhóm đã chạm pending/conflict; nhóm rời nhau được
công bố riêng. Không công bố singleton cho record unresolved hoặc chưa kiểm tra.

Dựng record lookup, pair lookup, incident edges, membership, frequencies và adjacency
một lần trong snapshot. Dùng BFS/connected components, không quét toàn snapshot cho
mỗi cạnh/nhóm. Reuse region chỉ khi input/profile/policy/quyền và budget phù hợp;
không reset frequencies toàn phiên thành frequencies cục bộ khi compute subset.

Repair preview dùng cùng `compute_publication` và eligibility với publication chính.
Đưa set conflict ra ngoài vòng record. Partial compute có thể kiểm tra vùng conflict
cũ riêng nhưng tái dùng kết quả vùng đã đánh giá, không chạy lại cùng một vùng chỉ để
lấy một loại kết quả khác. Giữ index/cache và batch insert đang có; đo số statement
DB thay vì suy round-trip từ số lần `add` ORM.

Kiểm tra: một pending không giữ cả vùng candidate yếu; hai nhóm rời nhau; conflict
bên trong nhóm; sửa một quyết định; known conflict; retained/reevaluated IDs; cache
vs full compute; recovery/undo vs publication; ngân sách không được nới bằng cache.

## 5. Định danh và lựa chọn thủ công

Tách ID snapshot nhóm (`cluster_id`, vẫn gắn generation) khỏi ID thực thể ổn định
(`entity_id`). API trả cả hai. Export bản mới dùng entity_id làm master_record_id;
có version trong metadata để consumer nhận biết đổi hợp đồng.

Dùng lifecycle các bảng ResolutionEntity/Member/Version hiện có cho run workflow v2;
không có lời hứa nối danh tính giữa hai run độc lập khi chưa có khóa nguồn bền.
Không đổi ID lịch sử trong artifact cũ.

Nhóm không đổi giữ entity_id. Thêm/bớt member giữ ID nếu có đúng một ancestor.
Khi merge nhiều ancestor, giữ entity được tạo sớm nhất, tie-break ID, ghi lineage
cho các ID được thay thế. Khi split, chỉ một nhánh giữ ID cha: nhánh chứa anchor
đã lưu; nếu anchor không còn, chọn overlap lớn nhất rồi ID member nhỏ nhất. Nhánh
còn lại có ID mới. Assignment được tính cho cả generation để một ID không gán
cho hai nhánh; transaction/retry không tạo entity trùng.

SOURCE_CHOICE được giữ nếu nguồn và giá trị vẫn hợp lệ. MANUAL_OVERRIDE khi đổi
membership giữ dưới dạng draft cần đối chiếu, không mất nội dung đã nhập và không
tự được xác nhận cho mọi nhánh split. Override xung đột từ nhiều ancestor được
hiển thị để chọn, không tự chọn theo thứ tự duyệt. Confirmation được reset khi
evidence, membership hoặc golden thay đổi.

## 6. Singleton và lưu trữ theo luồng

Singleton được suy ra từ **eligible universe đã đóng băng của generation** trừ
membership multi-record. Không suy singleton từ record hiện tại không có membership,
vì có thể đó là unresolved hoặc input đã đổi.

Không tạo WorkflowCluster/WorkflowMember/golden JSON cho mọi singleton. Tính golden
khi đọc/xuất, từ source snapshot đã đóng băng với policy của generation. Chỉ lưu row
ngoại lệ khi có lựa chọn thủ công hoặc xác nhận riêng. Xác nhận toàn bộ singleton
là một biên nhận gắn generation/hash; các exception chưa giải quyết vẫn chặn finalize.
Group list/detail/selection/repair, coverage counts và export dùng cùng reader,
trả virtual singleton với ID ổn định, phân trang không trùng/bỏ record.

Snapshot/plan/final result mới lưu manifest và các partition Parquet qua protocol
artifact/object store hiện có. DB giữ metadata, revision, selection, membership và
receipt. Partition phải có checksum, schema version, row count, readback và ownership;
không đánh dấu sẵn sàng bằng việc file local tồn tại. Artifact JSON cũ vẫn đọc được.

Worker đọc records/pairs qua iterator/index disk-backed có batch hữu hạn; không load
lại toàn manifest thành một list/dict để thực thi. Index frequencies, membership và
cannot-links trên disk khi quy mô yêu cầu. API group detail chỉ đọc nhóm, record và
partition cần thiết. Finalization và export cũng stream, không đưa mọi golden/record
vào `WorkflowGeneration.finalized_result` JSON.

Hash JSON trong đường cũ được tính tăng dần với canonical encoding tương thích byte
để không đổi hash/receipt lịch sử. Manifest mới có phiên bản hash rõ ràng. Việc chỉ
stream hash chưa được coi là giải quyết bộ nhớ toàn workflow.

Kiểm tra: old/new artifacts; tamper/missing partition; bounded page reads; singleton
edit/confirm/reopen; partial/unresolved; zero/one/all-singleton; many generations;
export mỗi record đúng một lần; source snapshot immutable; retry/cancel/lease expiry;
không có publication một phần bị lộ từ transaction thất bại.

## 7. Kiểm tra phối hợp và giao diện

Mỗi mục có test tái hiện trước thay đổi, test sau sửa và kiểm tra các bước lân cận.
Thứ tự: golden/guard + lỗi vòng lặp → shared clustering/regions + nhóm lớn → entity
identity/choices → virtual singleton → stream snapshot/finalization/export.
Mỗi phần lưu trữ có migration cộng thêm, reader cũ và kiểm tra downgrade/rollback
theo hợp đồng được hỗ trợ; không migration DB đang dùng trong bước QA.

Luồng tích hợp cần chạy qua route và leased worker thật trong môi trường QA riêng:

1. Upload tám CSV cùng một bundle, chuẩn hóa và phân loại cặp.
2. Review có ACCEPTED/REJECTED/NON_MATCH/DEFERRED và lý do soft conflict.
3. Preview, duyệt chuỗi/nhóm lớn, publication một phần rồi đủ coverage.
4. Chọn golden theo nguồn và manual, xác nhận, finalize, xuất rồi kiểm tra coverage.
5. Reopen, thêm/bớt/merge/split, sửa/undo; kiểm tra ID và draft choices không mất.
6. Quyết định/input/policy đổi sau preview phải stale; retry/cancel/lease hết hạn
   không công bố dữ liệu chưa kiểm tra hoặc làm mất bản đã công bố.

Browser kiểm tra nút, chờ worker, pending/conflict navigation, lý do, phân trang,
refresh và draft đang sửa. Test mock API không thay cho API/worker thật.
Frontend phải đọc hướng dẫn Next trong `frontend/node_modules/next/dist/docs`
trước khi sửa theo `frontend/AGENTS.md`.

Đo candidate recall, pairwise/B³, false co-clusters, JUNK co-clusters, constraint
violations và exact coverage riêng. Đo wall time/RSS/DB statements/disk/bytes read
của từng stage và toàn luồng 100k, sau đó 1M hợp lệ. Ghi cả timeout, budget và
abstention; chưa đặt SLA hoặc ngưỡng auto-merge từ dữ liệu tổng hợp.

## Đối chiếu từng nhận xét của tài liệu 03

| Nhận xét | Xử lý trong thiết kế |
| --- | --- |
| 2.1 tham lam | Component của quyết định đã duyệt; không bỏ cạnh ACCEPTED theo weight; conflict và draft cần steward quyết định. Không tuyên bố tối ưu toàn cục. |
| 2.2 cầu nối | Chuỗi draft + exact group approval, kiểm tra ràng buộc đầy đủ. |
| 2.3 giới hạn 20 | Policy thường 20, đường nhóm lớn duyệt đến 100, overflow có đường xử lý. |
| 2.4 shared DF | Chỉ SH; kiểm tra WARN và hiển thị nhất quán. |
| 2.5 vùng conflict | Graph phụ thuộc quyết định/membership, không khóa bằng candidate yếu. |
| 3.1 ba ngữ nghĩa | Chung graph/consistency/readers, policy có phiên bản. |
| 3.2 không tự động | Quyết định người dùng: giữ review, không coi đây là lỗi cần bật auto. |
| 3.3 ID/override | entity_id ổn định + lineage; manual giữ draft khi cần đối chiếu. |
| 3.4 singleton | Eligible virtual singleton + exception rows. |
| 4.1–4.3 survivorship | Lọc hợp lệ, verified/quality trước nguồn, shared alternative, policy theo field. |
| 4.4 recency v1 | Golden mới chỉ dùng source time; reader lịch sử tương thích. |
| 5.1–5.3 lặp/quét | Chung publication cho repair; index/BFS/cache vùng và compute reuse. |
| 5.4 ghi DB | Giữ batch hiện có; loại writes singleton mặc định; đo statement. |
| 5.5/5.6 artifact/read | Tiếp tục test phân phần/local evidence hiện có; manifest/partition/reader mới. |
| 5.7 approvals | Test scoped query hiện có; dùng index/frequencies đúng snapshot. |
| 5.8 hash | Canonical incremental digest tương thích lịch sử và version manifest mới. |
| 5.9 annotation | Giữ job riêng + lookup đã tối ưu; test stale/failure và bounded read. |
| 6 kiểm thử/quy mô | Metrics đã có + benchmark đầy đủ, tests permutation và integration qua export. |

## Trạng thái thực hiện

Thiết kế đầy đủ đã được người dùng duyệt. Bước tiếp theo là kế hoạch triển khai
trực tiếp theo từng mục và kiểm thử phối hợp. Không có implementation, migration,
deploy hay thay đổi Docker nào được tuyên bố hoàn thành bởi tài liệu này.
