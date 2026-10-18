import {expect,it} from 'vitest';
import {pairEvidenceSummary} from './pairEvidenceSummary';
import type {PairBaseline} from './workflowContracts';
const base={decision:'REVIEW',score:6,id:'p',left_id:'a',right_id:'b',field_evidence:[]} satisfies PairBaseline;
it('puts exact identity anchors and conflicts first without inventing a probability',()=>{
 const text=pairEvidenceSummary({...base,field_evidence:[
  {field:'name',state:'HS',similarity:.9},{field:'phone',state:'EX',similarity:1},
  {field:'email',state:'SH',similarity:1},{field:'dob',state:'CF',similarity:0},
 ]});
 expect(text).toContain('Khớp điện thoại');expect(text).toContain('mâu thuẫn ngày sinh');
 expect(text).not.toContain('Khớp email');expect(text).not.toContain('%');
});
it('keeps insufficient and blocked conclusions ahead of matching fields',()=>{
 expect(pairEvidenceSummary({...base,decision:'INSUFFICIENT_EVIDENCE'})).toContain('Chưa đủ bằng chứng nhận dạng; giữ riêng');
 expect(pairEvidenceSummary({...base,decision:'NOT_MERGEABLE',reason:'VERIFIED_DOB_CONFLICT'})).toContain('Ngày sinh đã xác minh mâu thuẫn');
});
it('explains manual recovery without hiding contradictory fields or inventing confidence',()=>{
 const text=pairEvidenceSummary({...base,review_support:{policy_version:'person-review-support-v1',kind:'FULL_DOB_DETAIL_ADDRESS',fields:['dob','address']},field_evidence:[{field:'name',state:'CF',similarity:.3}]});
 expect(text).toContain('ngày sinh đầy đủ');expect(text).toContain('địa chỉ cụ thể');
 expect(text).toContain('cần duyệt');expect(text).toContain('mâu thuẫn họ tên');expect(text).not.toContain('%');
});
it('shows an unverified identifier contradiction beside recovered evidence',()=>{
 const text=pairEvidenceSummary({...base,unverified_identifier_relation:'DIFFERENT_VALUE',review_support:{policy_version:'person-review-support-v1',kind:'NAME_FULL_DOB',fields:['name','dob']}});
 expect(text).toContain('mâu thuẫn giấy tờ định danh');
});
