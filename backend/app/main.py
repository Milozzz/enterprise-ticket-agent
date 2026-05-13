from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import chat, dashboard, tickets
from app.core.config import effective_simulate_database_down, get_settings
from app.core.logging import setup_logging
from app.core.observability import flush_langfuse, get_langfuse_client
from app.core.rate_limit import RateLimitMiddleware
from app.db.database import init_db

logger = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用启动/关闭时的生命周期管理"""
    setup_logging()
    logger.info("Starting enterprise ticket agent service")

    try:
        await init_db()
        logger.info("Database initialized")
    except Exception as e:
        logger.warning("Database unavailable, running in memory-only mode", error=str(e))

    # 初始化 Langfuse 客户端（key 未配置时静默跳过）
    lf = get_langfuse_client()
    if lf:
        logger.info("Langfuse observability enabled")

    yield

    # 关闭前刷新 Langfuse 缓冲区，确保所有 trace 都发送出去
    flush_langfuse()
    logger.info("Shutting down")


settings = get_settings()

app = FastAPI(
    title="企业级自动化工单 Agent API",
    description="AI-powered ticket automation with LangGraph + Generative UI",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs" if settings.environment == "development" else None,
)

# CORS：开发环境 Next 可能在 3001…（3000 被占用）
_cors_kw: dict = {
    "allow_credentials": True,
    "allow_methods": ["*"],
    "allow_headers": ["*"],
}
if settings.environment == "development":
    _cors_kw["allow_origin_regex"] = r"http://(localhost|127\.0\.0\.1)(:\d+)?"
else:
    frontend_origin = settings.frontend_origin.rstrip("/")
    _cors_kw["allow_origins"] = [
        origin for origin in [
            frontend_origin,
            "http://localhost:3000",
            "http://127.0.0.1:3000",
        ] if origin
    ]
app.add_middleware(CORSMiddleware, **_cors_kw)
app.add_middleware(RateLimitMiddleware)

app.include_router(chat.router, prefix="/api/agent", tags=["Agent"])
app.include_router(tickets.router, prefix="/api/tickets", tags=["Tickets"])
app.include_router(dashboard.router, prefix="/api/dashboard", tags=["Dashboard"])


# ── 认证路由（签发 JWT，供开发/测试使用）────────────────────────────────────────
from fastapi import Body
from app.core.auth import create_access_token


@app.post("/auth/token", tags=["Auth"])
async def issue_token(
    user_id: str = Body(...),
    role: str = Body(default="USER"),
):
    """
    开发用：凭 user_id + role 签发 JWT。
    生产环境应替换为真实 SSO / OAuth2 流程。
    """
    token = create_access_token(user_id=user_id, role=role)
    return {"access_token": token, "token_type": "bearer"}


@app.get("/health")
async def health_check():
    return {
        "status": "ok",
        "service": "enterprise-ticket-agent",
        "health_schema": 2,
        "simulate_database_down": effective_simulate_database_down(),
        "environment": get_settings().environment,
    }
