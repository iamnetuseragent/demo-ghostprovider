"""Deploy sequence for the three curated demo services."""

import os
import re
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

from demo_ghostprovider.hoster._helpers import _run_build_cmd, find_free_port
from demo_ghostprovider.hoster.git import _git_clone
from demo_ghostprovider.hoster.models import HostResult, RepoAnalysis
from demo_ghostprovider.hoster.recipes import DemoRecipe
from demo_ghostprovider.hoster.secrets import write_env_file
from demo_ghostprovider.hoster.systemd import (
    _check_service_started,
    _cleanup_strategy,
    _create_systemd_service,
    _get_service_logs,
)
from demo_ghostprovider.paths import SERVICES_DIR
from demo_ghostprovider.state import register as _register_state


def _safe_dirname(name: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]", "_", name)


def _clone_repo(analysis: RepoAnalysis, work_dir: str | None = None) -> bool:
    """Clone the service repository into a permanent working directory."""
    if work_dir:
        base = os.path.abspath(os.path.expanduser(work_dir))
    else:
        base = str(SERVICES_DIR)
    os.makedirs(base, exist_ok=True)

    clone_dir = os.path.join(base, _safe_dirname(analysis.name))
    if not os.path.isdir(os.path.join(clone_dir, ".git")):
        if os.path.isdir(clone_dir):
            shutil.rmtree(clone_dir, ignore_errors=True)
        git_url = f"https://github.com/{analysis.owner}/{analysis.name}.git"
        if not _git_clone(git_url, clone_dir):
            return False

    analysis.clone_path = clone_dir
    return True


def _resolve_start(recipe: DemoRecipe, project_dir: Path, port: int) -> str:
    """Fill the recipe start command placeholders with concrete paths."""
    cmd = recipe.start_cmd

    if recipe.language == "Rust":
        bin_name = "app"
        cargo = project_dir / "Cargo.toml"
        if cargo.is_file():
            content = cargo.read_text(errors="replace")
            m = re.search(r'\[\[bin\]\][^[]*name\s*=\s*"(.+?)"', content, re.DOTALL)
            if m:
                bin_name = m.group(1)
            else:
                m = re.search(r'\[package\][^[]*name\s*=\s*"(.+?)"', content, re.DOTALL)
                if m:
                    bin_name = m.group(1)
        candidate = project_dir / "target" / "release" / bin_name
        if not candidate.is_file():
            candidate = project_dir / "target" / "release" / project_dir.name
        cmd = cmd.replace("{bin}", str(candidate))
    elif recipe.language == "Go":
        cmd = cmd.replace("{bin}", str(project_dir / "ghost-server"))

    cmd = cmd.replace("{venv}", str(project_dir / ".venv" / "bin" / "python"))
    cmd = cmd.replace("{port}", str(port))
    return cmd


def _stop_existing(service_name: str) -> None:
    """Remove a previously deployed unit so it can be redeployed cleanly."""
    unit = os.path.expanduser(f"~/.config/systemd/user/{service_name}.service")
    if os.path.isfile(unit):
        _cleanup_strategy(HostResult(service_names=[service_name]))


def deploy_service(analysis: RepoAnalysis, recipe: DemoRecipe,
                   work_dir: str | None = None,
                   on_status: Callable[[str], None] | None = None) -> HostResult:
    """Build, install, and start one curated demo service."""
    def _emit(msg: str) -> None:
        if on_status:
            on_status(msg)

    result = HostResult()

    _emit("cloning repository...")
    if not _clone_repo(analysis, work_dir=work_dir):
        result.errors.append("git clone failed after retries (check network connection)")
        return result

    project_dir = Path(analysis.clone_path)

    try:
        for step in recipe.build_steps:
            _emit(f"build: {step[:100]}")
            r = _run_build_cmd(step, project_dir)
            if r.returncode != 0:
                raise RuntimeError(
                    f"Build step failed (exit {r.returncode}):\n{r.stderr[:300]}"
                )

        port = recipe.port or find_free_port()
        exec_start = _resolve_start(recipe, project_dir, port)
        _emit(f"installing systemd unit {recipe.service_name}...")
        _stop_existing(recipe.service_name)
        env_file = write_env_file(recipe.service_name, recipe.env) if recipe.env else None
        _create_systemd_service(
            service_name=recipe.service_name,
            working_dir=str(project_dir),
            exec_start=exec_start,
            description=f"demo: {recipe.description}",
            port=port,
            env_file=env_file,
        )

        _emit("starting service...")
        subprocess.run(
            ["systemctl", "--user", "start", recipe.service_name],
            capture_output=True, text=True, timeout=30, check=False,
        )
        if not _check_service_started(recipe.service_name):
            logs = _get_service_logs(recipe.service_name, 20)
            raise RuntimeError(f"Service crashed immediately after start:\n{logs[:300]}")

        _register_state(recipe.service_name, str(project_dir), analysis.url)
        result.service_names = [recipe.service_name]
        result.urls = [f"http://localhost:{port}"]
        return result
    except RuntimeError as e:
        result.errors.append(str(e))
        _cleanup_strategy(result)
        return result
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        result.errors.append(f"systemd error: {e}")
        _cleanup_strategy(result)
        return result


def cleanup(analysis: RepoAnalysis) -> None:
    """Remove the clone directory after an aborted deploy."""
    if analysis.clone_path and os.path.isdir(analysis.clone_path):
        shutil.rmtree(analysis.clone_path, ignore_errors=True)
