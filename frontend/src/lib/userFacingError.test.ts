import {expect,it} from 'vitest';
import {userFacingError} from './userFacingError';
import {userRoleLabel,goldenStatusLabel} from './uiLabels';
it('explains authentication, permission and stale-data failures with an action the user can take',()=>{
 expect(userFacingError('Invalid email or password',401)).toContain('Email hoặc mật khẩu chưa đúng');
 expect(userFacingError('Invalid CSRF token',403)).toContain('đăng nhập lại');
 expect(userFacingError({code:'STALE_REVISION',current:7},409)).toContain('tải lại');
 expect(userFacingError({code:'NO_GOLDEN_SELECTED'},409)).toContain('chọn ít nhất một hồ sơ đại diện');
 expect(userFacingError('SQL Exception Traceback',500)).not.toMatch(/SQL|Exception/);
 expect(userFacingError('Không tìm thấy cặp hồ sơ.',404)).toBe('Không tìm thấy cặp hồ sơ.');
 expect(userFacingError(null,0)).toContain('đường truyền');
 expect(userRoleLabel('STEWARD')).toBe('Phụ trách dữ liệu');
 expect(goldenStatusLabel('DRAFT')).toBe('Đang hoàn thiện');
});
