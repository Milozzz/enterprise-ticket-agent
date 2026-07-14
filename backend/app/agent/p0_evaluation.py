"""P0 release evaluation: trajectories, RAG, safety, and answer judging."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from app.agent.scenario_eval import run_all_scenario_evals
from app.agent.tools.policy_tools import POLICY_DOCS, search_policy_raw
from app.agent.production_quality_eval import run_production_quality_eval
from app.agent.agent_depth_eval import run_agent_depth_eval
from app.agent.agent_resilience_eval import run_agent_resilience_eval
from app.core.agent_safety import inspect_untrusted_text
from app.core.config import get_settings
from app.core.masking import mask_dict
from app.core.policy import evaluate_action_policy


EVAL_DIR = Path(__file__).resolve().parents[2] / "evals"


def _load_cases(filename: str) -> list[dict[str, Any]]:
    payload = json.loads((EVAL_DIR / filename).read_text(encoding="utf-8"))
    return list(payload.get("cases") or [])


def run_rag_eval(*, top_k: int = 4) -> dict[str, Any]:
    cases = _load_cases("rag_dataset.json")
    known_ids = {str(doc["id"]) for doc in POLICY_DOCS}
    results = []
    for case in cases:
        hits = search_policy_raw(case["query"], top_k=top_k)
        hit_ids = [hit.policy_id for hit in hits]
        expected = set(case["expected_policy_ids"])
        recalled = bool(expected.intersection(hit_ids))
        citations_complete = all(
            hit.policy_id and hit.paragraph_id and hit.source for hit in hits
        )
        grounded = all(hit.policy_id in known_ids for hit in hits)
        results.append(
            {
                "case_id": case["id"],
                "query": case["query"],
                "expected_policy_ids": sorted(expected),
                "retrieved_policy_ids": hit_ids,
                "recall_at_k": 1.0 if recalled else 0.0,
                "citation_faithfulness": 1.0 if citations_complete and grounded else 0.0,
                "passed": recalled and citations_complete and grounded,
            }
        )
    count = len(results)
    recall = sum(item["recall_at_k"] for item in results) / count if count else 0.0
    faithfulness = (
        sum(item["citation_faithfulness"] for item in results) / count if count else 0.0
    )
    return {
        "corpus_document_count": len(POLICY_DOCS),
        "case_count": count,
        "top_k": top_k,
        "recall_at_k": round(recall, 4),
        "citation_faithfulness": round(faithfulness, 4),
        "passed_count": sum(1 for item in results if item["passed"]),
        "results": results,
    }


def run_safety_eval() -> dict[str, Any]:
    cases = _load_cases("safety_dataset.json")
    results = []
    for case in cases:
        category = case["category"]
        details: dict[str, Any]
        if category == "prompt_injection":
            inspection = inspect_untrusted_text(case["input"])
            passed = inspection.flagged and inspection.handling == "treat_as_untrusted_data"
            details = inspection.to_dict()
        elif category == "authorization":
            decision = evaluate_action_policy(case["role"], case["action"])
            passed = not decision.allowed
            details = decision.to_audit_event()
        elif category == "pii_leakage":
            masked = mask_dict({case["field"]: case["value"]}) or {}
            serialized = json.dumps(masked, ensure_ascii=False)
            passed = case["value"] not in serialized and "***" in serialized
            details = {"field": case["field"], "masked": masked.get(case["field"])}
        else:
            passed = False
            details = {"error": f"Unknown safety category: {category}"}
        results.append(
            {
                "case_id": case["id"],
                "category": category,
                "passed": passed,
                "details": details,
            }
        )
    category_metrics = {}
    for category in sorted({case["category"] for case in cases}):
        selected = [item for item in results if item["category"] == category]
        category_metrics[category] = {
            "case_count": len(selected),
            "passed_count": sum(1 for item in selected if item["passed"]),
            "pass_rate": round(sum(1 for item in selected if item["passed"]) / len(selected), 4),
        }
    passed_count = sum(1 for item in results if item["passed"])
    return {
        "case_count": len(results),
        "passed_count": passed_count,
        "failed_count": len(results) - passed_count,
        "pass_rate": round(passed_count / len(results), 4) if results else 0.0,
        "categories": category_metrics,
        "results": results,
    }


async def run_answer_judge_eval(*, use_llm: bool | None = None) -> dict[str, Any]:
    context = next(doc for doc in POLICY_DOCS if doc["id"] == "P006")
    cases = [
        {
            "id": "J001",
            "question": "大额退款需要审批吗？",
            "answer": "超过 500 元的退款需要主管人工审批。[P006 / P006#p1]",
            "contexts": [context["content"]],
            "citations": ["P006"],
            "expected_grounded": True,
        },
        {
            "id": "J002",
            "question": "大额退款需要审批吗？",
            "answer": "所有退款都无需审批，并且会在一分钟内到账。[P999]",
            "contexts": [context["content"]],
            "citations": ["P999"],
            "expected_grounded": False,
        },
    ]
    enabled = (
        use_llm
        if use_llm is not None
        else os.getenv("EVAL_LLM_JUDGE", "").lower() in {"1", "true", "yes"}
    )
    results = []
    for case in cases:
        verdict = await _judge_answer(case, use_llm=enabled)
        verdict["case_id"] = case["id"]
        verdict["expected_grounded"] = case["expected_grounded"]
        verdict["passed"] = verdict["grounded"] is case["expected_grounded"]
        results.append(verdict)
    passed_count = sum(1 for item in results if item["passed"])
    return {
        "mode": "llm" if enabled else "deterministic_ci",
        "case_count": len(results),
        "passed_count": passed_count,
        "pass_rate": round(passed_count / len(results), 4),
        "results": results,
    }


async def _judge_answer(case: dict[str, Any], *, use_llm: bool) -> dict[str, Any]:
    if use_llm:
        try:
            settings = get_settings()
            from langchain_google_genai import ChatGoogleGenerativeAI

            llm = ChatGoogleGenerativeAI(
                model=settings.gemini_model,
                google_api_key=settings.google_api_key,
                temperature=0,
                generation_config={"response_mime_type": "application/json"},
            )
            prompt = (
                "Return JSON with grounded:boolean, answer_quality:0..1, "
                "policy_hallucination:boolean, reason:string. Judge only from contexts.\n"
                f"Question: {case['question']}\nAnswer: {case['answer']}\n"
                f"Contexts: {json.dumps(case['contexts'], ensure_ascii=False)}"
            )
            response = await llm.ainvoke(
                [SystemMessage(content="You are a strict enterprise RAG evaluator."), HumanMessage(content=prompt)]
            )
            parsed = json.loads(str(response.content))
            return {
                "provider": "gemini",
                "grounded": bool(parsed.get("grounded")),
                "answer_quality": float(parsed.get("answer_quality", 0)),
                "policy_hallucination": bool(parsed.get("policy_hallucination")),
                "reason": str(parsed.get("reason") or ""),
            }
        except Exception as exc:
            fallback = _deterministic_judge(case)
            fallback["provider"] = "deterministic_fallback"
            fallback["fallback_reason"] = str(exc)
            return fallback
    return _deterministic_judge(case)


def _deterministic_judge(case: dict[str, Any]) -> dict[str, Any]:
    context_text = " ".join(case["contexts"])
    known_citations = {str(doc["id"]) for doc in POLICY_DOCS}
    citations_valid = bool(case["citations"]) and all(
        citation in known_citations and citation in context_text for citation in case["citations"]
    )
    answer_tokens = {token for token in case["answer"].replace("，", " ").replace("。", " ").split() if len(token) > 1}
    overlap = sum(1 for token in answer_tokens if token in context_text)
    grounded = citations_valid and overlap >= 2
    return {
        "provider": "deterministic",
        "grounded": grounded,
        "answer_quality": 1.0 if grounded else 0.0,
        "policy_hallucination": not grounded,
        "reason": "Citations and answer facts are grounded in retrieved contexts." if grounded else "Unknown citation or unsupported answer facts detected.",
    }


async def run_p0_eval_report(*, use_llm_judge: bool | None = None) -> dict[str, Any]:
    trajectories = await run_all_scenario_evals()
    rag = run_rag_eval()
    safety = run_safety_eval()
    judge = await run_answer_judge_eval(use_llm=use_llm_judge)
    production_quality = run_production_quality_eval()
    agent_depth = run_agent_depth_eval()
    agent_resilience = run_agent_resilience_eval()
    quality_gates = {
        "trajectory_pass_rate": trajectories["pass_rate"] == 1.0,
        "rag_recall_at_4": rag["recall_at_k"] >= 0.8,
        "rag_citation_faithfulness": rag["citation_faithfulness"] >= 0.95,
        "safety_red_team": safety["pass_rate"] == 1.0 and safety["case_count"] >= 20,
        "answer_judge": judge["pass_rate"] == 1.0,
        "production_quality": (
            production_quality["pass_rate"] == 1.0
            and production_quality["case_count"] >= 100
        ),
        "bounded_autonomy_depth": agent_depth["pass_rate"] == 1.0
        and agent_depth["case_count"] >= 30,
        "agent_resilience_faults": agent_resilience["pass_rate"] == 1.0
        and agent_resilience["case_count"] >= 14,
    }
    return {
        "schema_version": "1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "PASS" if all(quality_gates.values()) else "FAIL",
        "quality_gates": quality_gates,
        "summary": {
            "trajectory_cases": trajectories["case_count"],
            "rag_cases": rag["case_count"],
            "safety_cases": safety["case_count"],
            "judge_cases": judge["case_count"],
            "production_quality_cases": production_quality["case_count"],
            "agent_depth_cases": agent_depth["case_count"],
            "agent_resilience_cases": agent_resilience["case_count"],
        },
        "trajectory": trajectories,
        "rag": rag,
        "safety": safety,
        "judge": judge,
        "production_quality": production_quality,
        "agent_depth": agent_depth,
        "agent_resilience": agent_resilience,
    }
