"""Tests for the build sandbox helpers."""

import shutil

import pytest
from demo_ghostprovider.hoster._helpers import (
    _run_sandboxed,
    _sandbox_enabled,
    _strip_systemd_status,
)


@pytest.mark.parametrize("value,expected", [
    ("1", False),
    ("true", False),
    ("yes", False),
    ("on", False),
    ("0", True),
    ("", True),
])
def test_sandbox_enabled_flag(monkeypatch, value, expected):
    monkeypatch.setenv("GHOSTPROVIDER_NO_SANDBOX", value)
    assert _sandbox_enabled() is expected


def test_sandbox_enabled_default(monkeypatch):
    monkeypatch.delenv("GHOSTPROVIDER_NO_SANDBOX", raising=False)
    assert _sandbox_enabled() is True


def test_no_sandbox_runs_directly(monkeypatch):
    """With GHOSTPROVIDER_NO_SANDBOX=1 the command runs directly (no systemd-run)."""
    monkeypatch.setenv("GHOSTPROVIDER_NO_SANDBOX", "1")
    result = _run_sandboxed(["/bin/sh", "-c", "echo direct-ok"])
    assert result.returncode == 0
    assert "direct-ok" in result.stdout


def test_sandbox_propagates_exit_code():
    """A failing command reports its real exit code (sandboxed or direct)."""
    result = _run_sandboxed(["/bin/sh", "-c", "exit 7"])
    assert result.returncode == 7


def test_strip_systemd_status():
    err = (
        "Running as unit: ghost-build-test.service\n"
        "real error line\n"
        "          Finished with result: exit-code\n"
        "Main processes terminated with: code=exited, status=1/FAILURE\n"
    )
    cleaned = _strip_systemd_status(err)
    assert "Running as unit" not in cleaned
    assert "Finished with result" not in cleaned
    assert "real error line" in cleaned


@pytest.mark.skipif(
    shutil.which("systemd-run") is None,
    reason="systemd-run not available",
)
def test_sandbox_runs_under_systemd_run():
    """When systemd-run is present, the command actually runs inside the sandbox."""
    result = _run_sandboxed(["/bin/sh", "-c", "echo sandboxed-ok"])
    assert result.returncode == 0
    assert "sandboxed-ok" in result.stdout
