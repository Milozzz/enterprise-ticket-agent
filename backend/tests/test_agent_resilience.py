from app.agent.agent_resilience_eval import run_agent_resilience_eval


def test_agent_resilience_release_gate_covers_all_faults():
    report = run_agent_resilience_eval()

    assert report["case_count"] == 14
    assert report["pass_rate"] == 1.0, report["failures"]
    assert report["quality_metrics"]["replan_success_rate"] == 1.0
    assert report["quality_metrics"]["policy_violation_rate"] == 0.0
    assert report["quality_metrics"]["compensation_success_rate"] == 1.0
    assert {
        "erp_timeout",
        "dirty_tool_output",
        "approval_timeout",
        "cross_system_amount_mismatch",
        "partial_execution_failure",
        "memory_current_fact_conflict",
        "prompt_injection_in_tool_output",
    } <= {item["fault"] for item in report["results"]}
