"""Probe policy tests use injected responses only; no executable is run."""

from pathlib import Path

import pytest

from skillforge.knowledge.tool_probes import CliProbePolicy, ProbeResponse, ToolProbeService


class FakeBackend:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def run(self, request):
        self.requests.append(request)
        return self.responses.pop(0)


def arguments(tmp_path):
    repository = tmp_path / "repo"
    repository.mkdir(exist_ok=True)
    return {
        "executable": tmp_path / "tools" / "ruff",
        "repository": repository,
        "expected_version": "0.6.9",
    }


def test_default_off_calls_no_backend(tmp_path):
    backend = FakeBackend([])
    result = ToolProbeService(backend=backend).inspect("ruff", **arguments(tmp_path))
    assert not result.ok and not backend.requests


def test_live_backend_not_available_even_with_consent(tmp_path):
    result = ToolProbeService(CliProbePolicy(enabled=True)).inspect("ruff", **arguments(tmp_path))
    assert not result.ok and "live probing" in result.reason


def test_version_first_fixed_argv_safe_cwd_env_and_cache(tmp_path):
    backend = FakeBackend(
        [
            ProbeResponse(0, "ruff 0.6.9"),
            ProbeResponse(0, "Usage: ruff check\n"),
            ProbeResponse(0, "Usage: ruff format\n"),
        ]
    )
    service = ToolProbeService(CliProbePolicy(enabled=True), backend)
    kwargs = arguments(tmp_path)
    result = service.inspect("ruff", command_path=("check",), **kwargs)
    assert result.ok and result.version == "0.6.9"
    assert [request.argv[1:] for request in backend.requests] == [
        ("--version",),
        ("check", "--help"),
    ]
    assert service.inspect("ruff", command_path=("check",), **kwargs) == result
    assert service.inspect("ruff", command_path=("format",), **kwargs).ok
    assert len(backend.requests) == 3
    for request in backend.requests:
        assert not request.cwd.is_relative_to(kwargs["repository"])
        assert "PYTHONPATH" not in request.environment
        assert request.environment["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] == "1"
        assert request.max_output_bytes == 64 * 1024 and request.timeout_seconds == 8
        assert not request.cwd.exists()  # temporary cwd cleaned on return


@pytest.mark.parametrize("version", ["ruff 1.0.0", "arbitrary output", "ruff 0.6.9\nplugin noise"])
def test_unknown_or_wrong_version_never_runs_help(tmp_path, version):
    backend = FakeBackend([ProbeResponse(0, version)])
    result = ToolProbeService(CliProbePolicy(enabled=True), backend).inspect(
        "ruff", **arguments(tmp_path)
    )
    assert not result.ok and len(backend.requests) == 1


def test_repository_paths_and_nonallowlisted_commands_refused(tmp_path):
    backend = FakeBackend([])
    service = ToolProbeService(CliProbePolicy(enabled=True), backend)
    kwargs = arguments(tmp_path)
    for tool, command in [
        ("npx", ()),
        ("ruff", ("check", ";rm")),
        ("ruff", ("--config", "repo.toml")),
    ]:
        assert not service.inspect(tool, command_path=command, **kwargs).ok
    kwargs["executable"] = kwargs["repository"] / "ruff"
    assert not service.inspect("ruff", **kwargs).ok
    kwargs["executable"] = Path("relative")
    assert not service.inspect("ruff", **kwargs).ok
    assert not backend.requests


@pytest.mark.parametrize(
    "response",
    [
        ProbeResponse(1, "fail"),
        ProbeResponse(0, timed_out=True),
        ProbeResponse(0, truncated=True),
        ProbeResponse(0, "x" * 100),
    ],
)
def test_failed_or_oversized_probe_is_not_promoted(tmp_path, response):
    backend = FakeBackend([response])
    result = ToolProbeService(CliProbePolicy(enabled=True, max_output_bytes=32), backend).inspect(
        "ruff", **arguments(tmp_path)
    )
    assert not result.ok
