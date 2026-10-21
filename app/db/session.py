from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy import create_engine
from app.config import settings

Base = declarative_base()

is_sqlite = "sqlite" in settings.DATABASE_URL.lower()

# Async engine for FastAPI
async_engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    future=True,
    pool_pre_ping=True,
    **({} if is_sqlite else {"pool_size": 10, "max_overflow": 10, "pool_recycle": 1800})
)

AsyncSessionLocal = sessionmaker(
    async_engine,
    class_=AsyncSession,
    expire_on_commit=False
)

is_sync_sqlite = "sqlite" in settings.SYNC_DATABASE_URL.lower()

# Sync engine for scripts and migrations
sync_engine = create_engine(
    settings.SYNC_DATABASE_URL,
    echo=False,
    pool_pre_ping=True,
    **({} if is_sync_sqlite else {"pool_size": 10, "max_overflow": 10, "pool_recycle": 1800})
)

async def get_db():
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()

