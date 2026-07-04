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
    """初始化数据库，创建所有表（开发环境使用，生产环境用 Alembic）。

    注意：create_all 只建表，不创建 RLS 策略（那写在 alembic 迁移里）。因此用
    create_all 起的库租户隔离是关闭的——不要用它跑多租户隔离测试，否则会得到
    “看起来隔离了”的假信心。需要验证隔离时请改用 `alembic upgrade head`。"""
    async with engine.begin() as conn:
        # 启用 pgvector 扩展（仅 PostgreSQL）
        if "postgresql" in settings.database_url:
            await conn.execute(__import__("sqlalchemy").text("CREATE EXTENSION IF NOT EXISTS vector"))
            import warnings
            warnings.warn(
                "init_db(create_all) does not create RLS policies; run alembic migrations "
                "for tenant isolation before relying on it.",
                RuntimeWarning,
                stacklevel=2,
            )
        await conn.run_sync(Base.metadata.create_all)


async def get_db():
    """FastAPI 依赖注入：获取数据库会话。

    绑定当前请求租户（而非写死默认租户），保证 RLS 生效；连接失败直接抛出，
    由 FastAPI 返回 5xx，而不是 yield None 让下游对 None 调用 .execute 崩在更深处。"""
    async with AsyncSessionLocal() as session:
        try:
            await apply_tenant_context(session, current_tenant_id())
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
