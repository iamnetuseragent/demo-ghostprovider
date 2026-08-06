"""Rust hosting strategy."""

import re
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
class RustHoster(Hoster):
    """Host a Rust project using systemd."""

    name = "Rust"
    aliases = ("rust",)
    description = "Rust applications built with cargo"
    priority = 50

    def host(self, project_dir: Path, port: int, repo_url: str = "",
             build_cmd: str = "", start_cmd: str = "", **kwargs) -> str:
        service_name = f"ghost-rust-{uuid.uuid4().hex[:8]}"

        bin_name = _detect_rust_binary(project_dir) or "app"

        # Build the Rust binary
        try:
            r = subprocess.run(
                ["cargo", "build", "--release", "--bin", bin_name],
                capture_output=True, text=True,
                cwd=str(project_dir),
            )
            if r.returncode != 0:
                # Try building without specifying binary
                r = subprocess.run(
                    ["cargo", "build", "--release"],
                    capture_output=True, text=True,
                    cwd=str(project_dir),
                )
                if r.returncode != 0:
                    raise RuntimeError(f"Cargo build failed: {r.stderr}")
        except FileNotFoundError:
            raise RuntimeError("Cargo/Rust compiler not found")

        binary_path = project_dir / "target" / "release" / bin_name
        if not binary_path.exists():
            # Try the project name
            binary_path = project_dir / "target" / "release" / project_dir.name

        if not binary_path.exists():
            raise RuntimeError(f"Built binary not found at {binary_path}")

        # Create systemd service
        _create_systemd_service(
            service_name=service_name,
            working_dir=str(project_dir),
            exec_start=f"{binary_path} --port {port}",
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


def _detect_rust_binary(project_dir: Path) -> str | None:
    """Extract binary/package name from Cargo.toml."""
    cargo_toml = project_dir / "Cargo.toml"
    if not cargo_toml.exists():
        return None
    try:
        content = cargo_toml.read_text()
        m = re.search(r'\[\[bin\]\][^[]*name\s*=\s*"(.+?)"', content, re.DOTALL)
        if m:
            return m.group(1)
        m = re.search(r'\[package\][^[]*name\s*=\s*"(.+?)"', content, re.DOTALL)
        if m:
            return m.group(1)
    except OSError:
        pass
    return None
