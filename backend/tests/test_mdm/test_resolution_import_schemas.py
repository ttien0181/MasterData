from io import BytesIO

import pytest
from fastapi import UploadFile

from masterone.mdm.resolution_import import parse_upload


@pytest.mark.asyncio
async def test_current_crm_erp_bill_schema_is_accepted():
    content = (
        "record_id,entity_id,source,name,phone,email,address,crm_customer_id\n"
        "CRM_1,E1,CRM,Nguyen Van An,0901,an@example.com,1 Nguyen Trai,C1\n"
    ).encode()
    parsed = await parse_upload(UploadFile(filename="crm.csv", file=BytesIO(content)))

    assert parsed.rows[0]["external_id"] == "CRM_1"
    assert parsed.rows[0]["source_system"] == "CRM"
    assert parsed.rows[0]["raw_fields"] == {
        "name": "Nguyen Van An",
        "phone": "0901",
        "email": "an@example.com",
        "address": "1 Nguyen Trai",
    }


@pytest.mark.asyncio
async def test_legacy_records_schema_remains_accepted():
    content = (
        "id,source_system,full_name,phone_number,email_address,raw_address\n"
        "r1,ERP,Nguyen Van An,0901,an@example.com,1 Nguyen Trai\n"
    ).encode()
    parsed = await parse_upload(UploadFile(filename="records.csv", file=BytesIO(content)))

    assert parsed.rows[0]["external_id"] == "r1"
    assert parsed.rows[0]["source_system"] == "ERP"
