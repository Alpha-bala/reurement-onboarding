from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from urllib.parse import quote_plus
from dotenv import load_dotenv
from contextlib import contextmanager
import os
load_dotenv()


DB_USER = os.getenv("DB_USER", "root")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "3306")
DB_NAME = os.getenv("DB_NAME", "spherehire")


ENCODED_PASSWORD = quote_plus(DB_PASSWORD)

DATABASE_URL = (
    f"mysql+pymysql://{DB_USER}:{ENCODED_PASSWORD}"
    f"@{DB_HOST}:{DB_PORT}/{DB_NAME}"
    "?charset=utf8mb4"
)


engine = create_engine(
    DATABASE_URL,
    pool_size=20,          # Base number of persistent connections
    max_overflow=40,       # Extra connections during traffic spikes
    pool_timeout=30,       # Seconds to wait before failing
    pool_recycle=1800,     # Recycle connections every 30 mins
    pool_pre_ping=True,    # Detects stale connections
    future=True,           # SQLAlchemy 2.x behavior
)


SessionLocal = sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,  # Frontend-safe: avoids detached objects
)


Base = declarative_base()

@contextmanager
def db_session():
    """Create a SQLAlchemy ORM session for database operations."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
 

def get_db():
    """
    Provides a SQLAlchemy session per request.
    Ensures connection is always returned to the pool.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

