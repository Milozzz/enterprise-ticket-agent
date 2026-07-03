from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase, Session
from app.core.config import get_settings
from app.db.tenant_context import apply_tenant_context, current_tenant_id

settings = get_settings()

# SQLite 不支持 pool_size 和 max_overflow
engine_kwargs = {
    "echo": settings.environment == "development",
}

if "sqlite" not in settings.database_url:
    engine_kwargs["pool_size"] = 10
    engine_kwargs["max_overflow"] = 20

engine = create_async_engine(
    settings.database_url,
    **engine_kwargs
)

AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    pass


@event.listens_for(Session, "after_begin")
def _set_postgres_tenant_context(session, transaction, connection) -> None:
    """Apply RLS context to every ORM transaction, including direct sessions."""

    del session, transaction
    if connection.dialect.name == "postgresql":
        connection.execute(
            text("SELECT set_config('app.current_tenant', :tenant_id, true)"),
            {"tenant_id": current_tenant_id()},
        )


async def init_db() -> None:
    """初始化数据库，创建所有表（开发环境使用，生产环境用 Alembic）"""
    async with engine.begin() as conn:
        # 启用 pgvector 扩展（仅 PostgreSQL）
        if "postgresql" in settings.database_url:
            await conn.execute(__import__("sqlalchemy").text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(Base.metadata.create_all)


async def get_db():
    """FastAPI 依赖注入：获取数据库会话"""
    try:
        async with AsyncSessionLocal() as session:
            try:
                await apply_tenant_context(session, settings.default_tenant_id)
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise
            finally:
                await session.close()
    except Exception:
        yield None
