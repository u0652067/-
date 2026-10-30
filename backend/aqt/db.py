from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from aqt.config import get_settings

_engine: Engine | None = None
_SessionLocal: sessionmaker | None = None


def get_engine(url: str | None = None) -> Engine:
    global _engine, _SessionLocal
    if url is not None:
        return create_engine(url, pool_pre_ping=True, future=True)
    if _engine is None:
        _engine = create_engine(get_settings().database_url, pool_pre_ping=True, future=True)
        _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False)
    return _engine


@contextmanager
def session_scope(engine: Engine | None = None) -> Iterator[Session]:
    if engine is not None:
        factory = sessionmaker(bind=engine, expire_on_commit=False)
    else:
        get_engine()
        factory = _SessionLocal
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
