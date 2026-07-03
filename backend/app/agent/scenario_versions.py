"""File-backed versioning for scenario configuration governance."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
from pathlib import Path
import shutil
from typing import Any
from uuid import uuid4

from app.agent.scenario_registry import (
    DEFAULT_SCENARIO_DIR,
    ScenarioConfig,
    ensure_scenario_storage_initialized,
    reload_default_registry,
)
from app.agent.scenario_validation import validate_scenario_config

VERSION_DIR = DEFAULT_SCENARIO_DIR / "_versions"


@dataclass(frozen=True)
class ScenarioVersion:
    scenario_id: str
    version_id: str
    action: str
    created_at: str
    author: str
    note: str
    status: str
    valid: bool
    file_name: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "version_id": self.version_id,
            "action": self.action,
            "created_at": self.created_at,
            "author": self.author,
            "note": self.note,
            "status": self.status,
            "valid": self.valid,
            "file_name": self.file_name,
        }


def create_scenario_version(
    scenario: ScenarioConfig,
    *,
    action: str,
    author: str = "system",
    note: str = "",
) -> ScenarioVersion:
    ensure_scenario_storage_initialized(DEFAULT_SCENARIO_DIR)
    VERSION_DIR.mkdir(parents=True, exist_ok=True)
    created_at = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    safe_action = action.replace("/", "_").replace("\\", "_")
    version_id = f"{created_at}_{safe_action}_{uuid4().hex[:8]}"
    report = validate_scenario_config(scenario)
    payload = {
        "metadata": {
            "scenario_id": scenario.id,
            "version_id": version_id,
            "action": action,
            "created_at": created_at,
            "author": author,
            "note": note,
            "status": scenario.status,
            "valid": report.valid,
            "validation": report.to_dict(),
        },
        "scenario": scenario.to_dict(),
    }
    path = _version_path(scenario.id, version_id)
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return _version_from_payload(path, payload)


def list_scenario_versions(scenario_id: str) -> list[ScenarioVersion]:
    ensure_scenario_storage_initialized(DEFAULT_SCENARIO_DIR)
    VERSION_DIR.mkdir(parents=True, exist_ok=True)
    versions: list[ScenarioVersion] = []
    for path in sorted(VERSION_DIR.glob(f"{scenario_id}__*.json"), reverse=True):
        with path.open("r", encoding="utf-8") as f:
            versions.append(_version_from_payload(path, json.load(f)))
    return versions


def get_scenario_version(scenario_id: str, version_id: str) -> dict[str, Any]:
    path = _version_path(scenario_id, version_id)
    if not path.exists():
        raise FileNotFoundError(version_id)
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def publish_scenario(
    scenario_id: str,
    *,
    author: str = "system",
    note: str = "",
) -> ScenarioVersion:
    scenario = _read_current_scenario(scenario_id)
    scenario_data = scenario.to_dict()
    scenario_data["status"] = "active"
    published = ScenarioConfig.from_dict(scenario_data)
    report = validate_scenario_config(published)
    if not report.valid:
        raise ValueError(f"Scenario '{scenario_id}' has validation errors and cannot be published.")
    _write_current_scenario(published)
    reload_default_registry()
    return create_scenario_version(published, action="publish", author=author, note=note)


def rollback_scenario(
    scenario_id: str,
    version_id: str,
    *,
    author: str = "system",
    note: str = "",
) -> ScenarioVersion:
    payload = get_scenario_version(scenario_id, version_id)
    scenario = ScenarioConfig.from_dict(payload["scenario"])
    _write_current_scenario(scenario)
    reload_default_registry()
    return create_scenario_version(
        scenario,
        action=f"rollback_{version_id}",
        author=author,
        note=note or f"Rollback to {version_id}",
    )


def diff_scenario_version(scenario_id: str, version_id: str) -> dict[str, Any]:
    """Compare a saved scenario version with the current editable scenario."""
    payload = get_scenario_version(scenario_id, version_id)
    version_scenario = payload["scenario"]
    current_scenario = _read_current_scenario(scenario_id).to_dict()
    changes = _diff_values(version_scenario, current_scenario)
    return {
        "scenario_id": scenario_id,
        "version_id": version_id,
        "change_count": len(changes),
        "changes": changes,
        "legend": {
            "version_value": "Value stored in the selected version.",
            "current_value": "Value currently active/editable in the scenario config.",
        },
    }


def _read_current_scenario(scenario_id: str) -> ScenarioConfig:
    ensure_scenario_storage_initialized(DEFAULT_SCENARIO_DIR)
    path = DEFAULT_SCENARIO_DIR / f"{scenario_id}.json"
    if not path.exists():
        raise FileNotFoundError(scenario_id)
    with path.open("r", encoding="utf-8") as f:
        return ScenarioConfig.from_dict(json.load(f))


def _write_current_scenario(scenario: ScenarioConfig) -> None:
    ensure_scenario_storage_initialized(DEFAULT_SCENARIO_DIR)
    path = DEFAULT_SCENARIO_DIR / f"{scenario.id}.json"
    tmp_path = path.with_suffix(".json.tmp")
    with tmp_path.open("w", encoding="utf-8") as f:
        json.dump(scenario.to_dict(), f, ensure_ascii=False, indent=2)
        f.write("\n")
    shutil.move(str(tmp_path), str(path))


def _diff_values(left: Any, right: Any, path: str = "") -> list[dict[str, Any]]:
    if isinstance(left, dict) and isinstance(right, dict):
        changes: list[dict[str, Any]] = []
        for key in sorted(set(left) | set(right)):
            next_path = f"{path}.{key}" if path else str(key)
            if key not in left:
                changes.append({"path": next_path, "change_type": "added", "version_value": None, "current_value": right[key]})
            elif key not in right:
                changes.append({"path": next_path, "change_type": "removed", "version_value": left[key], "current_value": None})
            else:
                changes.extend(_diff_values(left[key], right[key], next_path))
        return changes

    if isinstance(left, list) and isinstance(right, list):
        if left == right:
            return []
        return [
            {
                "path": path,
                "change_type": "changed",
                "version_value": left,
                "current_value": right,
            }
        ]

    if left != right:
        return [
            {
                "path": path,
                "change_type": "changed",
                "version_value": left,
                "current_value": right,
            }
        ]
    return []


def _version_path(scenario_id: str, version_id: str) -> Path:
    safe_version = version_id.replace("/", "_").replace("\\", "_")
    return VERSION_DIR / f"{scenario_id}__{safe_version}.json"


def _version_from_payload(path: Path, payload: dict[str, Any]) -> ScenarioVersion:
    metadata = payload.get("metadata", {})
    return ScenarioVersion(
        scenario_id=str(metadata.get("scenario_id") or ""),
        version_id=str(metadata.get("version_id") or ""),
        action=str(metadata.get("action") or ""),
        created_at=str(metadata.get("created_at") or ""),
        author=str(metadata.get("author") or ""),
        note=str(metadata.get("note") or ""),
        status=str(metadata.get("status") or ""),
        valid=bool(metadata.get("valid", False)),
        file_name=path.name,
    )
