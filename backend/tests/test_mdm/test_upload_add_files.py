from masterone.config import get_settings


def test_concurrent_append_and_replay_are_serialized(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from masterone.auth.models import User
    from masterone.db.base import Base
    from masterone.mdm.models import ResolutionUploadFile, ResolutionUploadSession
    from masterone.mdm.upload_sessions import add_upload_files
    from sqlalchemy import create_engine, event
    from sqlalchemy.orm import sessionmaker
    engine = create_engine('sqlite:///' + (tmp_path / 'uploads.db').as_posix(), connect_args={'timeout': 20})
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory.begin() as db:
        db.add(User(id='u', email='u@example.com', email_normalized='u@example.com', password_hash='unused'))
        db.flush()
        draft = ResolutionUploadSession(owner_user_id='u', storage_backend='local', total_bytes=0)
        db.add(draft)
        db.flush()
        sid = draft.id
    reserved = []
    def statement(_conn, _cursor, sql, *_):
        if sql == 'BEGIN IMMEDIATE':
            reserved.append(sql)
    event.listen(engine, 'before_cursor_execute', statement)
    def batch(ids):
        barrier = Barrier(2)
        def append(request_id):
            with factory() as db:
                barrier.wait(timeout=10)
                return add_upload_files(db, sid, 'u', [{'filename': request_id + '.csv', 'size': 1}], request_id).id
        with ThreadPoolExecutor(max_workers=2) as pool:
            assert list(pool.map(append, ids)) == [sid, sid]
    batch(['a', 'b'])
    batch(['replay', 'replay'])
    with factory() as db:
        rows = db.query(ResolutionUploadFile).filter_by(session_id=sid).all()
        assert len(rows) == 3 and len({f.position for f in rows}) == 3
    assert len(reserved) == 4
    engine.dispose()


def test_add_files_preserves_parts_and_ready_file_validation(client, monkeypatch, tmp_path):
    monkeypatch.setattr(get_settings(), 'upload_staging_dir', str(tmp_path))
    csv = b'record_id,source,name,phone,email,address\n1,CRM,Ada,0901234567,a@example.test,Hanoi\n'
    draft = client.post('/api/v1/mdm/upload-sessions',json={'files':[{'filename':'first.csv','size':len(csv)}]}).json()
    url = '/api/v1/mdm/upload-sessions/' + draft['session_id']
    first_id = draft['files'][0]['file_id']
    assert client.put(url + f'/files/{first_id}/parts/0',content=csv).status_code == 200
    assert client.post(url + '/validate').json()['status'] == 'READY'
    body = {'request_id':'add-two','files':[{'filename':'second.csv','size':len(csv)}]}
    result = client.post(url + '/files',json=body)
    assert result.status_code == 200,result.text
    added = result.json()
    assert added['status'] == 'UPLOADING' and len(added['files']) == 2
    assert added['files'][0]['file_id'] == first_id
    assert added['files'][0]['status'] == 'READY' and added['files'][0]['parts']
    assert client.post(url + '/files',json=body).json()['files'] == added['files']
    changed = {**body,'files':[{'filename':'changed.csv','size':len(csv)}]}
    assert client.post(url + '/files',json=changed).status_code == 409
    too_many = {'request_id':'too-many','files':[{'filename':f'{i}.csv','size':1} for i in range(9)]}
    assert client.post(url + '/files',json=too_many).status_code == 413
    assert len(client.get(url).json()['files']) == 2
