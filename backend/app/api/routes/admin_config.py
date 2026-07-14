"""Admin APIs for configuring the supervisor scenario platform."""

from __future__ import annotations

import json
import hmac
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field, field_validator

from app.agent.generic_runtime import run_configured_scenario
from app.agent.knowledge_base import (
    knowledge_base_report,
    rebuild_policy_index,
    test_policy_retrieval,
)
from app.agent.mcp_adapter import list_mcp_compatible_tools
from app.agent.saga import refund_saga_template
from app.agent.scenario_eval import (
    list_scenario_eval_catalog,
    run_all_scenario_evals,
    run_scenario_eval,
)
from app.agent.enterprise_readiness import run_enterprise_readiness_eval
from app.agent.p0_evaluation import run_p0_eval_report
from app.agent.scenario_registry import (
    DEFAULT_SCENARIO_DIR,
    ScenarioConfig,
    ensure_scenario_storage_initialized,
    get_default_registry,
    reload_default_registry,
)
from app.agent.scenario_schema import RUNTIME_SCHEMAS, RUNTIME_V2_SCHEMA, RUNTIME_V3_SCHEMA
from app.agent.plan_graph import specialist_catalog_report
from app.agent.agent_depth_eval import run_agent_depth_eval
from app.agent.agent_resilience_eval import run_agent_resilience_eval
from app.agent.evidence_store import load_persisted_evidence_graph
from app.agent.scenario_validation import (
    summarize_validation,
    validate_scenario_config,
)
from app.agent.scenario_templates import instantiate_template, list_scenario_templates
from app.agent.scenario_versions import (
    create_scenario_version,
    diff_scenario_version,
    list_scenario_versions,
    publish_scenario,
    rollback_scenario,
)
from app.agent.tool_gateway import list_tool_specs, tool_registry_report
from app.agent.workflow_factory import WORKFLOW_ENTRYPOINTS
from app.core.policy import get_policy_version, load_policy
from app.core.config import get_settings
from app.db.database import AsyncSessionLocal

router = APIRouter()


def require_admin_api_key(
    admin_api_key: Annotated[str | None, Header(alias="X-Admin-API-Key")] = None,
) -> None:
    from app.core.config import testing_mode_active
    if testing_mode_active():
        return
    configured = get_settings().admin_api_key
    if configured and (not admin_api_key or not hmac.compare_digest(configured, admin_api_key)):
        raise HTTPException(status_code=403, detail="Admin API key is required.")


class HITLConfigPayload(BaseModel):
    enabled: bool = True
    review_roles: list[str] = Field(default_factory=list)
    description: str = ""
    approval_chain: list[dict[str, Any]] = Field(default_factory=list)


