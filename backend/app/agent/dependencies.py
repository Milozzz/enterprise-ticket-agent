from __future__ import annotations

from dataclasses import dataclass

from app.db.database import AsyncSessionLocal
from app.llm.gateway import LLMGateway, get_llm_gateway

_DEFAULT_SESSION_FACTORY = AsyncSessionLocal


@dataclass
class AgentDependencies:
    llm: LLMGateway
    session_factory: object = AsyncSessionLocal


_dependencies: AgentDependencies | None = None


def get_agent_dependencies() -> AgentDependencies:
    global _dependencies
    if _dependencies is None:
        _dependencies = AgentDependencies(llm=get_llm_gateway())
    return _dependencies


def set_agent_dependencies(dependencies: AgentDependencies | None) -> None:
    """Test and composition-root override; pass None to restore defaults."""

    global _dependencies
    _dependencies = dependencies


def resolve_session_factory(local_default=None):
    """Prefer an injected factory while retaining legacy module-level test overrides."""

    dependencies = get_agent_dependencies()
    if dependencies.session_factory is _DEFAULT_SESSION_FACTORY and local_default is not None:
        return local_default
    return dependencies.session_factory
