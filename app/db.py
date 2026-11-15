from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import config

_kw = {}
if config.DATABASE_URL.startswith("sqlite"):
    _kw["connect_args"] = {"check_same_thread": False, "timeout": 30}
else:
    _kw["pool_pre_ping"] = True

engine = create_engine(config.DATABASE_URL, **_kw)
SessionLocal = sessionmaker(bind=engine, autoflush=True)


def get_db():
    db = SessionLocal()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
