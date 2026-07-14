from __future__ import annotations

import pytest
import httpx
from types import SimpleNamespace
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.agent.counterfactual_replay import (
    materialize_counterfactual_variants,
    run_counterfactual_replay,
)
from app.agent.delegation import (
    authorize_delegated_tool,
    consume_persisted_delegation,
    create_persisted_delegation,
    issue_delegation_token,
)
from app.agent.information_flow import (
    declassify_for_verified_execution,
    validate_information_flow,
)
from app.agent.online_eval import (
    online_eval_report,
    promote_online_eval_case,
    record_agent_feedback,
)
from app.agent.plan_graph import build_plan_graph
from app.agent.tool_gateway import ToolExecutionContext, execute_tool, settings as gateway_settings
from app.db.database import Base
from app.core.masking import mask_dict
from app.core.auth import get_current_user
from app.db.database import get_db
from app.api.routes.admin_config import require_admin_api_key
from app.main import app


def test_delegation_token_binds_agent_tool_resource_and_constraints():
    token, _, _ = issue_delegation_token(
        grant_id="dlg-test",
        principal_id="user-1",
        principal_role="USER",
        tenant_id="tenant-1",
        agent_id="enterprise-ticket-agent",
        allowed_tools=["execute_refund"],
        resource_scopes={"order_id": ["ORD-1"]},
        constraints={"max_amount": 500, "allowed_currencies": ["CNY"]},
        purpose="Refund one approved order",
        approval_id="approval-1",
    )
    allowed = authorize_delegated_tool(
        token,
        required=True,
        principal_id="user-1",
        principal_role="USER",
        tenant_id="tenant-1",
        agent_id="enterprise-ticket-agent",
        specialist_id="executor",
        tool_name="execute_refund",
        args={"order_id": "ORD-1", "ticket_id": "T-1", "amount": 299, "currency": "CNY"},
        approval_id="approval-1",
    )
    assert allowed.allowed is True

    denied = authorize_delegated_tool(
        token,
        required=True,
        principal_id="user-1",
        principal_role="USER",
        tenant_id="tenant-1",
        agent_id="enterprise-ticket-agent",
        specialist_id="executor",
        tool_name="execute_refund",
        args={"order_id": "ORD-2", "ticket_id": "T-1", "amount": 900, "currency": "USD"},
        approval_id="approval-1",
    )
    assert denied.allowed is False
    assert denied.reason_code in {"DELEGATION_RESOURCE_DENIED", "DELEGATION_CONSTRAINT_DENIED"}


def test_delegation_tokens_are_masked_before_audit_persistence():
    token = "signed-delegation-token-value"
    masked = mask_dict({"delegation_token": token, "principal_token": token})
    assert masked["delegation_token"] != token
    assert masked["principal_token"] != token


def test_taint_tracking_blocks_untrusted_write_and_accepts_verified_evidence():
    blocked = validate_information_flow(
        side_effect="write",
        risk_level="high",
        args={"order_id": "ORD-1", "amount": 299},
        provenance={
            "order_id": {"label": "rag_untrusted"},
            "amount": {"label": "model_generated"},
        },
        required_fields=["order_id", "amount"],
        mode="enforce",
    )
    assert blocked.allowed is False
    assert blocked.reason_code == "TAINT_FLOW_BLOCKED"

    provenance = declassify_for_verified_execution(
        fields={"order_id": "ORD-1", "amount": 299},
        cited_evidence_ids=["ev-order", "ev-amount"],
        verifier="deterministic-verifier",
        human_approved=True,
    )
    allowed = validate_information_flow(
        side_effect="write",
        risk_level="high",
        args={"order_id": "ORD-1", "amount": 299},
        provenance=provenance,
        required_fields=["order_id", "amount"],
        mode="enforce",
    )
    assert allowed.allowed is True


def test_tool_gateway_enforces_taint_before_handler(monkeypatch):
    monkeypatch.setattr(gateway_settings, "agent_information_flow_mode", "enforce")
    called = False

    def handler(**kwargs):
        nonlocal called
        called = True
        return kwargs

    context = ToolExecutionContext(
        actor_role="AGENT",
        requested_by_role="USER",
        user_id="user-1",
        tenant_id="tenant-1",
        argument_provenance={
            "order_id": {"label": "rag_untrusted"},
            "refund_id": {"label": "model_generated"},
        },
    )
    result = execute_tool(
        "send_notification",
        {"order_id": "ORD-1", "refund_id": "R-1"},
        context=context,
        handler=handler,
    )
    assert result.success is False
    assert called is False
    assert result.audit_event["information_flow"]["reason_code"] == "TAINT_FLOW_BLOCKED"


