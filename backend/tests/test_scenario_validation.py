import json

from app.agent.scenario_registry import ScenarioConfig, get_default_registry
from app.agent.scenario_validation import validate_scenario_config


def _clone_scenario_data(scenario_id: str) -> dict:
    return json.loads(json.dumps(get_default_registry().get(scenario_id).to_dict()))


def test_default_scenarios_pass_validation_without_errors():
    reports = [validate_scenario_config(config) for config in get_default_registry().list()]

    assert all(report.valid for report in reports)
    assert sum(report.error_count for report in reports) == 0


def test_config_driven_workflow_requires_runtime_contract():
    data = _clone_scenario_data("permission_request")
    data["runtime"] = {}

    report = validate_scenario_config(ScenarioConfig.from_dict(data))

    assert report.valid is False
    assert any(issue.code == "runtime.required" for issue in report.issues)


def test_runtime_tool_must_have_generic_handler_and_scenario_declaration():
    data = _clone_scenario_data("permission_request")
    data["runtime"]["tool"]["name"] = "lookup_order"

    report = validate_scenario_config(ScenarioConfig.from_dict(data))

    assert report.valid is False
    assert any(issue.code == "runtime.tool.unknown" for issue in report.issues)


def test_runtime_references_must_point_to_defined_slots():
    data = _clone_scenario_data("reimbursement")
    data["runtime"]["tool"]["args"]["amount"] = "$slots.total_amount"

    report = validate_scenario_config(ScenarioConfig.from_dict(data))

    assert report.valid is False
    assert any(issue.code == "runtime.reference.slot_unknown" for issue in report.issues)


def test_runtime_regex_validation_catches_invalid_patterns():
    data = _clone_scenario_data("reimbursement")
    data["runtime"]["slot_extraction"]["fields"]["amount"]["regex"] = "("

    report = validate_scenario_config(ScenarioConfig.from_dict(data))

    assert report.valid is False
    assert any(issue.code == "runtime.regex.invalid" for issue in report.issues)