class ScenarioConfigPayload(BaseModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    name: str = Field(min_length=2, max_length=80)
    description: str = Field(max_length=500)
    workflow: str
    status: Literal["active", "draft", "paused"] = "active"
    owner: str = Field(min_length=2, max_length=80)
    business_domain: str = Field(min_length=2, max_length=80)
    logic_module: str = Field(max_length=160)
    logic_file: str = Field(max_length=200)
    sla_minutes: int = Field(default=30, ge=1, le=10080)
    tags: list[str] = Field(default_factory=list)
    intents: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    allowed_roles: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    policies: list[str] = Field(default_factory=list)
    hitl: HITLConfigPayload = Field(default_factory=HITLConfigPayload)
    runtime: dict[str, Any] = Field(default_factory=dict)

    @field_validator("keywords", "allowed_roles", "tools", "policies", "intents", "tags")
    @classmethod
    def dedupe_non_empty(cls, values: list[str]) -> list[str]:
        cleaned: list[str] = []
        seen: set[str] = set()
        for item in values:
            value = str(item).strip()
            if not value:
                continue
            key = value.lower()
            if key in seen:
                continue
            seen.add(key)
            cleaned.append(value)
        return cleaned

    @field_validator("workflow")
    @classmethod
    def workflow_must_exist(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized or not normalized.replace("_", "a").isalnum():
            raise ValueError("Workflow must be a non-empty alphanumeric identifier")
        return normalized


class RouteSimulationPayload(BaseModel):
    message: str = Field(min_length=1, max_length=500)


class RuntimeSimulationPayload(BaseModel):
    message: str = Field(min_length=1, max_length=1000)
    user_id: str = Field(default="admin-simulator", max_length=80)
    user_role: str = Field(default="USER", max_length=40)
    dry_run: bool = True


class PolicySearchPayload(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    top_k: int = Field(default=3, ge=1, le=10, alias="topK")
    user_role: str = Field(default="USER", max_length=40, alias="userRole")


class PolicyIndexPayload(BaseModel):
    force: bool = False


class VersionActionPayload(BaseModel):
    author: str = Field(default="admin", max_length=80)
    note: str = Field(default="", max_length=500)


class RollbackPayload(VersionActionPayload):
    version_id: str = Field(alias="versionId")


class TemplateCreatePayload(BaseModel):
    template_id: str = Field(alias="templateId")
    scenario_id: str = Field(alias="scenarioId", pattern=r"^[a-z][a-z0-9_]*$")


def _scenario_path(scenario_id: str) -> Path:
    ensure_scenario_storage_initialized(DEFAULT_SCENARIO_DIR)
    return DEFAULT_SCENARIO_DIR / f"{scenario_id}.json"


def _write_scenario(payload: ScenarioConfigPayload) -> dict[str, Any]:
    data = payload.model_dump()
    data["allowed_roles"] = [role.upper() for role in data["allowed_roles"]]
    data["hitl"]["review_roles"] = [role.upper() for role in data["hitl"]["review_roles"]]
    data["hitl"]["approval_chain"] = _normalize_approval_chain(data["hitl"].get("approval_chain", []))
    scenario = ScenarioConfig.from_dict(data)
    report = validate_scenario_config(scenario)
    if not report.valid:
        raise HTTPException(status_code=422, detail=report.to_dict())

    path = _scenario_path(payload.id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    reload_default_registry()
    create_scenario_version(scenario, action="save", author="admin", note="Saved from Scenario Studio")
    scenario_dict = scenario.to_dict()
    scenario_dict["validation"] = report.to_dict()
    return scenario_dict


def _policy_catalog() -> list[dict[str, Any]]:
    policy = load_policy()
    return [
        {
            "name": key,
            "rule_count": len(value.get("rules", [])) if isinstance(value, dict) else 0,
            "rules": value.get("rules", []) if isinstance(value, dict) else [],
        }
        for key, value in policy.items()
        if key.endswith("_review") and isinstance(value, dict)
    ]


@router.get("/scenarios")
async def get_scenario_admin_config() -> dict[str, Any]:
    configs = get_default_registry().list()
    reports = [validate_scenario_config(config) for config in configs]
    scenarios = []
    for config, report in zip(configs, reports):
        scenario_dict = config.to_dict()
        scenario_dict["validation"] = report.to_dict()
        scenarios.append(scenario_dict)

    return {
        "scenarios": scenarios,
        "tools": list_tool_specs(),
        "workflows": [
            {"name": name, "entrypoint": entrypoint}
            for name, entrypoint in WORKFLOW_ENTRYPOINTS.items()
        ],
        "policies": _policy_catalog(),
        "policy_version": get_policy_version(),
        "validation_summary": summarize_validation(reports),
        "runtime_schema": RUNTIME_V2_SCHEMA,
        "runtime_schemas": RUNTIME_SCHEMAS,
        "specialist_catalog": specialist_catalog_report(),
        "eval_catalog": list_scenario_eval_catalog(),
        "templates": list_scenario_templates(),
        "roles": ["USER", "AGENT", "MANAGER", "SECURITY", "FINANCE"],
        "status_options": ["active", "draft", "paused"],
    }


@router.post("/scenarios/validate")
async def validate_scenario_payload(payload: ScenarioConfigPayload) -> dict[str, Any]:
    data = payload.model_dump()
    data["allowed_roles"] = [role.upper() for role in data["allowed_roles"]]
    data["hitl"]["review_roles"] = [role.upper() for role in data["hitl"]["review_roles"]]
    data["hitl"]["approval_chain"] = _normalize_approval_chain(data["hitl"].get("approval_chain", []))
    report = validate_scenario_config(ScenarioConfig.from_dict(data))
    return {"validation": report.to_dict()}


@router.get("/scenarios/schema/runtime-v2")
async def get_runtime_v2_schema() -> dict[str, Any]:
    return RUNTIME_V2_SCHEMA


@router.get("/scenarios/schema/runtime-v3")
async def get_runtime_v3_schema() -> dict[str, Any]:
    return RUNTIME_V3_SCHEMA


@router.get("/scenarios/schema/runtime")
async def get_runtime_schemas() -> dict[str, Any]:
    return {"versions": RUNTIME_SCHEMAS, "latest": "3"}


@router.get("/agent-architecture")
async def get_agent_architecture() -> dict[str, Any]:
    return {
        "runtime": "TaskSpec -> PlanGraph -> Specialists -> EvidenceGraph -> Verifier -> Policy/HITL -> Executor",
        "specialists": specialist_catalog_report(),
        "depth_eval": run_agent_depth_eval(),
        "resilience_eval": run_agent_resilience_eval(),
        "dynamic_planning_default": "disabled; validated deterministic plan remains the production fallback",
    }


@router.get(
    "/evidence/tasks/{task_id}",
    dependencies=[Depends(require_admin_api_key)],
)
async def get_task_evidence(
    task_id: str,
    tenant_id: Annotated[str | None, Header(alias="X-Tenant-ID")] = None,
) -> dict[str, Any]:
    tenant = tenant_id or get_settings().default_tenant_id
    graph = await load_persisted_evidence_graph(
        task_id,
        tenant_id=tenant,
        session_factory=AsyncSessionLocal,
    )
    if not graph["claims"]:
        raise HTTPException(status_code=404, detail="Task evidence was not found")
    return {"evidence_graph": graph}


def _normalize_approval_chain(chain: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for index, stage in enumerate(chain or []):
        if not isinstance(stage, dict):
            continue
        normalized.append(
            {
                "id": str(stage.get("id") or f"stage_{index + 1}"),
                "name": str(stage.get("name") or stage.get("id") or f"Stage {index + 1}"),
                "roles": [str(role).upper() for role in stage.get("roles", [])],
                "required": bool(stage.get("required", True)),
            }
        )
    return normalized


@router.put("/scenarios/{scenario_id}")
async def update_scenario(scenario_id: str, payload: ScenarioConfigPayload) -> dict[str, Any]:
    if payload.id != scenario_id:
        raise HTTPException(status_code=400, detail="Scenario id in path and body must match.")
    return {"scenario": _write_scenario(payload)}


@router.post("/scenarios")
async def create_scenario(payload: ScenarioConfigPayload) -> dict[str, Any]:
    path = _scenario_path(payload.id)
    if path.exists():
        raise HTTPException(status_code=409, detail=f"Scenario '{payload.id}' already exists.")
    return {"scenario": _write_scenario(payload)}


@router.get("/scenarios/templates")
async def get_scenario_templates() -> dict[str, Any]:
    return {"templates": list_scenario_templates()}


@router.post("/scenarios/from-template")
async def create_scenario_from_template(payload: TemplateCreatePayload) -> dict[str, Any]:
    path = _scenario_path(payload.scenario_id)
    if path.exists():
        raise HTTPException(status_code=409, detail=f"Scenario '{payload.scenario_id}' already exists.")
    try:
        data = instantiate_template(payload.template_id, payload.scenario_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Unknown template '{payload.template_id}'.") from exc
    scenario_payload = ScenarioConfigPayload.model_validate(data)
    return {"scenario": _write_scenario(scenario_payload)}


@router.get("/scenarios/{scenario_id}/versions")
async def get_scenario_versions(scenario_id: str) -> dict[str, Any]:
    return {"versions": [version.to_dict() for version in list_scenario_versions(scenario_id)]}


@router.post("/scenarios/{scenario_id}/publish")
async def publish_scenario_route(scenario_id: str, payload: VersionActionPayload) -> dict[str, Any]:
    try:
        version = publish_scenario(scenario_id, author=payload.author, note=payload.note)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"Unknown scenario '{scenario_id}'.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"version": version.to_dict(), "scenario": get_default_registry().get(scenario_id).to_dict()}


@router.post("/scenarios/{scenario_id}/rollback")
async def rollback_scenario_route(scenario_id: str, payload: RollbackPayload) -> dict[str, Any]:
    try:
        version = rollback_scenario(scenario_id, payload.version_id, author=payload.author, note=payload.note)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"Unknown scenario version '{payload.version_id}'.") from exc
    return {"version": version.to_dict(), "scenario": get_default_registry().get(scenario_id).to_dict()}


@router.get("/scenarios/{scenario_id}/versions/{version_id}/diff")
async def get_scenario_version_diff(scenario_id: str, version_id: str) -> dict[str, Any]:
    try:
        return {"diff": diff_scenario_version(scenario_id, version_id)}
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"Unknown scenario version '{version_id}'.") from exc


@router.get("/scenarios/evals/catalog")
async def get_scenario_eval_catalog() -> dict[str, Any]:
    return {"eval_catalog": list_scenario_eval_catalog()}


@router.get("/evals/report")
async def get_all_scenario_eval_report() -> dict[str, Any]:
    return {"eval_report": await run_all_scenario_evals()}


@router.get("/evals/p0-report")
async def get_p0_eval_report() -> dict[str, Any]:
    return {"eval_report": await run_p0_eval_report(use_llm_judge=False)}


@router.get("/evals/agent-resilience")
async def get_agent_resilience_report() -> dict[str, Any]:
    return {"eval_report": run_agent_resilience_eval()}


@router.get("/evals/enterprise-readiness")
async def get_enterprise_readiness_report(
    connector_id: str = "CONN-SAP-ODATA-DEMO",
) -> dict[str, Any]:
    return {"readiness": await run_enterprise_readiness_eval(connector_id)}


@router.post("/scenarios/{scenario_id}/eval")
async def run_scenario_eval_route(scenario_id: str) -> dict[str, Any]:
    return {"eval": await run_scenario_eval(scenario_id)}


@router.get("/tools/registry")
async def get_tool_registry_report() -> dict[str, Any]:
    return tool_registry_report()


@router.get("/tools/mcp")
async def get_mcp_tool_descriptors() -> dict[str, Any]:
    return {"tools": list_mcp_compatible_tools()}


@router.get("/knowledge/policies")
async def get_policy_knowledge_base() -> dict[str, Any]:
    return await knowledge_base_report()


@router.post("/knowledge/policies/search")
async def search_policy_knowledge_base(payload: PolicySearchPayload) -> dict[str, Any]:
    return {
        "result": await test_policy_retrieval(
            payload.query,
            top_k=payload.top_k,
            user_role=payload.user_role,
        )
    }


@router.post("/knowledge/policies/reindex")
async def reindex_policy_knowledge_base(
    payload: PolicyIndexPayload,
    _: Annotated[None, Depends(require_admin_api_key)],
) -> dict[str, Any]:
    try:
        return {"index": await rebuild_policy_index(force=payload.force)}
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/saga/templates/refund")
async def get_refund_saga_template() -> dict[str, Any]:
    return refund_saga_template()


@router.post("/scenarios/simulate-route")
async def simulate_scenario_route(payload: RouteSimulationPayload) -> dict[str, Any]:
    match = get_default_registry().match(payload.message)
    return {
        "scenario_id": match.scenario_id,
        "workflow": match.workflow,
        "confidence": match.confidence,
        "matched_keywords": list(match.matched_keywords),
        "reason": match.reason,
        "scenario": match.config.to_dict(),
    }


@router.post("/scenarios/{scenario_id}/simulate-runtime")
async def simulate_scenario_runtime(scenario_id: str, payload: RuntimeSimulationPayload) -> dict[str, Any]:
    state = {
        "messages": [{"role": "user", "content": payload.message}],
        "user_id": payload.user_id,
        "user_role": payload.user_role.upper(),
        "thread_id": f"admin-sim-{scenario_id}",
        "trace_id": f"admin-sim-{scenario_id}",
    }
    try:
        result = await run_configured_scenario(state, scenario_id, dry_run=payload.dry_run)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {
        "scenario_id": scenario_id,
        "dry_run": payload.dry_run,
        "result": result,
    }