@pytest.mark.asyncio
async def test_persisted_delegation_is_single_use_and_feedback_builds_eval_case(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'governance.db').as_posix()}")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    async with sessions() as session:
        grant, token = await create_persisted_delegation(
            session,
            tenant_id="tenant-1",
            principal_id="user-1",
            principal_role="USER",
            issued_by="manager-1",
            agent_id="enterprise-ticket-agent",
            allowed_tools=["execute_refund"],
            purpose="Approved refund",
            max_uses=1,
        )
        await session.commit()
        assert grant.use_count == 0

    async with sessions() as session:
        consumed = await consume_persisted_delegation(
            session, token, principal_id="user-1", tenant_id="tenant-1"
        )
        await session.commit()
        assert consumed.use_count == 1

    async with sessions() as session:
        with pytest.raises(ValueError, match="use limit"):
            await consume_persisted_delegation(
                session, token, principal_id="user-1", tenant_id="tenant-1"
            )

    async with sessions() as session:
        feedback, eval_case = await record_agent_feedback(
            session,
            tenant_id="tenant-1",
            thread_id="thread-1",
            trace_id="trace-1",
            task_id="task-1",
            scenario_id="refund",
            submitted_by="manager-1",
            disposition="correct",
            rating=2,
            reason_codes=["wrong_policy"],
            correction={"requires_human_approval": True},
            task_snapshot={"order_id": "ORD-1", "amount": 299},
            version_context={"model": "gemini", "prompt_version": "v2", "policy_version": "v3"},
        )
        await session.commit()
        assert feedback.eval_candidate is True
        assert eval_case is not None
        promoted = await promote_online_eval_case(
            session,
            tenant_id="tenant-1",
            case_id=eval_case.case_id,
            reviewed_by="reviewer-1",
            dataset_version="feedback-v1",
        )
        await session.commit()
        assert promoted.status == "approved"
        report = await online_eval_report(session, tenant_id="tenant-1", window_hours=24)
        assert report["current"]["corrected"] == 1
        assert report["dataset"]["approved"] == 1
    await engine.dispose()


def test_counterfactual_replay_compares_policy_without_side_effects():
    task_spec = {
        "task_id": "task-1",
        "scenario_id": "refund",
        "budget": {"max_steps": 20, "max_replans": 1},
        "success_criteria": [],
    }
    snapshot = {
        "task_spec": task_spec,
        "plan_graph": build_plan_graph(task_spec),
        "evidence_graph": {"claims": [{"evidence_id": "ev-1"}]},
        "verification_result": {"status": "pass"},
        "order_amount": 100,
        "risk_score": 0,
        "risk_level": "low",
        "user_history": {},
    }
    strict_policy = {
        "version": "strict-v1",
        "refund_review": {
            "rules": [{"id": "all-refunds-hitl", "when": {"amount_gte": 0}}]
        },
    }
    result = run_counterfactual_replay(
        baseline_snapshot=snapshot,
        variants=[
            {
                "variant_id": "strict-policy",
                "model": "candidate-model",
                "prompt_version": "prompt-v3",
                "policy_document": strict_policy,
            }
        ],
    )
    assert result["side_effects"] == "disabled"
    assert result["variants"][0]["side_effects_executed"] == 0
    assert result["variants"][0]["decision"] == "human_review"
    assert "decision" in result["variants"][0]["difference"]


@pytest.mark.asyncio
async def test_counterfactual_uses_precomputed_model_prompt_output():
    task_spec = {
        "task_id": "task-shadow",
        "scenario_id": "refund",
        "budget": {"max_steps": 20, "max_replans": 1},
        "success_criteria": [],
    }
    snapshot = {
        "task_spec": task_spec,
        "plan_graph": build_plan_graph(task_spec),
        "evidence_graph": {"claims": [{"evidence_id": "ev-1"}]},
        "verification_result": {"status": "pass"},
        "order_amount": 100,
        "risk_score": 0,
        "risk_level": "low",
    }
    variants = await materialize_counterfactual_variants(
        baseline_snapshot=snapshot,
        variants=[
            {
                "variant_id": "model-prompt-candidate",
                "model": "candidate-model",
                "prompt_version": "prompt-v4",
                "model_output": {"plan_graph": {"steps": []}},
            }
        ],
        tenant_id="tenant-1",
        thread_id="thread-shadow",
    )
    result = run_counterfactual_replay(baseline_snapshot=snapshot, variants=variants)
    candidate = result["variants"][0]
    assert candidate["model_execution"]["status"] == "supplied"
    assert candidate["decision"] == "reject_plan"
    assert candidate["side_effects_executed"] == 0


