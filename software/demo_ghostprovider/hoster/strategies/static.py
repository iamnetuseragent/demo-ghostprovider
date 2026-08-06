"""Static site hosting strategy."""

import subprocess
import uuid
from pathlib import Path

from demo_ghostprovider.hoster.strategies.base import Hoster
from demo_ghostprovider.hoster.strategies.registry import register
from demo_ghostprovider.hoster.systemd import (
    _create_systemd_service,
    _check_service_started,
    _get_service_logs,
)


@register
class StaticHoster(Hoster):
    """Host a static site using systemd and Python HTTP server."""

    name = "Static"
    aliases = ("static", "static html")
    description = "Static HTML sites served with python http.server"
    priority = 60

    def host(self, project_dir: Path, port: int, repo_url: str = "",
             build_cmd: str = "", start_cmd: str = "", **kwargs) -> str:
        service_name = f"ghost-static-{uuid.uuid4().hex[:8]}"

        # Use Python's built-in HTTP server for static files
        exec_start = f"python3 -m http.server {port} --directory \"{project_dir}\""

        # Create systemd service
        _create_systemd_service(
            service_name=service_name,
            working_dir=str(project_dir),
            exec_start=exec_start,
            description=f"GhostProvider: {repo_url}",
            port=port,
        )

        # Start the service
        try:
            try:
                r = subprocess.run(
                    ["systemctl", "--user", "start", service_name],
                    capture_output=True, text=True,
                )
                if r.returncode != 0:
                    raise RuntimeError(f"Failed to start service: {r.stderr}")
            except (subprocess.TimeoutExpired, FileNotFoundError) as e:
                raise RuntimeError(f"Failed to start service: {e}")

            if not _check_service_started(service_name):
                logs = _get_service_logs(service_name, 10)
                raise RuntimeError(f"Service crashed immediately after start: {logs[:200]}")
        except RuntimeError:
            from demo_ghostprovider.hoster.systemd import _cleanup_strategy
            from demo_ghostprovider.hoster.models import HostResult
            _cleanup_strategy(HostResult(service_names=[service_name]))
            raise

        return service_name
