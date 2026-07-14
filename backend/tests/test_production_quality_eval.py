from app.agent.production_quality_eval import (
    build_production_quality_cases,
    run_production_quality_eval,
)


def test_production_quality_suite_has_balanced_hundred_case_gate():
    cases = build_production_quality_cases()
    dimensions = {case.dimension for case in cases}

    assert len(cases) == 120
    assert dimensions == {"routing", "slot_extraction", "policy", "authorization"}


def test_production_quality_suite_passes_all_core_decisions():
    report = run_production_quality_eval()

    assert report["case_count"] == 120
    assert report["pass_rate"] == 1.0, report["failures"]
