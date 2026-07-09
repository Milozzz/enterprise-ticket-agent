import os
from functools import lru_cache
from pathlib import Path

from dotenv import dotenv_values
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# config.py 位于 backend/app/core/ — 向上两级为 backend 目录，再向上一级为仓库根
_BACKEND_ROOT = Path(__file__).resolve().parents[2]
_REPO_ROOT = _BACKEND_ROOT.parent


def _env_file_tuple() -> tuple[str, ...]:
    """同时加载仓库根目录与 backend 下的 .env（后者后加载，覆盖前者同名变量）。
    避免只在根目录 .env 里写配置时，从 backend 启动 uvicorn 读不到的问题。"""
    files: list[str] = []
    repo_env = _REPO_ROOT / ".env"
    back_env = _BACKEND_ROOT / ".env"
    if repo_env.is_file():
        files.append(str(repo_env))
    if back_env.is_file():
        files.append(str(back_env))
    if not files:
        files.append(str(back_env))
    return tuple(files)


def _truthy(s: str | None) -> bool:
    if s is None or str(s).strip() == "":
        return False
    return str(s).strip().lower() in ("1", "true", "yes", "on")


def effective_simulate_database_down() -> bool:
    """是否模拟数据库故障（聊天/审批降级）。
    与 `Settings.simulate_database_down` 分开实现：只读 `SIMULATE_DATABASE_DOWN`，
    先读进程环境变量（非空则生效），再按 `_env_file_tuple` 合并 .env（后者覆盖前者），
    避免与其它 Settings 字段的加载顺序混在一块导致本地 .env 写了 true 仍不生效。"""
    raw = os.environ.get("SIMULATE_DATABASE_DOWN")
    if raw is not None and str(raw).strip() != "":
        return _truthy(raw)
    merged: dict[str, str | None] = {}
    for fp in _env_file_tuple():
        path = Path(fp)
        if path.is_file():
            merged.update(dotenv_values(path, encoding="utf-8"))
    return _truthy(merged.get("SIMULATE_DATABASE_DOWN"))


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_env_file_tuple(),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # App
    environment: str = "development"
    secret_key: str = "change-me-in-production"
    admin_api_key: str = ""
    frontend_origin: str = ""
    # JWT
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60 * 24  # 24h
    # Rate limiting (requests per minute per user)
    rate_limit_rpm: int = 60

    # Database
    database_url: str = "sqlite+aiosqlite:///./ticket.db"
    default_tenant_id: str = "TENANT-DEMO-COMMERCE"
    field_encryption_key: str = ""
    field_encryption_key_id: str = "local-dev-v1"
    kms_provider: str = "local"  # local | http
    kms_endpoint: str = ""
    kms_bearer_token: str = ""
    pii_default_retention_days: int = 365
    outbox_worker_enabled: bool = False
    outbox_worker_poll_seconds: float = 1.0
    outbox_worker_id: str = "web-embedded-worker"
    reconciliation_worker_enabled: bool = False
    reconciliation_worker_poll_seconds: float = 5.0
    a2a_worker_enabled: bool = False
    a2a_worker_poll_seconds: float = 1.0
    a2a_worker_id: str = "web-a2a-worker"
    a2a_worker_tenants: str = ""
    a2a_allowed_callback_hosts: str = ""
    a2a_default_callback_url: str = ""
    a2a_callback_bearer_token: str = ""
    # Set this to a mounted persistent directory in production. When empty, the
    # platform uses the bundled repo scenarios.
    scenario_config_dir: str = ""
    # Day 18：设为 true 时模拟数据库不可用，聊天流返回友好文案而非异常栈
    simulate_database_down: bool = False
    # database_url: str = "postgresql+asyncpg://ticketuser:ticketpass@localhost:5432/ticketdb"

    # Redis
    redis_url: str = "redis://localhost:6379"
    # Upstash Redis（用于 LangGraph Checkpointer 和缓存）
    # 格式：rediss://:<password>@<host>:<port>  (Upstash 用 rediss:// + TLS)
    # 留空时回退到 redis_url（本地开发用 MemorySaver）
    upstash_redis_url: str = ""
    # 缓存 TTL（秒），相同 (user_id, message) 命中后直接返回缓存
    chat_cache_ttl: int = 300

    # LLM（仅使用 Google Gemini，免费额度充足）
    google_api_key: str = ""
    gemini_model: str = "gemini-2.0-flash"
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-3-5-haiku-latest"
    model_temperature: float = 0.0             # Agent 决策时用确定性更强的低温度
    llm_default_provider: str = "gemini"
    # Optional JSON map: {"classify_intent": [{"provider":"gemini","model":"..."}]}
    llm_node_routes_json: str = ""
    # Prices are configurable estimates in USD per one million tokens.
    llm_price_catalog_json: str = ""
    # B8: 每租户每日 LLM token 预算硬上限；<=0 表示关闭（默认关闭）。
    # 超额时 gateway 拒绝调用，节点走规则降级路径并标注 degraded。
    llm_tenant_daily_token_budget: int = 0
    # ── A2: 企业 SSO（OIDC 外部 IdP）────────────────────────────────────
    # 启用后 RS256/ES256 token 走 IdP JWKS 验证；本地 HS256 dev token 仍然可用。
    oidc_enabled: bool = False
    oidc_issuer: str = ""
    oidc_audience: str = ""
    oidc_jwks_url: str = ""        # 缺省从 issuer 的 openid-configuration 自动发现
    oidc_role_claim: str = "roles"
    oidc_tenant_claim: str = "tenant_id"
    oidc_default_role: str = "USER"
    # ── A3: Supervisor 语义路由 ─────────────────────────────────────────
    # 关键词置信度低于 route 阈值时调用 LLM 结构化路由；仍低于 clarify 阈值则主动澄清。
    supervisor_llm_routing_enabled: bool = False
    supervisor_route_confidence_threshold: float = 0.55
    supervisor_clarify_confidence_threshold: float = 0.45
    # A3 完整形态：置信度过低时中断对话向用户澄清（一轮），用户下一条消息即回答
    supervisor_clarification_enabled: bool = False
    # ── A4: 工具自主规划层（plan-execute on tool_gateway）───────────────
    # 默认关闭；开启后声明了 planner_enabled 的场景由 LLM 规划工具序列，
    # 每步仍经 tool_gateway 治理（授权/校验/熔断/审计）。
    agent_planner_enabled: bool = False
    agent_planner_max_steps: int = 5
    # A4 完整形态：react=步间重规划（观察-决策-执行循环，失败可补救）；
    # plan_execute=一次规划顺序执行（更保守）。连续失败达到上限即终止。
    agent_planner_mode: str = "react"
    agent_planner_max_consecutive_failures: int = 2
    # F4：允许规划器调用 ERP 只读工具（erp_get_order/erp_query_doctype），
    # 用于多步只读诊断（"这笔退款卡在哪"）。只读、走连接器治理路径，默认关闭。
    planner_erp_readonly_enabled: bool = False
    # ── A7: 场景 slot 的 LLM 结构化提取（正则保留为降级路径）────────────
    slot_llm_extraction_enabled: bool = False
    # A7 完整形态：必填 slot 缺失时中断对话追问（一轮），仍缺失按默认值继续
    slot_clarification_enabled: bool = False
    # H2（长对话优化）：answer 节点单次 LLM 调用携带的最近消息条数上限。
    # 完整历史仍在 checkpointer，只限制进 prompt 的窗口。
    chat_llm_history_window: int = 20
    # Prompt versions and stable/canary rollout rules, JSON encoded.
    prompt_versions_json: str = ""
    prompt_rollouts_json: str = ""
    policy_canary_json: str = ""
    policy_canary_percent: int = 0

    # RAG: PostgreSQL + pgvector is the primary path; TF-IDF remains the
    # fail-open read-only fallback when the embedding provider is unavailable.
    rag_embedding_model: str = "models/text-embedding-004"
    rag_embedding_dimensions: int = 768
    rag_top_k: int = 4
    rag_candidate_multiplier: int = 4
    rag_chunk_size: int = 420
    rag_chunk_overlap: int = 60
    rag_rerank_enabled: bool = True

    # Business Rules
    risk_threshold_amount: float = 500.0        # 超过此金额触发人工审批
    max_agent_iterations: int = 15              # 防止 Agent 死循环
    agent_timeout_seconds: int = 60            # Agent 执行超时，触发降级
    tool_circuit_failure_threshold: int = 3
    tool_circuit_reset_seconds: int = 30
    approval_escalation_poll_seconds: float = 30.0
    approval_escalation_worker_enabled: bool = False
    long_term_memory_retention_days: int = 730
    agent_job_worker_enabled: bool = False
    agent_job_poll_seconds: float = 1.0
    agent_job_max_attempts: int = 3
    agent_job_worker_id: str = "agent-job-worker"

    # Observability - Langfuse
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "https://cloud.langfuse.com"

    # Email (Gmail)
    gmail_user: str = ""
    gmail_app_password: str = ""

    # SAP / ERP Connector runtime. Secrets stay in environment variables and
    # are never persisted in connector records or audit payloads.
    sap_connector_mode: str = "mock"  # mock | live
    sap_base_url: str = ""
    sap_auth_type: str = "oauth2_client_credentials"
    sap_api_key: str = ""
    sap_username: str = ""
    sap_password: str = ""
    sap_bearer_token: str = ""
    sap_client_id: str = ""
    sap_client_secret: str = ""
    sap_token_url: str = ""
    sap_scope: str = ""
    sap_verify_tls: bool = True
    sap_read_only: bool = True
    sap_shadow_writes: bool = True
    sap_timeout_seconds: float = 15.0
    sap_max_retries: int = 2
    sap_circuit_failure_threshold: int = 3
    sap_circuit_reset_seconds: int = 30
    # Optional JSON object mapping logical operations to tenant-specific OData
    # paths. This keeps S/4HANA public/private cloud differences out of agents.
    sap_operation_paths_json: str = ""
    # F3：ERP Saga 进入 MANUAL_REVIEW 时在 HITL 收件箱建单的 SLA（分钟）
    erp_manual_review_sla_minutes: int = 120

    # Public URL advertised by MCP/A2A discovery documents.
    agent_public_url: str = "http://localhost:8000"

    # Operational objectives used by the readiness and business KPI reports.
    agent_slo_success_rate: float = 0.99
    agent_slo_p95_latency_ms: int = 5000
    agent_slo_max_compensation_rate: float = 0.02

    @field_validator("database_url")
    @classmethod
    def normalize_database_url(cls, value: str) -> str:
        if value.startswith("postgres://"):
            return "postgresql+asyncpg://" + value.removeprefix("postgres://")
        if value.startswith("postgresql://"):
            return "postgresql+asyncpg://" + value.removeprefix("postgresql://")
        return value


def testing_mode_active() -> bool:
    """是否处于测试快捷模式（跳过 JWT / admin key / 限流）。

    安全护栏：生产环境下即使误设 TESTING=1 也强制视为关闭，避免一次误配
    就关掉全部认证。仅在非生产环境允许 TESTING=1 生效。"""
    if os.environ.get("TESTING") != "1":
        return False
    return get_settings().environment != "production"


@lru_cache(maxsize=1)
def _cached_settings() -> "Settings":
    return Settings()


def get_settings() -> Settings:
    """生产环境缓存 Settings（.env 不会变，避免热路径每次读盘解析）；
    非生产不缓存，方便改 .env 后 uvicorn --reload 立即生效。"""
    if os.environ.get("ENVIRONMENT", "development") == "production":
        return _cached_settings()
    return Settings()
