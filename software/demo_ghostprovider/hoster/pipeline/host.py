"""Deployment orchestration — run strategies, verify, and register a service."""

import os
import shutil
from pathlib import Path
from typing import Callable

from demo_ghostprovider.hoster._helpers import find_free_port
from demo_ghostprovider.hoster.models import HostResult, RepoAnalysis
from demo_ghostprovider.hoster.pipeline.analyze import ensure_cloned
from demo_ghostprovider.hoster.pipeline.finalize import (
    _finalize_temp_dir,
    _relocate_compiled_binary,
)
from demo_ghostprovider.hoster.scanners.project import _deep_analyze_project
from demo_ghostprovider.hoster.scoring import _can_host_verdict
from demo_ghostprovider.hoster.strategies import _strategy_priority, get_strategy
from demo_ghostprovider.hoster.systemd import _cleanup_strategy
from demo_ghostprovider.hoster.verify import verify_deployment
from demo_ghostprovider.state import register as _register_state


def host_project(analysis: RepoAnalysis, port: int = 0,
                 verify: bool = True, work_dir: str | None = None,
                 on_status: Callable[[str], None] | None = None) -> HostResult:
    """Run the project and return service names and URLs."""
    def _emit(msg: str) -> None:
        if on_status:
            on_status(msg)

    if analysis.clone_path is None:
        _emit("cloning repository...")
        try:
            ensure_cloned(analysis, work_dir=work_dir)
        except RuntimeError as e:
            result = HostResult()
            result.errors.append(str(e))
            return result
    if not analysis.clone_path:
        result = HostResult()
        result.errors.append("Cannot clone repository (check network connection)")
        return result

    if not analysis.deep_analysis:
        _deep_analyze_project(analysis)
        analysis.can_host, analysis.reason = _can_host_verdict(analysis)

    if port == 0:
        port = find_free_port()

    project_dir = Path(analysis.clone_path)
    repo_url = analysis.url

    build_cmd = ""
    start_cmd = ""

    strategies = _strategy_priority(analysis)
    strategy = strategies[0] if strategies else None

    cls = get_strategy(strategy) if strategy else None
    if cls is None:
        raise RuntimeError("No hosting strategy available for this project")
    strategy_list = [(strategy, cls)]

    errors: list[str] = []
    for name, cls in strategy_list:
        _emit(f"trying {name} strategy...")
        strategy_result = HostResult()
        should_cleanup = False
        try:
            service_name = cls().host(
                project_dir, port, analysis.name,
                build_cmd=build_cmd, start_cmd=start_cmd,
            )
            strategy_result.service_names = [service_name]
            strategy_result.urls = [f"http://localhost:{port}"]
            if verify:
                strategy_result = verify_deployment(strategy_result)
            if strategy_result.healthy or (strategy_result.urls and strategy_result.service_names):
                _register_state(service_name, str(project_dir), repo_url)
                if work_dir:
                    _finalize_temp_dir(analysis, service_name, permanent_base=os.path.abspath(os.path.expanduser(work_dir)), on_status=on_status)
                elif name in ("Go", "Rust"):
                    _relocate_compiled_binary(analysis, service_name, name, project_dir, on_status)
                else:
                    managed_base = os.path.expanduser("~/.local/share/demo-ghostprovider/services")
                    _finalize_temp_dir(analysis, service_name, permanent_base=managed_base, on_status=on_status)
                return strategy_result
            should_cleanup = True
            msg = strategy_result.errors[0] if strategy_result.errors else "service started but health check failed"
            errors.append(f"[{name}] {msg}")
        except RuntimeError as e:
            should_cleanup = True
            errors.append(f"[{name}] {e}")
        except Exception as e:
            should_cleanup = True
            errors.append(f"[{name}] unexpected error: {e}")
        finally:
            if should_cleanup:
                _cleanup_strategy(strategy_result)

    # All strategies failed — clean up temp dir if we created one
    if analysis._temp_base:
        try:
            shutil.rmtree(analysis._temp_base, ignore_errors=True)
        except OSError:
            pass
        analysis._temp_base = None

    raise RuntimeError("All strategies failed:\n" + "\n".join(errors))
