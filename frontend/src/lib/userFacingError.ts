const messages:Record<string,string>={
 'Invalid credentials':'Email hoặc mật khẩu chưa đúng. Bạn kiểm tra lại nhé.',
 'Invalid email or password':'Email hoặc mật khẩu chưa đúng. Bạn kiểm tra lại nhé.',
 'Incorrect email or password':'Email hoặc mật khẩu chưa đúng. Bạn kiểm tra lại nhé.',
 'Email already registered':'Email này đã được đăng ký. Bạn hãy đăng nhập hoặc dùng email khác.',
 'Email already exists':'Email này đã được đăng ký. Bạn hãy đăng nhập hoặc dùng email khác.',
 'Not authenticated':'Phiên đăng nhập đã hết hạn. Bạn vui lòng đăng nhập lại.',
 'Run not found':'Không tìm thấy phiên này hoặc tài khoản của bạn chưa được phép mở phiên.',
 'Cluster not found':'Không tìm thấy nhóm hồ sơ này. Bạn hãy tải lại danh sách.',
 STALE_REVISION:'Dữ liệu đã được cập nhật. Bạn hãy tải lại và kiểm tra trước khi thao tác tiếp.',
 STALE_ASSESSMENT:'Kết quả đối chiếu đã được cập nhật. Bạn hãy tải lại trước khi thao tác tiếp.',
 ACTIVE_OPERATION:'Hệ thống đang xử lý phiên này. Bạn chờ hoàn tất rồi thử lại nhé.',
 RUN_ARCHIVED:'Phiên đã được lưu trữ. Bạn cần khôi phục phiên trước khi tiếp tục.',
 CLUSTERS_NOT_READY:'Nhóm khách hàng chưa sẵn sàng. Bạn hãy kiểm tra lại bước tạo nhóm.',
 NO_GOLDEN_SELECTED:'Bạn hãy chọn ít nhất một hồ sơ đại diện để xuất dữ liệu.',
 GOLDEN_MANUAL_SELECTION_REQUIRED:'Bạn cần chọn lại một số thông tin đại diện trước khi xác nhận.',
 PERMISSION_DENIED:'Tài khoản của bạn chưa được cấp quyền thực hiện thao tác này.',
};
export function userFacingError(detail:unknown,status:number):string {
 const code=detail&&typeof detail==='object'&&!Array.isArray(detail)?(detail as {code?:unknown}).code:detail;
 if(typeof code==='string'&&messages[code])return messages[code];
 if(status===401)return 'Phiên đăng nhập đã hết hạn hoặc thông tin đăng nhập chưa đúng. Bạn vui lòng kiểm tra lại.';
 if(status===403)return 'Tài khoản hoặc phiên đăng nhập hiện tại chưa cho phép thao tác này. Bạn hãy đăng nhập lại hoặc liên hệ người quản lý.';
 if(typeof detail==='string'&&/[À-ỹĐđ]/u.test(detail)&&!/(?:Traceback|SQL|Exception|[A-Z_]{8,})/.test(detail))return detail;
 if(status===404)return 'Không tìm thấy dữ liệu bạn cần. Bạn hãy tải lại trang hoặc kiểm tra lại phiên.';
 if(status===409)return 'Dữ liệu đã thay đổi hoặc chưa đủ điều kiện xử lý. Bạn hãy tải lại và kiểm tra trước khi tiếp tục.';
 if(status===413)return 'Tệp quá lớn để tải theo cách này. Bạn hãy thử chia tệp thành các tệp nhỏ hơn.';
 if(status===422||status===400)return 'Thông tin chưa hợp lệ. Bạn kiểm tra lại các ô đã nhập và tệp dữ liệu nhé.';
 if(status===429)return 'Có nhiều yêu cầu cùng lúc. Bạn chờ một chút rồi thử lại nhé.';
 if(status===0)return 'Chưa kết nối được hệ thống. Bạn kiểm tra đường truyền rồi thử lại nhé.';
 return 'Hệ thống chưa hoàn tất yêu cầu. Bạn vui lòng thử lại sau.';
}
