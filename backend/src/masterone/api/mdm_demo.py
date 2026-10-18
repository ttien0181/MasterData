from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session
from pydantic import BaseModel, ConfigDict, StrictBool
from masterone.auth.security import CurrentSession, require_capability, require_mutation
from masterone.db.session import get_session
from masterone.mdm.demo_dataset import catalog, csv_bytes, zip_bytes, SOURCES

router = APIRouter(prefix='/mdm/demo', tags=['Product demo'])


@router.get('/catalog')
def sample_catalog(auth: CurrentSession = Depends(require_capability('read'))):
    return catalog()


@router.get('/files/{filename}')
def sample_file(filename: str, auth: CurrentSession = Depends(require_capability('read'))):
    names = [source[0] for source in SOURCES]
    if filename == 'masterone-du-lieu-mau.zip':
        content, media = zip_bytes(), 'application/zip'
    elif filename in names:
        content, media = csv_bytes(names.index(filename)), 'text/csv; charset=utf-8'
    else:
        raise HTTPException(404, 'Không tìm thấy tệp dữ liệu mẫu.')
    return Response(content, media_type=media, headers={
        'Content-Disposition': f'attachment; filename="{filename}"', 'Cache-Control': 'private, max-age=3600'})


class DemoStartRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    use_embedding: StrictBool = False


@router.post('/start')
def begin_demo(body: DemoStartRequest | None = None,
               auth: CurrentSession = Depends(require_mutation), db: Session = Depends(get_session)):
    if not auth.demo:
        raise HTTPException(403, 'Chức năng này dành cho phiên demo.')
    from masterone.mdm.demo_service import start_demo
    return start_demo(db, auth.user.id, use_embedding=body.use_embedding if body else False)
