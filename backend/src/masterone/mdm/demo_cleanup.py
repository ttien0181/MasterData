"""Delete only expired demo run descendants; preserve the revoked identity tombstone."""
import logging
import shutil
import time
from hashlib import sha256
from pathlib import Path
from uuid import UUID

from sqlalchemy import delete, or_, select, tuple_, text
from sqlalchemy.exc import IntegrityError
from masterone.auth.models import DemoGrant
from masterone.config import get_settings
from masterone.db.base import Base
from masterone.mdm.models import ResolutionRun, ResolutionTask
from masterone.mdm.workflow_models import WorkflowArtifact

log = logging.getLogger(__name__)


def purge(factory):
    with factory() as db:
        candidates = [(g.user_id,g.run_id) for g in db.query(DemoGrant).filter(
            DemoGrant.state.in_(('ENDED','EXPIRED')), DemoGrant.expires_at_epoch < int(time.time())-300).limit(3)]
    for user_id, run_id in candidates:
        try:
            artifacts = []
            with factory.begin() as db:
                if db.get_bind().dialect.name == 'postgresql':
                    db.execute(text("SET LOCAL lock_timeout = '1s'"))
                    db.execute(text("SET LOCAL statement_timeout = '8s'"))
                grant = db.query(DemoGrant).filter_by(user_id=user_id).with_for_update().one()
                if grant.state not in ('ENDED','EXPIRED'): continue
                task = db.query(ResolutionTask).filter_by(run_id=run_id).first() if run_id else None
                if task and task.status == 'RUNNING': continue
                run = db.get(ResolutionRun, run_id) if run_id else None
                if run:
                    if run.owner_user_id != user_id: raise ValueError('DEMO_CLEANUP_OWNER_MISMATCH')
                    artifacts = [row[0] for row in db.query(WorkflowArtifact.id).filter_by(run_id=run_id)]
                    remove_run_rows(db, run_id)
                    remove_local_files(run_id, artifacts)
                grant.state = 'PURGED'
        except Exception:
            # A new FK or a live worker must retain the data for a later cleanup, never broaden deletion.
            log.warning('DEMO_CLEANUP_RETRY')


def remove_run_rows(db, run_id):
    tables = list(Base.metadata.tables.values())
    root = ResolutionRun.__table__
    selected = {root.name: {(run_id,)}}
    def where(table):
        keys = list(table.primary_key.columns)
        values = selected.get(table.name,set())
        return keys[0].in_([key[0] for key in values]) if len(keys)==1 else tuple_(*keys).in_(list(values))
    # Freeze the exact descendant PK set before deleting anything; composite FKs stay composite.
    for _ in range(len(tables)):
        changed = False
        for table in tables:
            if table is root or table.name == 'demo_grants' or not table.primary_key.columns: continue
            predicates = [table.c.run_id == run_id] if 'run_id' in table.c else []
            for fk in table.foreign_key_constraints:
                parent = fk.referred_table
                if parent.name not in selected: continue
                local = [element.parent for element in fk.elements]
                remote = [element.column for element in fk.elements]
                predicates.append((local[0] if len(local)==1 else tuple_(*local)).in_(select(*remote).where(where(parent))))
            if not predicates: continue
            found = set(db.execute(select(*table.primary_key.columns).where(or_(*predicates))))
            previous = selected.setdefault(table.name,set())
            if not found <= previous: previous.update(found);changed=True
        if not changed: break
    pending = [table for table in reversed(tables) if selected.get(table.name)]
    for _ in range(len(pending)+1):
        progress = False
        for table in list(pending):
            try:
                with db.begin_nested(): db.execute(delete(table).where(where(table)))
                pending.remove(table);progress=True
            except IntegrityError:
                continue
        if not pending: return
        if not progress: break
    raise ValueError('DEMO_CLEANUP_DEPENDENCY_RETRY:' + ','.join(t.name for t in pending))


def remove_local_files(run_id, artifacts):
    UUID(run_id)
    root = Path(get_settings().workflow_artifact_dir).resolve()
    targets = [root/'objects'/'workflow'/run_id]
    targets.extend(root/'cache'/sha256(artifact.encode()).hexdigest()[:24] for artifact in artifacts)
    for target in targets:
        # Do not follow links or delete outside the explicitly configured artifact root.
        resolved = target.resolve()
        if target.is_symlink() or resolved == root or not resolved.is_relative_to(root):
            raise ValueError('DEMO_CLEANUP_UNSAFE_PATH')
        if target.exists(): shutil.rmtree(target)