@pytest.mark.asyncio
async def test_counterfactual_live_shadow_model_proposes_plan_without_tools(monkeypatch):
    task_spec = {
        "task_id": "task-live-shadow",
        "scenario_id": "refund",
        "budget": {"max_steps": 20, "max_replans": 1},
        "success_criteria": [],
    }
    baseline_plan = build_plan_graph(task_spec)
    snapshot = {
        "task_spec": task_spec,
        "plan_graph": baseline_plan,
        "evidence_graph": {"claims": [{"evidence_id": "ev-1"}]},
        "verification_result": {"status": "pass"},
        "order_amount": 100,
        "risk_score": 0,
        "risk_level": "low",
    }

    async def fake_ainvoke(self, node_name, messages, **kwargs):
        assert node_name == "counterfactual_planner"
        assert kwargs["candidate_overrides"][0].model == "shadow-model"
        assert "tools" not in kwargs or kwargs["tools"] is None
        return SimpleNamespace(
            output={"plan_graph": baseline_plan, "rationale": "bounded shadow plan"},
            provider="gemini",
            model="shadow-model",
            latency_ms=12,
            total_tokens=42,
            estimated_cost_usd="0.0001",
        )

    monkeypatch.setattr("app.agent.counterfactual_replay.LLMGateway.ainvoke", fake_ainvoke)
    variants = await materialize_counterfactual_variants(
        baseline_snapshot=snapshot,
        variants=[
            {
                "variant_id": "live-shadow",
                "provider": "gemini",
                "model": "shadow-model",
                "prompt_version": "prompt-shadow-v1",
                "run_model": True,
            }
        ],
        tenant_id="tenant-1",
        thread_id="thread-live-shadow",
    )
    result = run_counterfactual_replay(baseline_snapshot=snapshot, variants=variants)
    candidate = result["variants"][0]
    assert candidate["model_execution"]["status"] == "completed"
    assert candidate["plan_errors"] == []
    assert candidate["side_effects_executed"] == 0


@pytest.mark.asyncio
async def test_governance_api_closes_identity_feedback_and_replay_loops(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'governance-api.db').as_posix()}")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    async def override_db():
        async with sessions() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: {
        "user_id": "user-1",
        "role": "AGENT",
        "tenant_id": "tenant-1",
    }
    app.dependency_overrides[require_admin_api_key] = lambda: None
    headers = {"X-Tenant-ID": "tenant-1"}
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
            headers=headers,
        ) as client:
            delegation = await client.post(
                "/api/agent/governance/delegations",
                json={
                    "allowed_tools": ["execute_refund"],
                    "purpose": "Execute one approved refund",
                    "resource_scopes": {"order_id": ["ORD-1"]},
                    "constraints": {"max_amount": 500, "allowed_currencies": ["CNY"]},
                },
            )
            assert delegation.status_code == 200
            assert delegation.json()["delegation_token"]

            feedback = await client.post(
                "/api/agent/governance/feedback",
                json={
                    "thread_id": "thread-1",
                    "scenario_id": "refund",
                    "disposition": "reject",
                    "rating": 1,
                    "reason_codes": ["incorrect_decision"],
                    "task_snapshot": {"order_id": "ORD-1"},
                    "version_context": {"model": "candidate-model", "prompt_version": "v2"},
                },
            )
            assert feedback.status_code == 200
            assert feedback.json()["eval_case"]["status"] == "candidate"

            report = await client.get("/api/agent/governance/online-eval/report")
            assert report.status_code == 200
            assert report.json()["current"]["rejected"] == 1

            replay = await client.post(
                "/api/agent/governance/counterfactual/replay",
                json={
                    "scenario_id": "refund",
                    "variants": [{"variant_id": "candidate", "model": "candidate-model"}],
                },
            )
            assert replay.status_code == 200
            assert replay.json()["result"]["side_effects"] == "disabled"
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(require_admin_api_key, None)
        await engine.dispose()
