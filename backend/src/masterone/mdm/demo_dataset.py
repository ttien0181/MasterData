"""Deterministic fictitious Vietnamese customer data; never customer production data."""
import csv
import io
import unicodedata
import zipfile
from functools import lru_cache

VERSION = 'customer-demo-v1'
SOURCES = [('crm_khach_hang.csv', 'CRM', 'Quản lý khách hàng'),
           ('erp_ban_hang.csv', 'ERP', 'Bán hàng'),
           ('bill_thanh_toan.csv', 'BILL', 'Thanh toán'),
           ('csv_cham_soc.csv', 'CSV', 'Chăm sóc khách hàng')]
FIELDS = ['source_record_id', 'source_system', 'name', 'phone', 'email', 'address', 'dob', 'government_id']
SURNAMES = ['Nguyễn', 'Trần', 'Lê', 'Phạm', 'Hoàng', 'Huỳnh', 'Phan', 'Vũ', 'Võ', 'Đặng', 'Bùi', 'Đỗ', 'Hồ', 'Ngô', 'Dương', 'Lý', 'Đinh', 'Trịnh', 'Đoàn', 'Mai']
NAMES = ['Minh An', 'Thu Hà', 'Quang Huy', 'Ngọc Linh', 'Thanh Tâm', 'Minh Khoa', 'Phương Anh', 'Tuấn Kiệt', 'Khánh Vy', 'Gia Bảo', 'Hải Yến', 'Đức Anh', 'Thảo Nguyên', 'Hoài Nam', 'Bảo Ngọc', 'Anh Thư', 'Trung Hiếu', 'Hồng Nhung', 'Nhật Minh', 'Quỳnh Chi', 'Mỹ Duyên', 'Hữu Phúc', 'Kim Anh', 'Đăng Khoa', 'Thanh Bình']


def unaccent(value):
    return ''.join(c for c in unicodedata.normalize('NFD', value.replace('Đ','D').replace('đ','d')) if unicodedata.category(c) != 'Mn')


@lru_cache(maxsize=4)
def csv_bytes(source_index):
    _, source, _ = SOURCES[source_index]
    text = io.StringIO(newline='')
    writer = csv.DictWriter(text, fieldnames=FIELDS)
    writer.writeheader()
    for i in range(500):
        name = f'{SURNAMES[i // 25]} {NAMES[i % 25]}'
        phone = f'090{i + 1000000:07d}'
        address = f'{i + 10} đường Nguyễn Trãi, phường Thanh Xuân, Hà Nội'
        row = dict(source_record_id=f'{source}-{i+1:04d}', source_system=source, name=name,
                   phone=phone, email=f'khachhang{i+1:04d}@example.test', address=address,
                   dob=f'{1975+i%25}-{1+i%12:02d}-{1+i%27:02d}', government_id=f'001{i%2}{75+i%25:02d}{i+100000:06d}')
        if source_index == 1:
            row['name'] = name.upper()
            row['phone'] = '+84' + phone[1:]
            if i % 4 == 0: row['email'] = ''
        if source_index == 2:
            row['name'] = unaccent(name)
            row['address'] = f'{i+10} Nguyen Trai, Thanh Xuan, Ha Noi'
            if i % 5 == 0: row['dob'] = ''
        if source_index == 3:
            row['address'] = f'{i+10} Đ. Nguyễn Trãi, P. Thanh Xuân, Hà Nội'
            if i % 3 == 0: row['government_id'] = ''
            if i % 7 == 0: row['phone'] = ''
            # Deliberately ambiguous and different-person examples.
            if i % 40 == 9:
                row['government_id'] = f'0791{90+i%10:02d}{i+800000:06d}'
                row['dob'] = '2000-01-01'
                row['phone'] = f'091{i+2000000:07d}'
        if i == 1 and source_index == 3:
            row['name'] = f'{SURNAMES[0]} {NAMES[0]}'
            row['phone'] = '0901000000'  # Shared household contact, different identity.
        writer.writerow(row)
    return text.getvalue().encode('utf-8-sig')


def catalog():
    return {'version': VERSION, 'title': 'Một khách hàng, nhiều nguồn thông tin',
            'description': 'Dữ liệu giả lập để thực hành đối chiếu khách hàng, chọn thông tin đại diện và xuất CSV.',
            'record_count': 2000, 'file_count': 4, 'synthetic': True,
            'files': [{'name': name, 'source': source, 'description': description,
                       'record_count': 500, 'byte_size': len(csv_bytes(i))}
                      for i, (name, source, description) in enumerate(SOURCES)]}


@lru_cache(maxsize=1)
def zip_bytes():
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        for i, (name, _, _) in enumerate(SOURCES): archive.writestr(name, csv_bytes(i))
        archive.writestr('HUONG_DAN.txt', ('MASTERONE — DỮ LIỆU GIẢ LẬP\nPhiên bản: '+VERSION+'\n'
            '4 tệp CSV, mỗi tệp 500 hồ sơ. Dữ liệu không mô tả khách hàng thực.\n'
            'Tải cả bốn tệp vào cùng một phiên để trải nghiệm đầy đủ.\n'
            'Tình huống: tên có/không dấu, số điện thoại +84, địa chỉ viết tắt, thiếu thông tin, '
            'trùng tên và thông tin định danh khác nhau.\n').encode('utf-8-sig'))
    return output.getvalue()
