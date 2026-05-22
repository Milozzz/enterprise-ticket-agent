"""Config-driven scenario registry for the supervisor platform."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from functools import lru_cache
from pathlib import Path
import shutil
from typing import Any

from app.agent.router_fallback import token_embedding_similarity
from app.core.config import get_settings


BUNDLED_SCENARIO_DIR = Path(__file__).resolve().parents[1] / "scenarios"
_settings = get_settings()
DEFAULT_SCENARIO_DIR = (
    Path(_settings.scenario_config_dir).expanduser().resolve()
    if _settings.scenario_config_dir.strip()
    else BUNDLED_SCENARIO_DIR
)
DEFAULT_SCENARIO_ID = "refund"


@dataclass(frozen=True)
class ApprovalStageConfig:
    id: str
    name: str
    roles: tuple[str, ...] = ()
    required: bool = True


@dataclass(frozen=True)
class ScenarioHITLConfig:
    enabled: bool = False
    review_roles: tuple[str, ...] = ()
    description: str = ""
    approval_chain: tuple[ApprovalStageConfig, ...] = ()


@dataclass(frozen=True)
class ScenarioConfig:
    id: str
    name: str
    description: str
    workflow: str
    status: str = "active"
    owner: str = "Platform"
    business_domain: str = "General"
    logic_module: str = ""
    logic_file: str = ""
    sla_minutes: int = 30
    tags: tuple[str, ...] = ()
    intents: tuple[str, ...] = ()
    keywords: tuple[str, ...] = ()
    allowed_roles: tuple[str, ...] = ()
    tools: tuple[str, ...] = ()
    policies: tuple[str, ...] = ()
    hitl: ScenarioHITLConfig = field(default_factory=ScenarioHITLConfig)
    runtime: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ScenarioConfig":
        hitl_data = data.get("hitl") or {}
        approval_chain = tuple(
            ApprovalStageConfig(
                id=str(item.get("id") or f"stage_{index + 1}"),
                name=str(item.get("name") or item.get("id") or f"Stage {index + 1}"),
                roles=tuple(str(role).upper() for role in item.get("roles", [])),
                required=bool(item.get("required", True)),
            )
            for index, item in enumerate(hitl_data.get("approval_chain", []))
            if isinstance(item, dict)
        )
        return cls(
            id=str(data["id"]),
            name=str(data.get("name") or data["id"]),
            description=str(data.get("description") or ""),
            workflow=str(data["workflow"]),
            status=str(data.get("status") or "active"),
            owner=str(data.get("owner") or "Platform"),
            business_domain=str(data.get("business_domain") or "General"),
            logic_module=str(data.get("logic_module") or ""),
            logic_file=str(data.get("logic_file") or ""),
            sla_minutes=int(data.get("sla_minutes") or 30),
            tags=tuple(str(item) for item in data.get("tags", [])),
            intents=tuple(str(item) for item in data.get("intents", [])),
            keywords=tuple(str(item).lower() for item in data.get("keywords", [])),
            allowed_roles=tuple(str(item).upper() for item in data.get("allowed_roles", [])),
            tools=tuple(str(item) for item in data.get("tools", [])),
            policies=tuple(str(item) for item in data.get("policies", [])),
            hitl=ScenarioHITLConfig(
                enabled=bool(hitl_data.get("enabled", False)),
                review_roles=tuple(str(item).upper() for item in hitl_data.get("review_roles", [])),
                description=str(hitl_data.get("description") or ""),
                approval_chain=approval_chain,
            ),
            runtime=dict(data.get("runtime") or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "workflow": self.workflow,
            "status": self.status,
            "owner": self.owner,
            "business_domain": self.business_domain,
            "logic_module": self.logic_module,
            "logic_file": self.logic_file,
            "sla_minutes": self.sla_minutes,
            "tags": list(self.tags),
            "intents": list(self.intents),
            "keywords": list(self.keywords),
            "allowed_roles": list(self.allowed_roles),
            "tools": list(self.tools),
            "policies": list(self.policies),
            "hitl": {
                "enabled": self.hitl.enabled,
                "review_roles": list(self.hitl.review_roles),
                "description": self.hitl.description,
                "approval_chain": [
                    {
                        "id": stage.id,
                        "name": stage.name,
                        "roles": list(stage.roles),
                        "required": stage.required,
                    }
                    for stage in self.hitl.approval_chain
                ],
            },
            "runtime": self.runtime,
        }


@dataclass(frozen=True)
class ScenarioMatch:
    scenario_id: str
    workflow: str
    confidence: float
    matched_keywords: tuple[str, ...]
    reason: str
    config: ScenarioConfig


class ScenarioRegistry:
    """Loads scenario JSON files and provides deterministic keyword routing."""

    def __init__(self, scenarios: dict[str, ScenarioConfig]):
        if DEFAULT_SCENARIO_ID not in scenarios:
            raise ValueError(f"Default scenario '{DEFAULT_SCENARIO_ID}' is required.")
        self._scenarios = dict(scenarios)

    @classmethod
    def load_from_dir(cls, scenario_dir: Path = DEFAULT_SCENARIO_DIR) -> "ScenarioRegistry":
        ensure_scenario_storage_initialized(scenario_dir)
        scenarios: dict[str, ScenarioConfig] = {}
        for path in sorted(scenario_dir.glob("*.json")):
            with path.open("r", encoding="utf-8") as f:
                config = ScenarioConfig.from_dict(json.load(f))
            scenarios[config.id] = config
        return cls(scenarios)

    def get(self, scenario_id: str, default: str = DEFAULT_SCENARIO_ID) -> ScenarioConfig:
        return self._scenarios.get(scenario_id) or self._scenarios[default]

    def list(self) -> list[ScenarioConfig]:
        return list(self._scenarios.values())

    def match(self, message: str) -> ScenarioMatch:
        text = (message or "").lower()
        best_config: ScenarioConfig | None = None
        best_keywords: tuple[str, ...] = ()

        for config in self._scenarios.values():
            matched = tuple(keyword for keyword in config.keywords if keyword and keyword in text)
            if len(matched) > len(best_keywords):
                best_config = config
                best_keywords = matched

        if best_config is None or not best_keywords:
            semantic_match = self._semantic_fallback(message)
            if semantic_match is not None:
                return semantic_match
            fallback = self._scenarios[DEFAULT_SCENARIO_ID]
            return ScenarioMatch(
                scenario_id=fallback.id,
                workflow=fallback.workflow,
                confidence=0.35,
                matched_keywords=(),
                reason="No scenario keyword matched; fallback to the default refund/general support workflow.",
                config=fallback,
            )

        confidence = min(0.95, 0.55 + 0.1 * len(best_keywords))
        return ScenarioMatch(
            scenario_id=best_config.id,
            workflow=best_config.workflow,
            confidence=confidence,
            matched_keywords=best_keywords,
            reason=f"Matched scenario keywords: {', '.join(best_keywords)}",
            config=best_config,
        )

    def _semantic_fallback(self, message: str) -> ScenarioMatch | None:
        best_config: ScenarioConfig | None = None
        best_score = 0.0
        for config in self._scenarios.values():
            candidates = [
                config.name,
                config.description,
                config.business_domain,
                *config.intents,
                *config.keywords,
                *config.tags,
            ]
            score = token_embedding_similarity(message, candidates)
            if score > best_score:
                best_score = score
                best_config = config

        if best_config is None or best_score < 0.03:
            return None

        return ScenarioMatch(
            scenario_id=best_config.id,
            workflow=best_config.workflow,
            confidence=min(0.7, 0.45 + best_score),
            matched_keywords=(),
            reason=f"Embedding-style router fallback selected '{best_config.id}' with similarity {best_score:.2f}.",
            config=best_config,
        )


@lru_cache(maxsize=1)
def get_default_registry() -> ScenarioRegistry:
    return ScenarioRegistry.load_from_dir()


def reload_default_registry() -> ScenarioRegistry:
    get_default_registry.cache_clear()
    return get_default_registry()


def list_scenarios() -> list[dict[str, Any]]:
    return [scenario.to_dict() for scenario in get_default_registry().list()]


def ensure_scenario_storage_initialized(scenario_dir: Path = DEFAULT_SCENARIO_DIR) -> None:
    """Create and seed a configured scenario directory for persistent deployments."""
    scenario_dir.mkdir(parents=True, exist_ok=True)
    if scenario_dir == BUNDLED_SCENARIO_DIR or any(scenario_dir.glob("*.json")):
        return
    for source in BUNDLED_SCENARIO_DIR.glob("*.json"):
        shutil.copy2(source, scenario_dir / source.name)
