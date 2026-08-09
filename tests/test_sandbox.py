"""Tests for the build sandbox helpers."""

import os
import shutil

import pytest
from demo_ghostprovider.hoster._helpers import (
    _cache_env,
    _rmtree,
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


def test_cache_env_empty_without_project_dir():
    """Without a project dir no cache redirection is applied."""
    assert _cache_env(None) == {}


def test_cache_env_redirects_into_project():
    """Tool caches point under <project>/.ghost-cache/<tool>."""
    env = _cache_env("/proj")
    assert env["XDG_CACHE_HOME"] == "/proj/.ghost-cache/xdg"
    assert env["npm_config_cache"] == "/proj/.ghost-cache/npm"
    assert env["CARGO_HOME"] == "/proj/.ghost-cache/cargo"
    assert env["GOCACHE"] == "/proj/.ghost-cache/go"
    assert env["GOMODCACHE"] == "/proj/.ghost-cache/go-mod"
    assert env["TMPDIR"] == "/proj/.ghost-cache/tmp"


def test_cache_redirect_applied_in_run(monkeypatch, tmp_path):
    """The sandboxed command sees the redirected cache vars."""
    proj = tmp_path / "app"
    proj.mkdir()
    monkeypatch.setenv("GHOSTPROVIDER_NO_SANDBOX", "1")
    result = _run_sandboxed(
        ["/bin/sh", "-c", "echo $XDG_CACHE_HOME; echo $GOCACHE"],
        cwd=str(proj),
    )
    assert result.returncode == 0
    assert str(proj / ".ghost-cache" / "xdg") in result.stdout
    assert str(proj / ".ghost-cache" / "go") in result.stdout


def test_cache_dirs_precreated_before_run(monkeypatch, tmp_path):
    """Cache dirs (e.g. GOTMPDIR) exist before the command runs, so tools
    like `go build` that require a pre-existing tmp dir do not fail."""
    proj = tmp_path / "app"
    proj.mkdir()
    monkeypatch.setenv("GHOSTPROVIDER_NO_SANDBOX", "1")
    result = _run_sandboxed(
        ["/bin/sh", "-c", "test -d \"$GOTMPDIR\" && test -d \"$GOCACHE\" && echo dirs-ok"],
        cwd=str(proj),
    )
    assert result.returncode == 0
    assert "dirs-ok" in result.stdout


def test_rmtree_removes_readonly_files(tmp_path):
    """_rmtree removes trees containing read-only files/dirs (Go module
    cache writes 0444 files / 0555 dirs), which plain shutil.rmtree leaves
    behind."""
    tree = tmp_path / "cache"
    sub = tree / "go-mod" / "pkg"
    sub.mkdir(parents=True)
    ro_file = sub / "module.zip"
    ro_file.write_text("data")
    os.chmod(ro_file, 0o444)
    os.chmod(sub, 0o555)
    os.chmod(tree / "go-mod", 0o555)

    _rmtree(str(tree))

    assert not tree.exists()


def test_ghost_cache_persists_after_run(monkeypatch, tmp_path):
    """The .ghost-cache directory survives a run so repeat deployments reuse
    downloaded tool artifacts (Go modules, npm/pip caches)."""
    proj = tmp_path / "app"
    proj.mkdir()
    cache_dir = proj / ".ghost-cache"
    (cache_dir / "dummy").mkdir(parents=True)
    monkeypatch.setenv("GHOSTPROVIDER_NO_SANDBOX", "1")
    result = _run_sandboxed(["/bin/sh", "-c", "true"], cwd=str(proj))
    assert result.returncode == 0
    assert cache_dir.is_dir()


def test_sandbox_cmd_redirects_caches(monkeypatch, tmp_path):
    """systemd-run gets cache-redirection setenv and no ~/.cache write path."""
    from demo_ghostprovider.hoster import _helpers as helpers
    proj = tmp_path / "app"
    proj.mkdir()
    captured: dict = {}

    class FakeProc:
        returncode = 0
        stdout = "ok"
        stderr = ""

    def fake_run(args, **kwargs):
        captured["args"] = args
        return FakeProc()

    monkeypatch.setattr(helpers, "_sandbox_enabled", lambda: True)
    monkeypatch.setattr(helpers.shutil, "which", lambda _name: "/usr/bin/systemd-run")
    monkeypatch.setattr(helpers.subprocess, "run", fake_run)

    _run_sandboxed(["/bin/sh", "-c", "echo ok"], cwd=str(proj))

    args = captured["args"]
    assert f"--setenv=XDG_CACHE_HOME={proj}/.ghost-cache/xdg" in args
    assert f"--setenv=GOCACHE={proj}/.ghost-cache/go" in args
    rw_props = [a for a in args if a.startswith("--property=ReadWritePaths=")]
    assert rw_props == []


@pytest.mark.skipif(
    shutil.which("systemd-run") is None,
    reason="systemd-run not available",
)
def test_sandbox_runs_under_systemd_run():
    """When systemd-run is present, the command actually runs inside the sandbox."""
    result = _run_sandboxed(["/bin/sh", "-c", "echo sandboxed-ok"])
    assert result.returncode == 0
    assert "sandboxed-ok" in result.stdout
