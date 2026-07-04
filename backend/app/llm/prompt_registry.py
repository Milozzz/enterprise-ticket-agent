from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from functools import lru_cache

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)
settings = get_settings()


@dataclass(frozen=True)
class PromptSelection:
    node_name: str
    template: str
    version: str
    variant: str
    rollout_bucket: int

    def to_audit_event(self) -> dict:
        return {
            "node": self.node_name,
            "prompt_version": self.version,
            "prompt_variant": self.variant,
            "rollout_bucket": self.rollout_bucket,
            "template_hash": hashlib.sha256(self.template.encode()).hexdigest(),
        }


class PromptRegistry:
    """Deterministic stable/canary prompt routing with no runtime randomness."""

    def __init__(self, versions_json: str = "", rollouts_json: str = "") -> None:
        self.versions = self._parse(versions_json, "prompt_versions")
        self.rollouts = self._parse(rollouts_json, "prompt_rollouts")

    @staticmethod
    def _parse(value: str, label: str) -> dict:
        if not value.strip():
            return {}
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except (TypeError, ValueError) as exc:
            logger.warning("invalid_prompt_registry_config", config=label, error=str(exc))
            return {}

    def select(
        self,
        node_name: str,
        default_template: str,
        *,
        routing_key: str,
        default_version: str = "builtin-v1",
    ) -> PromptSelection:
        bucket = int(hashlib.sha256(f"{node_name}:{routing_key}".encode()).hexdigest()[:8], 16) % 100
        node_versions = self.versions.get(node_name) or {}
        rollout = self.rollouts.get(node_name) or {}
        stable = str(rollout.get("stable") or default_version)
        canary = str(rollout.get("canary") or "")
        percent = min(max(int(rollout.get("percent") or 0), 0), 100)
        use_canary = bool(canary and canary in node_versions and bucket < percent)
        version = canary if use_canary else stable
        template = str(node_versions.get(version) or default_template)
        return PromptSelection(
            node_name=node_name,
            template=template,
            version=version,
            variant="canary" if use_canary else "stable",
            rollout_bucket=bucket,
        )

    def describe(self) -> dict:
        nodes = sorted(set(self.versions) | set(self.rollouts))
        return {
            "nodes": [
                {
                    "node": node,
                    "versions": sorted((self.versions.get(node) or {}).keys()),
                    "rollout": self.rollouts.get(node) or {"stable": "builtin-v1", "percent": 0},
                }
                for node in nodes
            ],
            "routing": "sha256(node:routing_key) % 100",
        }


@lru_cache
def get_prompt_registry() -> PromptRegistry:
    return PromptRegistry(settings.prompt_versions_json, settings.prompt_rollouts_json)
