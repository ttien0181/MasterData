from masterone.mdm.pipeline.standardization import tokenize_vietnamese_name, normalize_org_name

def test_tokenize_vietnamese_name():
    # 1. Tên phổ biến VN
    parts = tokenize_vietnamese_name("nguyễn văn an")
    assert parts == {"surname": "nguyễn", "middle_name": "văn", "given_name": "an"}

    parts = tokenize_vietnamese_name("trần thị bích ngọc")
    assert parts == {"surname": "trần", "middle_name": "thị bích", "given_name": "ngọc"}

    # 2. Tên 2 token (surname + given_name, middle_name rỗng)
    parts = tokenize_vietnamese_name("lê a")
    assert parts == {"surname": "lê", "middle_name": "", "given_name": "a"}

    # 3. Tên 1 token (surname = given = token đó)
    parts = tokenize_vietnamese_name("hùng")
    assert parts == {"surname": "hùng", "middle_name": "", "given_name": "hùng"}

    # 4. Tên rỗng hoặc toàn space
    parts = tokenize_vietnamese_name("   ")
    assert parts == {"surname": "", "middle_name": "", "given_name": ""}

def test_normalize_org_name():
    assert normalize_org_name("Cong ty TNHH Thuong mai va Dich vu ABC") == {"org_name_core": "thuong mai va dich vu abc", "legal_form": "TNHH"}
    assert normalize_org_name("Cong ty Co phan Tap doan XYZ") == {"org_name_core": "tap doan xyz", "legal_form": "CO PHAN"}
    assert normalize_org_name("CTY CP Dau tu Phat trien") == {"org_name_core": "dau tu phat trien", "legal_form": "CO PHAN"}
    assert normalize_org_name("ABC JSC") == {"org_name_core": "abc", "legal_form": "JSC"}
    assert normalize_org_name("Tap doan Hoa Phat") == {"org_name_core": "hoa phat", "legal_form": "TAP DOAN"}
