from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

import os

# DATABASE_URL lets deployment move off the default SQLite file (Docker volume,
# managed Postgres) without code changes. Relative SQLite paths keep the
# historical backend_app.db so existing installs keep their data.
SQLALCHEMY_DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./backend_app.db")


def _ensure_sqlite_parent_dir(url: str) -> None:
    """Create the folder that holds a file-backed SQLite database.

    SQLite creates the *file* but never its directory, so a deployment that points
    DATABASE_URL at a mounted volume (``sqlite:////data/app.db``) would fail at
    startup with "unable to open database file". In-memory URLs are left alone.
    """
    if not url.startswith("sqlite") or ":memory:" in url:
        return
    _, _, path = url.partition(":///")
    if not path:
        return
    parent = os.path.dirname(os.path.abspath(path))
    if parent and not os.path.isdir(parent):
        os.makedirs(parent, exist_ok=True)


_engine_kwargs: dict = {}
if SQLALCHEMY_DATABASE_URL.startswith("sqlite"):
    _ensure_sqlite_parent_dir(SQLALCHEMY_DATABASE_URL)
    _engine_kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
else:
    _engine_kwargs.update(pool_pre_ping=True, pool_size=int(os.getenv("DB_POOL_SIZE", "5")), max_overflow=10)

engine = create_engine(SQLALCHEMY_DATABASE_URL, **_engine_kwargs)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()