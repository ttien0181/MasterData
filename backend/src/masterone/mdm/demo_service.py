"""Demo lifecycle uses the real workflow and AI configuration, with isolated ownership."""
import io
import time
from uuid import uuid4

from fastapi import HTTPException
from masterone.auth.models import AuthSession, DemoGrant, User
from masterone.config import get_settings
from masterone.mdm.models import ResolutionRun, ResolutionTask


def end_demo(db, grant, *, expired=False):
    if grant is None or grant.state == 'PURGED':
        return
    grant.state = 'EXPIRED' if expired else 'ENDED'
    grant.expires_at_epoch = min(grant.expires_at_epoch, int(time.time()))
    user = db.get(User, grant.user_id)
    if user: user.is_active = False
    from masterone.db.base import now_utc
    session = db.get(AuthSession, grant.session_id)
    if session: session.revoked_at = now_utc()
    if grant.run_id:
        task = db.query(ResolutionTask).filter_by(run_id=grant.run_id).first()
        if task and task.status in ('QUEUED', 'RUNNING'): task.cancel_requested = True


def expire_demos(factory):
    """Called periodically, including when the browser was simply closed."""
    from sqlalchemy import or_
    now = int(time.time())
    with factory.begin() as db:
        grants = db.query(DemoGrant).filter(DemoGrant.state.in_(('NEW', 'STARTING', 'READY')),
            or_(DemoGrant.expires_at_epoch <= now,
                DemoGrant.last_seen_epoch <= now - get_settings().demo_idle_seconds)).all()
        for grant in grants: end_demo(db, grant, expired=True)


def start_demo(db, user_id, *, use_embedding=False):
    from masterone.mdm.demo_dataset import SOURCES, csv_bytes
    from masterone.mdm.resolution_import import create_resolution_run_from_streams
    from masterone.mdm.matching_foundation_service import create_profile, transition_profile, apply_default
    from masterone.mdm.matching_foundation_contracts import ProfileCreate, DefaultApply
    from masterone.mdm.matching_foundation_models import MatchingProfileDefault
    from masterone.storage.demo_scope import local_storage
    grant = db.query(DemoGrant).filter_by(user_id=user_id).with_for_update().one_or_none()
    if grant is None or grant.state not in ('NEW', 'STARTING', 'READY'):
        raise HTTPException(403, 'Bạn cần bắt đầu một phiên demo mới.')
    if grant.run_id and db.get(ResolutionRun, grant.run_id):
        grant.state = 'READY'
        db.commit()
        return {'run_id': grant.run_id, 'status': 'READY'}
    if grant.state == 'STARTING':
        raise HTTPException(409, 'Dữ liệu mẫu đang được chuẩn bị. Bạn chờ một chút rồi thử lại nhé.')
    settings = get_settings()
    if not (settings.session_workflow_enabled and settings.matching_profile_registry_enabled
            and (not use_embedding or settings.advanced_matching_agents_enabled)):
        raise HTTPException(503, 'Hệ thống demo chưa sẵn sàng đối chiếu. Vui lòng thử lại sau.')
    # A normal owned, verified profile; the client cannot supply configuration or arbitrary files.
    if db.get(MatchingProfileDefault, (user_id, 'owner-private')) is None:
        profile = create_profile(db, user_id, ProfileCreate(version=1, advanced_agents=use_embedding, fallback_mode='fail_operation'))
        transition_profile(db, user_id, profile.id, 1, 'VALIDATED')
        apply_default(db, user_id, 'owner-private', DefaultApply(profile_id=profile.id, expected_revision=0))
    grant.run_id, grant.state = str(uuid4()), 'STARTING'
    run_id = grant.run_id
    db.commit()
    try:
        with local_storage():
            run, _ = create_resolution_run_from_streams(db,
                [(name, io.BytesIO(csv_bytes(i))) for i, (name, _, _) in enumerate(SOURCES)],
                user_id, name='Demo — Một khách hàng, nhiều nguồn thông tin',
                workflow_version=2, reserved_run_id=run_id, use_embedding=use_embedding)
        grant = db.get(DemoGrant, user_id)
        grant.state = 'READY'
        db.commit()
        return {'run_id': run.id, 'status': 'READY'}
    except Exception:
        db.rollback()
        grant = db.get(DemoGrant, user_id)
        # An ambiguous response after commit is recovered by the fixed run ID on retry.
        grant.state = 'READY' if db.get(ResolutionRun, run_id) else 'NEW'
        db.commit()
        raise
