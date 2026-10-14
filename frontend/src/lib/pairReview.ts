export const number = (value: number) => value.toLocaleString('vi-VN');
export const fieldLabels: Record<string,string> = { name:'Họ tên', phone:'Điện thoại', email:'Email', address:'Địa chỉ', dob:'Ngày sinh', government_id:'Giấy tờ định danh' };
export const groups: Record<string,{title:string;description:string;tone:string}> = {
  A:{title:'Có điểm neo nhận dạng',description:'Có tên phù hợp cùng thông tin liên hệ hoặc giấy tờ khớp. Cần đối chiếu trước khi Accept.',tone:'green'},
  C:{title:'Cần bổ sung bằng chứng',description:'Có tín hiệu liên hệ hoặc định danh nhưng thiếu thông tin đối chiếu cần thiết.',tone:'amber'},
  D:{title:'Có thông tin mâu thuẫn',description:'Có trường mâu thuẫn. Duyệt thủ công và các ràng buộc vẫn được kiểm tra.',tone:'red'},
  BLOCKED:{title:'Bị ràng buộc chặn',description:'Cannot-link hoặc mâu thuẫn đã xác minh. Mô hình và LLM không thể cấp quyền Accept.',tone:'red'},
  UNCLASSIFIED:{title:'Chưa phân nhóm',description:'Chưa đến bước phân nhóm vì thiếu bằng chứng hoặc bị ràng buộc chặn. Xem lý do từng cặp.',tone:'neutral'},
};
export const subgroups: Record<string,string> = {
  C_REVIEW_NAME_FULL_DOB:'Tên và ngày sinh đầy đủ',C_REVIEW_FULL_DOB_DETAIL_ADDRESS:'Ngày sinh và địa chỉ cụ thể',
  C_REVIEW_NAME_UNVERIFIED_ID:'Tên và giấy tờ chưa xác minh',C_REVIEW_NAME_NEAR_CONTACT_DOB:'Tên, liên hệ gần khớp và ngày sinh',C_REVIEW_NAME_NEAR_CONTACT_ADDRESS:'Tên, liên hệ gần khớp và địa chỉ',
  A1_NAME_PHONE:'Tên và điện thoại',A2_NAME_EMAIL:'Tên và email',A3_NAME_PHONE_DOB:'Tên, điện thoại và ngày sinh',A3_NAME_GOV_ID:'Tên và giấy tờ định danh',
  C1_EMAIL_DOB:'Email và ngày sinh',C2_PHONE_NAME:'Điện thoại và tên',C3_GOV_ID_ONLY:'Giấy tờ, thiếu xác nhận tên',C_MULTI:'Nhiều tín hiệu chưa đủ',C_NAME_ANCHOR_WEAK:'Tên tương đồng yếu',C_NAME_ANCHOR_MISSING:'Thiếu xác nhận tên',
  D1_NAME_PHONE_CONFLICT:'Điện thoại mâu thuẫn',D2_NAME_CONFLICT:'Họ tên mâu thuẫn',D3_IDENTITY_CONFLICT:'Email hoặc giấy tờ mâu thuẫn',D4_DOB_CONFLICT:'Ngày sinh mâu thuẫn',D_MULTI:'Nhiều trường mâu thuẫn',
  D_UNVERIFIED_ID_CONFLICT:'Giấy tờ chưa xác minh khác nhau',C_INSUFFICIENT_DATA:'Không có dữ liệu hữu ích để đối chiếu',C_NO_VALID_IDENTITY_ANCHOR:'Chưa có điểm neo nhận dạng',C_INSUFFICIENT_EVIDENCE:'Bằng chứng chưa đủ mạnh',
  PERSON_ORG_MISMATCH:'Khác loại thực thể',PERSON_POLICY_OUT_OF_SCOPE:'Ngoài phạm vi cá nhân',VERIFIED_GOV_ID_CONFLICT:'Giấy tờ đã xác minh mâu thuẫn',VERIFIED_DOB_CONFLICT:'Ngày sinh đã xác minh mâu thuẫn',STEWARD_NEGATIVE_DECISION:'Có cannot-link từ Deny',
};
export const subgroupLabel = (key?: string) => key ? subgroups[key] || key : 'Chưa có Subgroup';
export const groupLabel = (key?: string) => groups[key || 'UNCLASSIFIED']?.title || key || 'Chưa phân nhóm';
export const baselineLabels: Record<string,string> = {MERGEABLE:'Đủ bằng chứng gộp',REVIEW:'Cần duyệt thủ công',INSUFFICIENT_EVIDENCE:'Thiếu bằng chứng',NOT_MERGEABLE:'Bị ràng buộc chặn',BLOCKED:'Bị ràng buộc chặn',NON_MATCH:'Không phù hợp',DEFERRED:'Chờ bổ sung'};
export const decisionLabels: Record<string,string> = {UNDECIDED:'Chưa duyệt',ACCEPTED:'Đã Accept',NON_MATCH:'Đã xác nhận khác thực thể (Deny)',DENIED:'Đã xác nhận khác thực thể (Deny)',REJECTED:'Giữ riêng liên kết',DEFERRED:'Đang hoãn'};
export const reasons: Record<string,string> = {CLUSTER_REVIEW_VETO:'Liên kết đã bị từ chối nhưng còn đường ghép gián tiếp; đối chiếu các cặp liên quan',CLUSTER_PAIR_PERMISSION_CONFLICT:'Liên kết không đủ quyền ghép hoặc bằng chứng đã thay đổi',CLUSTER_CANNOT_LINK_CONFLICT:'Có hai bản ghi không được cùng nhóm, kể cả qua liên kết gián tiếp',CLUSTER_NEGATIVE_LINK_CONFLICT:'Ràng buộc khác thực thể xung đột với đường ghép hiện có',CLUSTER_SIZE_LIMIT:'Nhóm vượt giới hạn thành viên; kiểm tra các liên kết trong nhóm',CLUSTER_VERIFIED_ID_CONFLICT:'Thành viên có giấy tờ đã xác minh mâu thuẫn',SAME_TYPE_GOV_ID_CONFLICT:'Giấy tờ cùng loại không khớp',VERIFIED_GOV_ID_CONFLICT:'Giấy tờ đã xác minh mâu thuẫn',VERIFIED_DOB_CONFLICT:'Ngày sinh đã xác minh mâu thuẫn',NO_VALID_IDENTITY_ANCHOR:'Thiếu điểm neo nhận dạng',STEWARD_NEGATIVE_DECISION:'Có quyết định Deny ngăn cùng nhóm',REVIEW_VETO:'Liên kết này đã bị từ chối',PERSON_ORG_MISMATCH:'Khác loại thực thể',PERSON_POLICY_OUT_OF_SCOPE:'Ngoài phạm vi dữ liệu cá nhân',PAIR_MERGE_BLOCKED:'Chưa đủ điều kiện Accept',NON_HARD_CONFLICT:'Có thông tin cần đối chiếu'};
Object.assign(reasons, {
  REVIEW_SUPPORT_NAME_FULL_DOB:'Tên phù hợp, cùng ngày sinh đầy đủ; cần duyệt',
  REVIEW_SUPPORT_FULL_DOB_DETAIL_ADDRESS:'Cùng ngày sinh đầy đủ và địa chỉ cụ thể; cần duyệt',
  REVIEW_SUPPORT_NAME_UNVERIFIED_ID:'Tên phù hợp, giấy tờ chưa xác minh trùng; cần duyệt',
  REVIEW_SUPPORT_NAME_NEAR_CONTACT_DOB:'Tên phù hợp, liên hệ gần khớp và ngày sinh; cần duyệt',
  REVIEW_SUPPORT_NAME_NEAR_CONTACT_ADDRESS:'Tên phù hợp, liên hệ gần khớp và địa chỉ cụ thể; cần duyệt',
  SOFT_ACCEPT_REASON_REQUIRED:'Cần bổ sung lý do đối chiếu cho quyết định Accept có xung đột mềm',
  CLUSTER_GUARD_UNION_BUDGET:'Chưa kiểm tra xong các liên kết trong nhóm vì đạt giới hạn xử lý. Tạo các nhóm đủ điều kiện hoặc thử lại sau khi xử lý các vùng còn chờ',
  CLUSTER_GUARD_RUN_BUDGET:'Chưa kiểm tra xong các liên kết trong phiên vì đạt giới hạn xử lý. Tạo các nhóm đủ điều kiện hoặc thử lại sau khi xử lý các vùng còn chờ',
  ASSESSMENT_ACCEPTED_CONFLICT:'Đánh giá mới phát hiện ràng buộc mâu thuẫn với quyết định cùng thực thể; đối chiếu lại các bản ghi',
  CLUSTER_CONFIRMED_CANNOT_LINK:'Có quyết định khác thực thể ngăn các thành viên cùng nhóm, kể cả qua liên kết gián tiếp',
  CLUSTER_NEGATIVE_EDGE:'Có ràng buộc ngăn hai thành viên cùng nhóm; kiểm tra các quyết định và nguồn ràng buộc',
  CLUSTER_BRIDGE_MISSING_EVIDENCE:'Liên kết gián tiếp chưa đủ bằng chứng giữa các thành viên; đối chiếu các cặp liên quan',
  CLUSTER_GOV_ID_CONFLICT:'Các thành viên có giấy tờ định danh mâu thuẫn; đối chiếu nguồn và phạm vi giấy tờ',
  CLUSTER_SHARED_VALUE_DOMINANT:'Thông tin liên hệ dùng chung trong nhóm chưa đủ để xác nhận cùng thực thể',
  CLUSTER_SIZE_EXCEEDED:'Nhóm vượt giới hạn thành viên; kiểm tra các liên kết nối nhóm',
});
export const reasonLabel = (value?: string) => {
  if (!value) return 'Cần kiểm tra thêm bằng chứng';
  if (reasons[value]) return reasons[value];
  if (value.startsWith('CLUSTER_SOURCE_UNIQUE_CONFLICT_')) return `Nhóm vi phạm quy tắc duy nhất của nguồn ${value.slice('CLUSTER_SOURCE_UNIQUE_CONFLICT_'.length)}`;
  if (value.startsWith('CLUSTER_SOURCE_DOMINANCE_')) return `Nguồn ${value.slice('CLUSTER_SOURCE_DOMINANCE_'.length)} chiếm ưu thế trong nhóm; kiểm tra các bản ghi cùng nguồn`;
  return 'Cần kiểm tra thêm bằng chứng và ràng buộc';
};
export const stateLabels: Record<string,string> = {EX:'Khớp chính xác',NX:'Khớp sau chuẩn hóa',NA:'Biến thể tương đương',HS:'Tương đồng cao',MS:'Tương đồng vừa',WS:'Tương đồng thấp',CF:'Mâu thuẫn',DF:'Khác nhau',MI:'Thiếu thông tin',NC:'Chưa đối chiếu',CL:'Gần nhau'};
export const valueText = (value: unknown): string => value == null || value === '' ? '—' : Array.isArray(value) ? value.map(valueText).join(' · ') : typeof value === 'object' ? Object.entries(value).map(([k,v])=>`${fieldLabels[k] || k}: ${valueText(v)}`).join('; ') : String(value);
export const recommendationLabel = (value?:string) => ({LIKELY_SAME_PERSON:'Có khả năng cùng người',LIKELY_DIFFERENT_PERSON:'Có khả năng khác người',INSUFFICIENT_BASIS:'Thiếu căn cứ'}[value || ''] || 'Chưa có đề xuất');

export const adviceSkipLabel = (key:string) => ({CLEAR_BLOCKED_OR_REVIEWED:'Luật/mô hình đã rõ, bị chặn hoặc đã duyệt',ALREADY_REVIEWED:'Đã có quyết định',HARD_CONSTRAINT:'Có ràng buộc cứng',NO_USEFUL_INFORMATION:'Thiếu dữ liệu hữu ích',MODEL_CLEAR:'Mô hình đã có đề xuất rõ',RULE_CLEAR:'Mẫu luật rõ ràng',RULE_CLEAR_DIFFERENT:'Dấu hiệu khác nhau rõ ràng',ALREADY_ASSESSED:'Đã có đề xuất LLM',BATCH_LIMIT:'Ngoài giới hạn đợt này'}[key] || 'Chưa đáp ứng điều kiện đánh giá');
