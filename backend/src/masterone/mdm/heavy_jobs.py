"""One host-wide slot shared by import supervisors and the resolution worker.

The lock file must live on the same mounted local filesystem in API and worker.
The OS releases the lock on supervisor exit; no stale sentinel needs deleting.
"""
import errno
import os
import time
from contextlib import contextmanager
from pathlib import Path

from masterone.config import get_settings


@contextmanager
def heavy_job_slot(*, blocking=True):
    settings = get_settings()
    if not settings.heavy_jobs_serialized:
        yield True
        return
    path = Path(settings.heavy_job_lock_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b'0')
            handle.flush()
        acquired = False
        while not acquired:
            try:
                handle.seek(0)
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except OSError as error:
                if error.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                    raise
                if not blocking:
                    break
                time.sleep(.2)
        try:
            yield acquired
        finally:
            if acquired:
                handle.seek(0)
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle, fcntl.LOCK_UN)
