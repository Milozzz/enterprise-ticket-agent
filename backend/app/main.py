import asyncio
from contextlib import asynccontextmanager

import structlog
from fastapi import Body, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import (
    a2a_server,
    agent_jobs,
    admin_config,
    approvals,
    chat,
    commercial,
    dashboard,
    erp_mock,
    erp_governance,
    erp_runtime,
    mcp_server,
    prompt_governance,
    tickets,
)
from app.agent.a2a_tasks import run_a2a_worker
from app.agent.agent_jobs import run_agent_job_worker
from app.agent.approval_tasks import run_approval_escalation_worker
from app.agent import graph as graph_runtime
from app.core.config import effective_simulate_database_down, get_settings
from app.core.auth import create_access_token
from app.core.logging import setup_logging
from app.core.observability import flush_langfuse, get_langfuse_client
from app.core.rate_limit import RateLimitMiddleware
from app.core.tenant_middleware import TenantContextMiddleware
from app.db.database import AsyncSessionLocal, init_db
from app.erp.outbox_worker import run_outbox_worker
from app.erp.reconciliation_worker import run_reconciliation_worker

logger = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用启动/关闭时的生命周期管理"""
    setup_logging()
    logger.info("Starting enterprise ticket agent service")

    graph_resource = None
    try:
        await init_db()
        logger.info("Database initialized")
    except Exception as e:
        if get_settings().environment != "development":
            raise
        logger.warning("Database unavailable, running in memory-only mode", error=str(e))

    if get_settings().environment != "development":
        runtime_graph, graph_resource = await graph_runtime.open_postgres_graph(
            get_settings().database_url
        )
        graph_runtime.set_ticket_graph(runtime_graph)
        chat.ticket_graph = runtime_graph
        logger.info("PostgreSQL LangGraph checkpointer enabled")

    # 初始化 Langfuse 客户端（key 未配置时静默跳过）
    lf = get_langfuse_client()
    if lf:
        logger.info("Langfuse observability enabled")

    worker_stop = asyncio.Event()
    worker_task = None
    reconciliation_task = None
    a2a_task = None
    approval_escalation_task = None
    agent_job_task = None
    if get_settings().outbox_worker_enabled:
        worker_task = asyncio.create_task(
            run_outbox_worker(
                AsyncSessionLocal,
                worker_id=get_settings().outbox_worker_id,
                poll_seconds=get_settings().outbox_worker_poll_seconds,
                stop_event=worker_stop,
            )
        )
        logger.info("Outbox worker started", worker_id=get_settings().outbox_worker_id)
    if get_settings().reconciliation_worker_enabled:
        reconciliation_task = asyncio.create_task(
            run_reconciliation_worker(
                AsyncSessionLocal,
                poll_seconds=get_settings().reconciliation_worker_poll_seconds,
                stop_event=worker_stop,
            )
        )
        logger.info("CDC reconciliation worker started")
    if get_settings().a2a_worker_enabled:
        a2a_task = asyncio.create_task(
            run_a2a_worker(
                AsyncSessionLocal,
                worker_id=get_settings().a2a_worker_id,
                tenant_ids=[
                    tenant.strip()
                    for tenant in get_settings().a2a_worker_tenants.split(",")
                    if tenant.strip()
                ]
                or [get_settings().default_tenant_id],
                poll_seconds=get_settings().a2a_worker_poll_seconds,
                stop_event=worker_stop,
            )
        )
        logger.info("A2A task worker started", worker_id=get_settings().a2a_worker_id)
    if get_settings().approval_escalation_worker_enabled:
        approval_escalation_task = asyncio.create_task(
            run_approval_escalation_worker(
                AsyncSessionLocal,
                poll_seconds=get_settings().approval_escalation_poll_seconds,
                stop_event=worker_stop,
            )
        )
        logger.info("Approval escalation worker started")
    if get_settings().agent_job_worker_enabled:
        agent_job_task = asyncio.create_task(
            run_agent_job_worker(
                graph_runtime.ticket_graph,
                AsyncSessionLocal,
                worker_id=get_settings().agent_job_worker_id,
                poll_seconds=get_settings().agent_job_poll_seconds,
                stop_event=worker_stop,
            )
        )
        logger.info("Agent job worker started", worker_id=get_settings().agent_job_worker_id)

    yield

    if any(task is not None for task in (worker_task, reconciliation_task, a2a_task, approval_escalation_task, agent_job_task)):
        worker_stop.set()
    if worker_task is not None:
        try:
            await asyncio.wait_for(worker_task, timeout=5)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            worker_task.cancel()
    if reconciliation_task is not None:
        try:
            await asyncio.wait_for(reconciliation_task, timeout=5)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            reconciliation_task.cancel()
    if a2a_task is not None:
        try:
            await asyncio.wait_for(a2a_task, timeout=5)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            a2a_task.cancel()
    if approval_escalation_task is not None:
        try:
            await asyncio.wait_for(approval_escalation_task, timeout=5)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            approval_escalation_task.cancel()
    if agent_job_task is not None:
        try:
            await asyncio.wait_for(agent_job_task, timeout=5)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            agent_job_task.cancel()

    if graph_resource is not None:
        await graph_resource.__aexit__(None, None, None)

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
app.add_middleware(TenantContextMiddleware)

app.include_router(chat.router, prefix="/api/agent", tags=["Agent"])
app.include_router(agent_jobs.router, prefix="/api/agent/jobs", tags=["Agent Jobs"])
app.include_router(approvals.router, prefix="/api/agent", tags=["Agent"])
app.include_router(tickets.router, prefix="/api/tickets", tags=["Tickets"])
app.include_router(dashboard.router, prefix="/api/dashboard", tags=["Dashboard"])
app.include_router(admin_config.router, prefix="/api/admin", tags=["Admin"])
app.include_router(prompt_governance.router, prefix="/api/admin/prompts", tags=["Prompt Governance"])
app.include_router(erp_mock.router, prefix="/api/erp", tags=["ERP Mock"])
app.include_router(erp_runtime.router, prefix="/api/erp", tags=["ERP Connector Runtime"])
app.include_router(erp_governance.router, prefix="/api/erp/governance", tags=["ERP Governance"])
app.include_router(commercial.router, prefix="/api/commercial", tags=["Commercial Operations"])
app.include_router(mcp_server.router, tags=["MCP"])
app.include_router(a2a_server.router, tags=["A2A"])


# ── 认证路由（签发 JWT，供开发/测试使用）────────────────────────────────────────
@app.post("/auth/token", tags=["Auth"])
async def issue_token(
    user_id: str = Body(...),
    role: str = Body(default="USER"),
    tenant_id: str = Body(default=settings.default_tenant_id),
):
    """
    开发用：凭 user_id + role 签发 JWT。
    生产环境应替换为真实 SSO / OAuth2 流程。
    """
    token = create_access_token(user_id=user_id, role=role, tenant_id=tenant_id)
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
