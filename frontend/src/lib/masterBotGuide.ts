export function masterBotGuide(pathname:string,dialog=''):string {
 if(dialog){
  if(dialog.startsWith('Hồ sơ đại diện')) return 'Đây là thông tin đã được giữ lại cho hồ sơ đại diện của khách hàng. Bạn có thể xem lại tên, thông tin liên hệ, giấy tờ và địa chỉ trước khi chọn hồ sơ để xuất CSV nhé.';
  if(dialog.includes('Đối chiếu cặp')) return 'Ở đây, bạn có thể đặt hai hồ sơ cạnh nhau để kiểm tra tên, thông tin liên hệ và giấy tờ. Hãy xem kỹ các điểm khác biệt trước khi quyết định gộp nhé.';
  if(dialog.includes('Chi tiết nhóm')) return 'Đây là các hồ sơ đang thuộc cùng một khách hàng. Bạn chỉ cần bấm vào ô thông tin muốn giữ lại; ô được chọn sẽ nổi bật và trở thành thông tin đại diện.';
  if(dialog.includes('gộp')||dialog.includes('quyết định')) return 'Mình cùng kiểm tra lại lựa chọn trước khi lưu nhé. Hãy chắc rằng các hồ sơ thuộc cùng một khách hàng và chọn lý do phù hợp.';
  return `Bạn đang xem ${dialog.toLocaleLowerCase('vi')}. Hãy kiểm tra thông tin tại đây trước khi xác nhận nhé.`;
 }
 if(/\/sessions\/[^/]+\/data(?:\/|$)/.test(pathname)) return 'Chào bạn! Bước 1 giúp bạn kiểm tra dữ liệu vừa tải lên: có bao nhiêu hồ sơ, thông tin nào còn thiếu và dữ liệu đã sẵn sàng chưa. Khi đã kiểm tra xong, mình cùng chuyển sang đối chiếu khách hàng nhé.';
 if(/\/sessions\/[^/]+\/decisions(?:\/|$)/.test(pathname)) return 'Ở Bước 2, bạn xem các cặp hồ sơ có khả năng thuộc cùng một khách hàng. Chọn Đối chiếu để kiểm tra, rồi Đồng ý gộp hoặc Không gộp. Nếu cần gợi ý, hãy nhờ mình giải thích nhé.';
 if(/\/sessions\/[^/]+\/clusters(?:\/|$)/.test(pathname)) return 'Bước 3 tập hợp các hồ sơ đã đồng ý gộp thành từng nhóm khách hàng. Mở một nhóm để so sánh và bấm vào từng ô thông tin muốn giữ lại cho hồ sơ đại diện nhé.';
 if(/\/sessions\/[^/]+\/golden(?:\/|$)/.test(pathname)) return 'Ở Bước 4, bạn xem lại các hồ sơ đại diện của khách hàng. Chọn từng hồ sơ hoặc Chọn tất cả để xuất CSV; lựa chọn của bạn được giữ khi chuyển trang. Bấm Xem chi tiết để kiểm tra thông tin trước khi xuất nhé.';
 if(pathname.endsWith('/utilities')) return 'Đây là nơi xem lại lịch sử xử lý, các lần xuất dữ liệu và quy định đang áp dụng cho phiên. Bạn có thể dùng thông tin này để tra cứu khi cần nhé.';
 if(pathname==='/app') return 'Đây là danh sách các phiên làm việc của bạn. Bạn có thể mở lại một phiên đang xử lý hoặc tải tệp khách hàng lên để bắt đầu phiên mới nhé.';
 if(pathname.includes('/uploads/')) return 'Mình đang chuẩn bị dữ liệu bạn vừa tải lên. Bạn có thể theo dõi tiến độ tại đây; khi hoàn tất, hãy vào Bước 1 để kiểm tra hồ sơ nhé.';
 return '';
}
