import os
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
    frontend_origin: str = ""
    # JWT
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60 * 24  # 24h
    # Rate limiting (requests per minute per user)
    rate_limit_rpm: int = 60

    # Database
    database_url: str = "sqlite+aiosqlite:///./ticket.db"
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
    model_temperature: float = 0.0             # Agent 决策时用确定性更强的低温度

    # Business Rules
    risk_threshold_amount: float = 500.0        # 超过此金额触发人工审批
    max_agent_iterations: int = 15              # 防止 Agent 死循环
    agent_timeout_seconds: int = 60            # Agent 执行超时，触发降级

    # Observability - Langfuse
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "https://cloud.langfuse.com"

    # Email (Gmail)
    gmail_user: str = ""
    gmail_app_password: str = ""

    @field_validator("database_url")
    @classmethod
    def normalize_database_url(cls, value: str) -> str:
        if value.startswith("postgres://"):
            return "postgresql+asyncpg://" + value.removeprefix("postgres://")
        if value.startswith("postgresql://"):
            return "postgresql+asyncpg://" + value.removeprefix("postgresql://")
        return value


def get_settings() -> Settings:
    """每次调用重新读取环境变量与 .env（不使用 lru_cache）。
    否则改 .env 后 uvicorn --reload 往往不会重启进程，聊天里仍拿到旧的 simulate_database_down 等配置。"""
    return Settings()
