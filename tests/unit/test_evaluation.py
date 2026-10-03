"""Tests for the evaluation framework."""

from __future__ import annotations

import json
from pathlib import Path

from tests.conftest import REPO_ROOT, write_file

from skillforge.errors import ConfigError, NotFoundError
from skillforge.evaluation import (
    EvaluationRunner,
    NullAgentExecutor,
    RecordedAgentExecutor,
    load_scenario,
    load_scenarios,
)
from skillforge.models import NOT_MEASURED

BENCHMARKS = REPO_ROOT / "benchmarks" / "scenarios"


def test_repository_scenarios_are_valid() -> None:
    scenarios = load_scenarios(BENCHMARKS)
    assert len(scenarios) >= 4
    ids = [scenario.id for scenario in scenarios]
    assert len(ids) == len(set(ids))
    for scenario in scenarios:
        assert scenario.fixture
        assert (REPO_ROOT / scenario.fixture).is_dir(), scenario.fixture
        assert scenario.checks, scenario.id


def test_load_scenario_rejects_invalid_check_type(tmp_path: Path) -> None:
    write_file(
        tmp_path,
        "bad.toml",
        'id = "x"\ntitle = "t"\nfixture = "f"\n\n[[checks]]\ntype = "not-a-check"\n',
    )
    try:
        load_scenario(tmp_path / "bad.toml")
    except ConfigError as exc:
        assert "checks" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected ConfigError")


def test_load_scenarios_missing_directory(tmp_path: Path) -> None:
    try:
        load_scenarios(tmp_path / "nope")
    except NotFoundError:
        return
    raise AssertionError("expected NotFoundError")


def test_runner_measures_deterministic_metrics_only(tmp_path: Path) -> None:
    scenarios = [
        scenario for scenario in load_scenarios(BENCHMARKS) if scenario.id == "go-add-handler"
    ]
    report = EvaluationRunner(repo_root=REPO_ROOT, scenarios=scenarios).run()
    assert len(report.scenarios) == 1
    scenario = report.scenarios[0]
    assert scenario.passed
    assert scenario.skills
    metrics = {metric.name: metric for metric in scenario.metrics}
    assert metrics["skills_generated"].measured
    assert metrics["validation_errors"].value == 0
    assert metrics["dangerous_commands_embedded"].value == 0
    assert metrics["commands_with_provenance"].value == 100.0
    # Agent metrics are honestly not measured.
    assert metrics["task_success[with-skill]"].display() == NOT_MEASURED
    assert metrics["tokens_consumed[without-skill]"].display() == NOT_MEASURED
    assert all(metric.measured is False for metric in metrics.values() if "[" in metric.name)
    assert report.checks_failed == 0


def test_runner_reports_missing_fixture(tmp_path: Path) -> None:
    scenario = load_scenarios(BENCHMARKS)[0].model_copy(
        update={"fixture": "tests/fixtures/does-not-exist"}
    )
    report = EvaluationRunner(repo_root=REPO_ROOT, scenarios=[scenario]).run()
    assert report.scenarios[0].outcomes
    assert report.scenarios[0].outcomes[0].passed is False


def test_null_executor_is_explicitly_unmeasured() -> None:
    result = NullAgentExecutor().run("s", "task", "with-skill")
    assert result.measured is False
    assert "NOT MEASURED" in result.notes or "not measured" in result.notes.lower()


def test_recorded_executor_reads_recordings(tmp_path: Path) -> None:
    payload = {
        "scenario": "demo",
        "condition": "with-skill",
        "agent": "claude-code",
        "model": "claude-sonnet-4-5",
        "success": True,
        "tokens": 1000,
        "tool_calls": 7,
        "latency_seconds": 12.5,
    }
    (tmp_path / "demo.json").write_text(json.dumps(payload), encoding="utf-8")
    executor = RecordedAgentExecutor(tmp_path)
    measured = executor.run("demo", "task", "with-skill")
    assert measured.measured
    assert measured.success is True
    assert measured.tokens == 1000
    missing = executor.run("demo", "task", "without-skill")
    assert missing.measured is False
    assert "no recording" in missing.notes


def test_recordings_produce_measured_comparison(tmp_path: Path) -> None:
    (tmp_path / "go-add-handler.json").write_text(
        json.dumps(
            {
                "scenario": "go-add-handler",
                "condition": "with-skill",
                "success": True,
                "tokens": 900,
                "tool_calls": 5,
                "latency_seconds": 10.0,
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "go-add-handler-without.json").write_text(
        json.dumps(
            {
                "scenario": "go-add-handler",
                "condition": "without-skill",
                "success": True,
                "tokens": 1500,
                "tool_calls": 9,
                "latency_seconds": 18.0,
            }
        ),
        encoding="utf-8",
    )
    scenarios = [
        scenario for scenario in load_scenarios(BENCHMARKS) if scenario.id == "go-add-handler"
    ]
    report = EvaluationRunner(
        repo_root=REPO_ROOT, scenarios=scenarios, executor=RecordedAgentExecutor(tmp_path)
    ).run()
    comparison = {row.metric: row for row in report.comparisons}
    assert comparison["tokens_consumed"].without_skill.value == 1500
    assert comparison["tokens_consumed"].with_skill.value == 900
    assert comparison["tool_calls"].with_skill.measured


def test_all_scenarios_pass() -> None:
    """The full benchmark suite must be green on the checked-in fixtures."""
    scenarios = load_scenarios(BENCHMARKS)
    report = EvaluationRunner(repo_root=REPO_ROOT, scenarios=scenarios).run()
    failures = [
        (scenario.scenario_id, outcome.id)
        for scenario in report.scenarios
        for outcome in scenario.outcomes
        if outcome.measured and outcome.passed is False
    ]
    assert not failures, failures
    assert report.checks_failed == 0
