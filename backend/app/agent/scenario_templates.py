"""Scenario template marketplace for configurable enterprise agents."""

from __future__ import annotations

from copy import deepcopy
import json
from typing import Any

from app.agent.scenario_registry import DEFAULT_SCENARIO_DIR


SCENARIO_TEMPLATES: dict[str, dict[str, Any]] = {
    "access_governance": {
        "id": "access_governance",
        "name": "Access Governance",
        "description": "RBAC access request with manager/security approvals.",
        "category": "ITSM",
        "recommended_workflow": "permission_request_workflow",
        "base_scenario": "permission_request",
        "scenario": {
            "name": "Access Governance",
            "description": "Enterprise access request workflow generated from template.",
            "workflow": "permission_request_workflow",
            "status": "draft",
            "owner": "Security & IT",
            "business_domain": "Access Governance",
            "logic_module": "app.agent.nodes.permission_request.permission_request_node",
            "logic_file": "backend/app/agent/nodes/permission_request.py",
            "sla_minutes": 60,
            "tags": ["Template", "RBAC", "Approval"],
            "intents": ["permission_request"],
            "keywords": ["access request", "permission", "申请权限"],
            "allowed_roles": ["USER", "AGENT", "MANAGER"],
            "tools": ["create_permission_request"],
            "policies": ["permission_request_review"],
            "hitl": {
                "enabled": True,
                "review_roles": ["MANAGER", "SECURITY"],
                "description": "Privileged access requires manager and security review.",
                "approval_chain": [
                    {"id": "manager_review", "name": "Manager review", "roles": ["MANAGER"], "required": True},
                    {"id": "security_review", "name": "Security review", "roles": ["SECURITY"], "required": True},
                ],
            },
        },
    },
    "expense_reimbursement": {
        "id": "expense_reimbursement",
        "name": "Expense Reimbursement",
        "description": "Employee reimbursement request with finance controls.",
        "category": "Finance",
        "recommended_workflow": "reimbursement_workflow",
        "base_scenario": "reimbursement",
        "scenario": {
            "name": "Expense Reimbursement",
            "description": "Employee expense workflow generated from template.",
            "workflow": "reimbursement_workflow",
            "status": "draft",
            "owner": "Finance",
            "business_domain": "Expense Management",
            "logic_module": "app.agent.nodes.reimbursement.reimbursement_node",
            "logic_file": "backend/app/agent/nodes/reimbursement.py",
            "sla_minutes": 120,
            "tags": ["Template", "Expense", "Approval"],
            "intents": ["reimbursement"],
            "keywords": ["reimbursement", "expense", "invoice", "报销"],
            "allowed_roles": ["USER", "AGENT", "MANAGER"],
            "tools": ["create_reimbursement_request"],
            "policies": ["reimbursement_review"],
            "hitl": {
                "enabled": True,
                "review_roles": ["MANAGER", "FINANCE"],
                "description": "Large or sensitive expenses require manager and finance review.",
                "approval_chain": [
                    {"id": "manager_review", "name": "Manager review", "roles": ["MANAGER"], "required": True},
                    {"id": "finance_review", "name": "Finance review", "roles": ["FINANCE"], "required": True},
                ],
            },
        },
    },
}


def list_scenario_templates() -> list[dict[str, Any]]:
    return [
        {
            "id": template["id"],
            "name": template["name"],
            "description": template["description"],
            "category": template["category"],
            "recommended_workflow": template["recommended_workflow"],
        }
        for template in SCENARIO_TEMPLATES.values()
    ]


def instantiate_template(template_id: str, scenario_id: str) -> dict[str, Any]:
    template = SCENARIO_TEMPLATES.get(template_id)
    if template is None:
        raise KeyError(template_id)
    base_path = DEFAULT_SCENARIO_DIR / f"{template['base_scenario']}.json"
    if base_path.exists():
        with base_path.open("r", encoding="utf-8") as f:
            scenario = json.load(f)
    else:
        scenario = {}
    scenario.update(deepcopy(template["scenario"]))
    scenario["id"] = scenario_id
    return scenario
