import asyncio

from app.agent.p0_evaluation import run_p0_eval_report, run_rag_eval, run_safety_eval
from app.agent.rag_service import chunk_policy_document
from app.agent.tools.policy_tools import POLICY_DOCS, search_policy_raw
from app.agent.scenario_registry import ScenarioConfig, ScenarioRegistry, get_default_registry
from app.agent import graph as graph_runtime
from scripts.repo_hygiene_check import find_violations


def test_policy_corpus_has_production_scale_and_stable_citations():
    assert len(POLICY_DOCS) >= 50
    hits = search_policy_raw("生产数据库写权限需要审批", top_k=4)

    assert hits
    assert all(hit.policy_id and hit.paragraph_id and hit.source for hit in hits)
    assert all(hit.retrieval_method == "tfidf" for hit in hits)


def test_paragraph_chunking_is_bounded_and_citation_stable():
    content = "第一条政策。" * 80
    chunks = chunk_policy_document("DOC-001", content, chunk_size=120, overlap=20)

    assert len(chunks) > 1
    assert chunks[0].paragraph_id == "DOC-001#p1"
    assert chunks[-1].paragraph_id == f"DOC-001#p{len(chunks)}"
    assert all(len(chunk.content) <= 120 for chunk in chunks)


def test_rag_eval_meets_recall_and_faithfulness_gate():
    report = run_rag_eval()

    assert report["corpus_document_count"] >= 50
    assert report["recall_at_k"] >= 0.8
    assert report["citation_faithfulness"] == 1.0


def test_safety_eval_runs_24_red_team_cases():
    report = run_safety_eval()

    assert report["case_count"] == 24
    assert report["pass_rate"] == 1.0
    assert set(report["categories"]) == {"prompt_injection", "authorization", "pii_leakage"}


def test_p0_release_report_passes_all_quality_gates():
    report = asyncio.run(run_p0_eval_report(use_llm_judge=False))

    assert report["status"] == "PASS"
    assert all(report["quality_gates"].values())
    assert report["summary"] == {
        "trajectory_cases": 6,
        "rag_cases": 12,
        "safety_cases": 24,
        "judge_cases": 2,
            "production_quality_cases": 120,
            "agent_depth_cases": 48,
            "agent_resilience_cases": 14,
        }


def test_repository_hygiene_rules_reject_sensitive_artifacts():
    violations = find_violations(
        [".env", "ticket.db", "resume.pdf", ".worktrees/tmp/index", "backend/evals/results_old.json"]
    )

    assert len(violations) == 5
    assert find_violations([".env.example", "backend/evals/eval_report_sample.json"]) == []


def test_new_configured_scenario_is_discovered_without_new_graph_code(monkeypatch):
    existing = {scenario.id: scenario for scenario in get_default_registry().list()}
    existing["purchase_request"] = ScenarioConfig(
        id="purchase_request",
        name="Purchase Request",
        description="Config-only test scenario",
        workflow="configured_workflow",
        runtime={"schema_version": "2"},
    )
    registry = ScenarioRegistry(existing)
    monkeypatch.setattr(graph_runtime, "get_default_registry", lambda: registry)

    graph = graph_runtime.build_graph()

    assert "purchase_request" in graph.get_graph().nodes
