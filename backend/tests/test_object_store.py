from io import BytesIO

import pytest
from botocore.exceptions import ClientError
from botocore.stub import Stubber
from masterone.storage.object_store import R2ObjectStore


@pytest.mark.parametrize("upload_fails", [False, True])
def test_upload_stream_preserves_caller_file_lifetime(upload_fails):
    store = R2ObjectStore("https://r2.example.test", "test-access", "test-secret", "test-bucket")
    payload = b"record_id,name\n1,Ada\n"
    with BytesIO(payload) as stream, Stubber(store.client) as stubber:
        if upload_fails:
            stubber.add_client_error("put_object", service_error_code="AccessDenied", http_status_code=403)
            with pytest.raises(ClientError):
                store.upload_stream(stream, "records.csv", "text/csv")
        else:
            stubber.add_response("put_object", {"ETag": '"test-etag"'})
            store.upload_stream(stream, "records.csv", "text/csv")

        assert not stream.closed
        stream.seek(0)
        assert stream.read() == payload
        stubber.assert_no_pending_responses()
    assert stream.closed
