from collections.abc import Generator

from fastapi import Request
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker


def create_database(database_url: str) -> tuple[Engine, sessionmaker[Session]]:
    engine = create_engine(database_url)
    return engine, sessionmaker(bind=engine, expire_on_commit=False)


def get_session(request: Request) -> Generator[Session, None, None]:
    factory: sessionmaker[Session] = request.app.state.session_factory

    with factory() as session:
        yield session
