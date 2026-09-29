from datetime import UTC, datetime

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


def now_utc() -> datetime:
    return datetime.now(UTC)
