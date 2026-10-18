export const mvpGroups = {
  A_STRONG: { title: 'Trùng giấy tờ hoặc nhiều thông tin', description: 'Tên phù hợp cùng giấy tờ, hoặc điện thoại và ngày sinh.', tone: 'green' },
  A_CONTACT: { title: 'Trùng thông tin liên hệ', description: 'Tên phù hợp cùng số điện thoại hoặc email.', tone: 'green' },
  B_SUPPORT: { title: 'Có căn cứ cần xác minh thêm', description: 'Có thông tin bổ sung để người dùng đối chiếu.', tone: 'amber' },
  B_CONFLICT: { title: 'Có thông tin mâu thuẫn', description: 'Thông tin khác nhau hoặc kết quả các agent chưa thống nhất.', tone: 'red' },
} as const;
export type MvpGroup = keyof typeof mvpGroups;
export const isMvpGroup = (key?: string): key is MvpGroup => !!key && Object.hasOwn(mvpGroups, key);
