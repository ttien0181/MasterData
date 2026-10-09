from pathlib import Path
from tempfile import TemporaryDirectory

from masterone.db.base import Base
from masterone.db.session import create_database


def test_database_creates_engine_and_session_factory():
    """create_database returns a working engine+factory pair for SQLite (used in tests)."""
    with TemporaryDirectory(dir=Path(__file__).resolve().parents[2]) as temporary_directory:
        database_path = Path(temporary_directory) / "mdm.db"
        engine, factory = create_database(f"sqlite:///{database_path.as_posix()}")
        Base.metadata.create_all(engine)
        assert database_path.exists()
        assert factory is not None
        engine.dispose()

