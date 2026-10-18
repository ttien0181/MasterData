export const number = (value: number) => value.toLocaleString('vi-VN');
export const fieldLabels: Record<string,string> = { name:'Họ tên', phone:'Điện thoại', email:'Email', address:'Địa chỉ', dob:'Ngày sinh', government_id:'Giấy tờ định danh' };
import { mvpGroups } from './mvpReview';
export const groups: Record<string,{title:string;description:string;tone:string}> = {
  ...mvpGroups,
  B:{title:'Cần đối chiếu',description:'Có thông tin đối chiếu cần xác minh hoặc mâu thuẫn mềm.',tone:'amber'},
  A:{title:'Đề xuất khớp',description:'Có tên phù hợp cùng thông tin liên hệ hoặc giấy tờ khớp. Cần đối chiếu trước khi Đồng ý gộp.',tone:'green'},
  C:{title:'Chưa đủ căn cứ',description:'Có tín hiệu liên hệ hoặc định danh nhưng thiếu thông tin đối chiếu cần thiết.',tone:'amber'},
  D:{title:'Có thông tin mâu thuẫn',description:'Có trường mâu thuẫn. Duyệt thủ công và các quy định gộp vẫn được kiểm tra.',tone:'red'},
  BLOCKED:{title:'Chưa được phép gộp',description:'Đã có quyết định không gộp hoặc thông tin đã xác minh không khớp. Hệ thống không cho gộp hai hồ sơ này.',tone:'red'},
  UNCLASSIFIED:{title:'Chưa phân nhóm',description:'Chưa đến bước phân nhóm vì thiếu thông tin đối chiếu hoặc bị quy định gộp chặn. Xem lý do từng cặp.',tone:'neutral'},
};
export const subgroups: Record<string,string> = {
  A_MULTI_SIGNAL:'Nhiều tín hiệu hỗ trợ',A_CONTACT:'Liên hệ phù hợp',A_IDENTIFIER:'Định danh phù hợp',A_PROPOSED_MATCH:'Đề xuất khớp',
  B_AGENT_DISAGREEMENT:'Kết quả đối chiếu khác nhau',B_SOFT_CONFLICT:'Mâu thuẫn cần đối chiếu',B_RECOVERED_SUPPORT:'Thông tin đối chiếu bổ sung',B_PARTIAL_EVIDENCE:'Cần xác minh thêm',
  C_MISSING_DATA:'Dữ liệu thiếu hoặc không hợp lệ',C_NO_ANCHOR:'Thiếu thông tin xác nhận',
  C_REVIEW_NAME_FULL_DOB:'Tên và ngày sinh đầy đủ',C_REVIEW_FULL_DOB_DETAIL_ADDRESS:'Ngày sinh và địa chỉ cụ thể',
  C_REVIEW_NAME_UNVERIFIED_ID:'Tên và giấy tờ chưa xác minh',C_REVIEW_NAME_NEAR_CONTACT_DOB:'Tên, liên hệ gần khớp và ngày sinh',C_REVIEW_NAME_NEAR_CONTACT_ADDRESS:'Tên, liên hệ gần khớp và địa chỉ',
  A1_NAME_PHONE:'Tên và điện thoại',A2_NAME_EMAIL:'Tên và email',A3_NAME_PHONE_DOB:'Tên, điện thoại và ngày sinh',A3_NAME_GOV_ID:'Tên và giấy tờ định danh',
  C1_EMAIL_DOB:'Email và ngày sinh',C2_PHONE_NAME:'Điện thoại và tên',C3_GOV_ID_ONLY:'Giấy tờ, thiếu xác nhận tên',C_MULTI:'Nhiều tín hiệu chưa đủ',C_NAME_ANCHOR_WEAK:'Tên tương đồng yếu',C_NAME_ANCHOR_MISSING:'Thiếu xác nhận tên',
  D1_NAME_PHONE_CONFLICT:'Điện thoại mâu thuẫn',D2_NAME_CONFLICT:'Họ tên mâu thuẫn',D3_IDENTITY_CONFLICT:'Email hoặc giấy tờ mâu thuẫn',D4_DOB_CONFLICT:'Ngày sinh mâu thuẫn',D_MULTI:'Nhiều trường mâu thuẫn',
  D_UNVERIFIED_ID_CONFLICT:'Giấy tờ chưa xác minh khác nhau',C_INSUFFICIENT_DATA:'Không có dữ liệu hữu ích để đối chiếu',C_NO_VALID_IDENTITY_ANCHOR:'Thiếu thông tin xác nhận nhận dạng',C_INSUFFICIENT_EVIDENCE:'Thông tin đối chiếu chưa đủ mạnh',
  PERSON_ORG_MISMATCH:'Khác loại khách hàng',PERSON_POLICY_OUT_OF_SCOPE:'Ngoài phạm vi cá nhân',VERIFIED_GOV_ID_CONFLICT:'Giấy tờ đã xác minh mâu thuẫn',VERIFIED_DOB_CONFLICT:'Ngày sinh đã xác minh mâu thuẫn',STEWARD_NEGATIVE_DECISION:'Đã có quyết định giữ hai hồ sơ ở hai khách hàng khác nhau',
};
export const subgroupLabel = (key?: string) => key ? subgroups[key] || 'Cần xem lại thông tin' : 'Chưa có nhóm chi tiết';
export const groupLabel = (key?: string) => groups[key || 'UNCLASSIFIED']?.title || 'Chưa phân nhóm';
export const baselineLabels: Record<string,string> = {MERGEABLE:'Đủ thông tin đối chiếu gộp',REVIEW:'Cần duyệt thủ công',INSUFFICIENT_EVIDENCE:'Thiếu thông tin đối chiếu',NOT_MERGEABLE:'Chưa được phép gộp',BLOCKED:'Chưa được phép gộp',NON_MATCH:'Không phù hợp',DEFERRED:'Chờ bổ sung'};
export const decisionLabels: Record<string,string> = {UNDECIDED:'Chưa duyệt',ACCEPTED:'Đã đồng ý gộp',NON_MATCH:'Đã xác nhận khác khách hàng',DENIED:'Đã xác nhận khác khách hàng',REJECTED:'Giữ riêng liên kết',DEFERRED:'Đang hoãn'};
export const reasons: Record<string,string> = {CLUSTER_REVIEW_VETO:'Liên kết đã bị từ chối nhưng còn đường ghép gián tiếp; đối chiếu các cặp liên quan',CLUSTER_PAIR_PERMISSION_CONFLICT:'Liên kết không đủ quyền ghép hoặc thông tin đối chiếu đã thay đổi',CLUSTER_CANNOT_LINK_CONFLICT:'Có hai hồ sơ không được cùng nhóm, kể cả qua liên kết gián tiếp',CLUSTER_NEGATIVE_LINK_CONFLICT:'Ràng buộc khác khách hàng xung đột với đường ghép hiện có',CLUSTER_SIZE_LIMIT:'Nhóm vượt giới hạn thành viên; kiểm tra các liên kết trong nhóm',CLUSTER_VERIFIED_ID_CONFLICT:'Thành viên có giấy tờ đã xác minh mâu thuẫn',SAME_TYPE_GOV_ID_CONFLICT:'Giấy tờ cùng loại không khớp',VERIFIED_GOV_ID_CONFLICT:'Giấy tờ đã xác minh mâu thuẫn',VERIFIED_DOB_CONFLICT:'Ngày sinh đã xác minh mâu thuẫn',NO_VALID_IDENTITY_ANCHOR:'Thiếu thông tin để xác định khách hàng',STEWARD_NEGATIVE_DECISION:'Có quyết định Không gộp ngăn cùng nhóm',REVIEW_VETO:'Liên kết này đã bị từ chối',PERSON_ORG_MISMATCH:'Khác loại khách hàng',PERSON_POLICY_OUT_OF_SCOPE:'Ngoài phạm vi dữ liệu cá nhân',PAIR_MERGE_BLOCKED:'Chưa đủ điều kiện Đồng ý gộp',NON_HARD_CONFLICT:'Có thông tin cần đối chiếu'};
Object.assign(reasons, {
  REVIEW_SUPPORT_NAME_FULL_DOB:'Tên phù hợp, cùng ngày sinh đầy đủ; cần duyệt',
  REVIEW_SUPPORT_FULL_DOB_DETAIL_ADDRESS:'Cùng ngày sinh đầy đủ và địa chỉ cụ thể; cần duyệt',
  REVIEW_SUPPORT_NAME_UNVERIFIED_ID:'Tên phù hợp, giấy tờ chưa xác minh trùng; cần duyệt',
  REVIEW_SUPPORT_NAME_NEAR_CONTACT_DOB:'Tên phù hợp, liên hệ gần khớp và ngày sinh; cần duyệt',
  REVIEW_SUPPORT_NAME_NEAR_CONTACT_ADDRESS:'Tên phù hợp, liên hệ gần khớp và địa chỉ cụ thể; cần duyệt',
  SOFT_ACCEPT_REASON_REQUIRED:'Cần bổ sung lý do đối chiếu cho quyết định Đồng ý gộp có xung đột mềm',
  CLUSTER_GUARD_UNION_BUDGET:'Chưa kiểm tra xong các liên kết trong nhóm vì đạt giới hạn xử lý. Tạo các nhóm đủ điều kiện hoặc thử lại sau khi xử lý các vùng còn chờ',
  CLUSTER_GUARD_RUN_BUDGET:'Chưa kiểm tra xong các liên kết trong phiên vì đạt giới hạn xử lý. Tạo các nhóm đủ điều kiện hoặc thử lại sau khi xử lý các vùng còn chờ',
  ASSESSMENT_ACCEPTED_CONFLICT:'Đánh giá mới phát hiện quy định gộp mâu thuẫn với quyết định cùng khách hàng; đối chiếu lại các hồ sơ',
  CLUSTER_CONFIRMED_CANNOT_LINK:'Có quyết định khác khách hàng ngăn các thành viên cùng nhóm, kể cả qua liên kết gián tiếp',
  CLUSTER_NEGATIVE_EDGE:'Có quy định gộp ngăn hai thành viên cùng nhóm; kiểm tra các quyết định và nguồn quy định gộp',
  CLUSTER_BRIDGE_MISSING_EVIDENCE:'Liên kết gián tiếp chưa đủ thông tin đối chiếu giữa các thành viên; đối chiếu các cặp liên quan',
  CLUSTER_GOV_ID_CONFLICT:'Các thành viên có giấy tờ định danh mâu thuẫn; đối chiếu nguồn và phạm vi giấy tờ',
  CLUSTER_SHARED_VALUE_DOMINANT:'Thông tin liên hệ dùng chung trong nhóm chưa đủ để xác nhận cùng khách hàng',
  CLUSTER_SIZE_EXCEEDED:'Nhóm vượt giới hạn thành viên; kiểm tra các liên kết nối nhóm',
});
export const reasonLabel = (value?: string) => {
  if (!value) return 'Cần kiểm tra thêm thông tin đối chiếu';
  if (reasons[value]) return reasons[value];
  if (value.startsWith('CLUSTER_SOURCE_UNIQUE_CONFLICT_')) return `Nhóm vi phạm quy tắc duy nhất của nguồn ${value.slice('CLUSTER_SOURCE_UNIQUE_CONFLICT_'.length)}`;
  if (value.startsWith('CLUSTER_SOURCE_DOMINANCE_')) return `Nguồn ${value.slice('CLUSTER_SOURCE_DOMINANCE_'.length)} chiếm ưu thế trong nhóm; kiểm tra các hồ sơ cùng nguồn`;
  return 'Cần kiểm tra thêm thông tin đối chiếu và quy định gộp';
};
export const stateLabels: Record<string,string> = {EX:'Khớp chính xác',NX:'Khớp sau chuẩn hóa',NA:'Biến thể tương đương',HS:'Tương đồng cao',MS:'Tương đồng vừa',WS:'Tương đồng thấp',CF:'Mâu thuẫn',DF:'Khác nhau',MI:'Thiếu thông tin',NC:'Chưa đối chiếu',CL:'Gần nhau'};
export const valueText = (value: unknown): string => value == null || value === '' ? '—' : Array.isArray(value) ? value.map(valueText).join(' · ') : typeof value === 'object' ? Object.entries(value).map(([k,v])=>`${fieldLabels[k] || k}: ${valueText(v)}`).join('; ') : String(value);
export const recommendationLabel = (value?:string) => ({LIKELY_SAME_PERSON:'Có khả năng cùng người',LIKELY_DIFFERENT_PERSON:'Có khả năng khác người',INSUFFICIENT_BASIS:'Thiếu căn cứ'}[value || ''] || 'Chưa có đề xuất');

export const adviceSkipLabel = (key:string) => ({CLEAR_BLOCKED_OR_REVIEWED:'Luật/mô hình đã rõ, bị chặn hoặc đã duyệt',ALREADY_REVIEWED:'Đã có quyết định',HARD_CONSTRAINT:'Có quy định bắt buộc',NO_USEFUL_INFORMATION:'Thiếu dữ liệu hữu ích',MODEL_CLEAR:'Mô hình đã có đề xuất rõ',RULE_CLEAR:'Mẫu luật rõ ràng',RULE_CLEAR_DIFFERENT:'Dấu hiệu khác nhau rõ ràng',ALREADY_ASSESSED:'Đã có đề xuất trợ lý AI',BATCH_LIMIT:'Ngoài giới hạn đợt này'}[key] || 'Chưa đáp ứng điều kiện đánh giá');
