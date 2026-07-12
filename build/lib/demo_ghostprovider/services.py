"""systemd service discovery and management for ghostprovider."""

import json
import os
import re
import socket
import subprocess
import time
from dataclasses import dataclass


@dataclass
class ServiceInfo:
    name: str
    unit_name: str
    status: str
    state: str
    description: str = ""
    ports: list[int] = None
    exec_start: str = ""
    urls: list[str] = None

    def __post_init__(self):
        if self.ports is None:
            self.ports = []
        if self.urls is None:
            self.urls = []


def _is_systemd_service(unit_name: str) -> bool:
    """Check if a systemd unit is a service (not socket, timer, etc)."""
    try:
        r = subprocess.run(
            ["systemctl", "--user", "is-enabled", "--quiet", unit_name],
            capture_output=True, timeout=5,
        )
        # Also check if it's a .service unit
        r2 = subprocess.run(
            ["systemctl", "--user", "list-unit-files", f"{unit_name}.service"],
            capture_output=True, text=True, timeout=5,
        )
        return unit_name in r2.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _get_unit_property(unit_name: str, prop: str) -> str:
    """Get a systemd unit property value."""
    try:
        r = subprocess.run(
            ["systemctl", "--user", "show", unit_name, f"--property={prop}", "--value"],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0:
            return r.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return ""


def _get_unit_ports(unit_name: str) -> list[int]:
    """Extract listening ports from a systemd service."""
    ports: list[int] = []

    # Check socket units linked to this service
    try:
        r = subprocess.run(
            ["systemctl", "--user", "list-units", "--type=socket", "--state=running", "--plain", "--no-legend"],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0:
            for line in r.stdout.strip().split("\n"):
                parts = line.split()
                if len(parts) >= 1:
                    sock_name = parts[0].replace(".socket", "")
                    # Check if this socket is for our service
                    listen = _get_unit_property(parts[0], "ListenStream")
                    if listen and unit_name in _get_unit_property(parts[0], "WantedBy"):
                        # Extract port from ListenStream
                        m = re.search(r":(\d+)", listen)
                        if m:
                            ports.append(int(m.group(1)))
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    # Check ss for ports used by this service's main PID and all child PIDs
    try:
        main_pid = _get_unit_property(f"{unit_name}.service", "MainPID")
        if main_pid and main_pid != "0":
            # Collect main PID + all child PIDs from cgroup
            pids = {main_pid}
            try:
                cg = subprocess.run(
                    ["systemctl", "--user", "show", f"{unit_name}.service", "--property=ControlGroup", "--value"],
                    capture_output=True, text=True, timeout=5,
                )
                if cg.returncode == 0 and cg.stdout.strip():
                    cg_path = f"/sys/fs/cgroup{cg.stdout.strip()}"
                    procs = subprocess.run(
                        ["bash", "-c", f"cat {cg_path}/cgroup.procs 2>/dev/null"],
                        capture_output=True, text=True, timeout=5,
                    )
                    if procs.returncode == 0:
                        for pid in procs.stdout.strip().split("\n"):
                            if pid.strip():
                                pids.add(pid.strip())
            except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
                pass

            r = subprocess.run(
                ["ss", "-tlnp"],
                capture_output=True, text=True, timeout=5,
            )
            if r.returncode == 0:
                for line in r.stdout.strip().split("\n")[1:]:
                    for pid in pids:
                        if f"pid={pid}" in line:
                            parts = line.split()
                            if len(parts) >= 4:
                                addr_port = parts[3]
                                if ":" in addr_port:
                                    port_str = addr_port.rsplit(":", 1)[-1]
                                    if port_str.isdigit():
                                        port = int(port_str)
                                        if port not in ports:
                                            ports.append(port)
                            break
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    return ports


def list_services(all_services: bool = False) -> list[ServiceInfo]:
    """List ghostprovider-managed systemd services."""
    services: list[ServiceInfo] = []

    try:
        # Always list all ghost-prefixed services (running or stopped)
        cmd = ["systemctl", "--user", "list-units", "--type=service", "--plain", "--no-legend",
               "--all"]

        r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if r.returncode != 0:
            return services

        for line in r.stdout.strip().split("\n"):
            parts = line.split()
            if len(parts) < 4:
                continue

            unit_name = parts[0].replace(".service", "")

            # Only show ghostprovider-managed services (prefixed with ghost-)
            if not unit_name.startswith("ghost-"):
                continue

            status = parts[2] if len(parts) > 2 else "unknown"
            state = parts[3] if len(parts) > 3 else "unknown"

            # Get description
            desc = _get_unit_property(unit_name, "Description")

            # Get exec start command
            exec_start = _get_unit_property(unit_name, "ExecStart")

            # Get ports
            ports = _get_unit_ports(unit_name)

            # Build extra URLs for known multi-endpoint services
            urls: list[str] = []
            desc_lower = desc.lower()
            if "affine" in desc_lower:
                for port in ports:
                    if port > 0:
                        urls.append(f"http://localhost:{port}")
                        urls.append(f"http://localhost:{port}/admin")

            services.append(ServiceInfo(
                name=unit_name,
                unit_name=unit_name,
                status=status,
                state=state,
                description=desc,
                ports=ports,
                exec_start=exec_start,
                urls=urls,
            ))

    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    return services


def service_urls(service: ServiceInfo) -> list[str]:
    """Get HTTP URLs for a service.

    Returns pre-computed ``service.urls`` when available (multi-endpoint
    services like AFFiNE), otherwise falls back to port-based generation.
    """
    if service.urls:
        return list(service.urls)
    urls: list[str] = []
    for port in service.ports:
        if port > 0:
            urls.append(f"http://localhost:{port}")
    return urls


def _exec_systemd_action(action: str, unit_name: str) -> str:
    """Execute a systemd action on a unit."""
    try:
        result = subprocess.run(
            ["systemctl", "--user", action, unit_name],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            return f"Service '{unit_name}' {action}ed successfully"
        error = result.stderr.strip() or "unknown error"
        return f"Failed to {action} '{unit_name}': {error}"
    except subprocess.TimeoutExpired:
        return f"Timeout during '{action}' for service '{unit_name}'"
    except FileNotFoundError:
        return "systemctl is not available on this system"


def stop_service(name: str) -> str:
    return _exec_systemd_action("stop", name)


def start_service(name: str) -> str:
    return _exec_systemd_action("start", name)


def restart_service(name: str) -> str:
    return _exec_systemd_action("restart", name)


def _read_unit_file(unit_file: str) -> dict[str, str]:
    """Parse a systemd unit file and return key=value pairs from [Service] section."""
    props: dict[str, str] = {}
    try:
        with open(unit_file, encoding="utf-8") as f:
            in_service = False
            for line in f:
                line = line.strip()
                if line == "[Service]":
                    in_service = True
                    continue
                if line.startswith("[") and line.endswith("]"):
                    in_service = False
                    continue
                if in_service and "=" in line:
                    key, _, value = line.partition("=")
                    props[key.strip()] = value.strip()
    except OSError:
        pass
    return props


def _extract_working_dir(unit_file: str) -> str | None:
    """Extract WorkingDirectory from a unit file."""
    props = _read_unit_file(unit_file)
    wd = props.get("WorkingDirectory", "")
    if wd:
        return os.path.expanduser(wd)
    return None


def _is_affine_service(name: str, working_dir: str | None, exec_start: str) -> bool:
    """Detect if a service is an AFFiNE deployment."""
    # Check unit file description
    unit_file = os.path.expanduser(f"~/.config/systemd/user/{name}.service")
    if os.path.isfile(unit_file):
        try:
            with open(unit_file) as f:
                content = f.read()
                if "AFFiNE" in content:
                    return True
        except OSError:
            pass
    # Check working directory path
    if working_dir and "affine" in working_dir.lower():
        return True
    # Check clone path from state
    from demo_ghostprovider.state import get_clone_path
    cp = get_clone_path(name)
    if cp and "affine" in cp.lower():
        return True
    return False


def _cleanup_affine_db() -> str | None:
    """Drop AFFiNE PostgreSQL user and database. Returns summary string."""
    db_user = "affine"
    db_name = "affine"
    results = []

    # Try password auth first, fall back to sudo
    for cmd_base in (
        ["psql", "-U", "postgres", "-h", "localhost"],
        ["sudo", "-u", "postgres", "psql", "-h", "localhost"],
    ):
        try:
            r = subprocess.run(
                cmd_base + ["-c", f"DROP DATABASE IF EXISTS {db_name};"],
                capture_output=True, text=True, timeout=10,
            )
            if r.returncode == 0:
                results.append("database dropped")
                subprocess.run(
                    cmd_base + ["-c", f"DROP USER IF EXISTS {db_user};"],
                    capture_output=True, text=True, timeout=10,
                )
                results.append("user dropped")
                break
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue

    # Clean up AFFiNE data directory
    affine_home = os.path.expanduser("~/.affine")
    if os.path.isdir(affine_home):
        try:
            shutil.rmtree(affine_home, ignore_errors=True)
            results.append("~/.affine removed")
        except Exception:
            pass

    # Clean up cargo cache for AFFiNE
    cargo_cache = os.path.expanduser("~/.cache/affine-cargo-target")
    if os.path.isdir(cargo_cache):
        try:
            shutil.rmtree(cargo_cache, ignore_errors=True)
            results.append("cargo cache removed")
        except Exception:
            pass

    return ", ".join(results) if results else None


def remove_service(name: str) -> str:
    """Remove a ghostprovider service: stop, disable, delete unit file, clean up all artifacts."""
    import shutil
    from demo_ghostprovider.state import unregister as _unregister_state, get_clone_path

    cleanup_log: list[str] = []

    # 1. Stop the service
    stop_result = _exec_systemd_action("stop", name)
    if "Failed" in stop_result:
        cleanup_log.append("stop failed (may already be stopped)")

    # 2. Disable the service
    try:
        r = subprocess.run(
            ["systemctl", "--user", "disable", name],
            capture_output=True, text=True, timeout=10,
        )
        if r.returncode == 0:
            cleanup_log.append("disabled")
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    # 3. Reset failed state (needed for services that crashed)
    try:
        subprocess.run(
            ["systemctl", "--user", "reset-failed", name],
            capture_output=True, text=True, timeout=10,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    # 4. Read unit file BEFORE deleting (need WorkingDirectory and ExecStart)
    # Check user systemd dir first (ghostprovider always uses user services)
    unit_file = os.path.expanduser(f"~/.config/systemd/user/{name}.service")
    if not os.path.isfile(unit_file):
        unit_file = f"/etc/systemd/system/{name}.service"

    working_dir = None
    exec_start = ""
    if os.path.isfile(unit_file):
        working_dir = _extract_working_dir(unit_file)
        props = _read_unit_file(unit_file)
        exec_start = props.get("ExecStart", "")

        # Delete the unit file
        try:
            os.remove(unit_file)
            cleanup_log.append("unit file deleted")
        except OSError:
            cleanup_log.append("failed to delete unit file")

    # 5. Reload systemd daemon
    try:
        subprocess.run(
            ["systemctl", "--user", "daemon-reload"],
            capture_output=True, text=True, timeout=10,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    # 6. Clean up clone/working directory
    # First try state.json
    clone_path = get_clone_path(name)
    # Fallback to WorkingDirectory from unit file
    if not clone_path and working_dir:
        clone_path = working_dir

    if clone_path and os.path.isdir(clone_path):
        try:
            shutil.rmtree(clone_path, ignore_errors=True)
            cleanup_log.append(f"directory removed: {clone_path}")
        except Exception:
            cleanup_log.append(f"failed to remove: {clone_path}")

    # 6b. Clean up AFFiNE database if this is an AFFiNE service
    if _is_affine_service(name, working_dir, exec_start):
        db_result = _cleanup_affine_db()
        if db_result:
            cleanup_log.append(db_result)

    # 7. Unregister from state
    _unregister_state(name)

    # 8. Kill any lingering processes on the freed ports
    ports = _get_service_ports(name)
    if ports:
        for port in ports:
            try:
                subprocess.run(
                    ["fuser", "-k", f"{port}/tcp"],
                    capture_output=True, timeout=5,
                )
            except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
                pass

    # 9. Verify ports are freed
    ports_after = _verify_ports_freed(ports)

    status = "removed successfully"
    if cleanup_log:
        status += f" ({', '.join(cleanup_log)})"
    return f"Service '{name}' {status}{ports_after}"


def _get_service_ports(name: str) -> list[int]:
    """Get ports currently used by a service."""
    ports: list[int] = []
    try:
        r = subprocess.run(
            ["systemctl", "--user", "show", name, "--property=ExecMainPID", "--value"],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0 and r.stdout.strip() != "0":
            pid = r.stdout.strip()
            r2 = subprocess.run(
                ["ss", "-tlnp", f"pid={pid}"],
                capture_output=True, text=True, timeout=5,
            )
            if r2.returncode == 0:
                for line in r2.stdout.strip().split("\n")[1:]:
                    parts = line.split()
                    if len(parts) >= 4:
                        port_str = parts[3].rsplit(":", 1)[-1]
                        if port_str.isdigit():
                            ports.append(int(port_str))
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return ports


def _verify_ports_freed(ports: list[int]) -> str:
    """Verify that previously occupied ports are now free."""
    if not ports:
        return ""
    time.sleep(0.5)
    still_used: list[int] = []
    for port in ports:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                if s.connect_ex(("127.0.0.1", port)) == 0:
                    still_used.append(port)
        except (socket.error, OSError):
            pass
    if still_used:
        return f" (warning: ports {still_used} still in use)"
    return ""


def wait_service_ready(name: str, timeout: int = 60) -> bool:
    """Wait until a systemd service is fully ready (active or port-responsive)."""
    MIN_VISIBLE = 3.0
    deadline = time.time() + timeout
    start = time.time()

    while time.time() < deadline:
        try:
            result = subprocess.run(
                ["systemctl", "--user", "is-active", name],
                capture_output=True, text=True, timeout=5,
            )
            status = result.stdout.strip()

            if status == "active":
                # Check if ports are responsive
                ports = _get_service_ports(name)
                if ports:
                    for port in ports:
                        try:
                            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                                s.settimeout(2)
                                if s.connect_ex(("127.0.0.1", port)) == 0:
                                    elapsed = time.time() - start
                                    if elapsed >= MIN_VISIBLE:
                                        return True
                        except OSError:
                            pass
                else:
                    elapsed = time.time() - start
                    if elapsed >= MIN_VISIBLE:
                        return True

            elif status in ("failed", "inactive"):
                return False

        except (subprocess.TimeoutExpired, FileNotFoundError):
            return False

        time.sleep(1)
    return False


def get_service_unit_content(unit_name: str) -> str | None:
    """Read the systemd unit file content."""
    # Check system unit files first
    paths = [
        f"/etc/systemd/system/{unit_name}.service",
        os.path.expanduser(f"~/.config/systemd/user/{unit_name}.service"),
    ]

    for path in paths:
        if os.path.isfile(path):
            try:
                with open(path, encoding="utf-8") as f:
                    return f.read()
            except OSError:
                pass
    return None


def find_free_port(start: int = 0, max_tries: int = 50) -> int:
    """Find the first available port."""
    import random
    if start == 0:
        start = random.randint(8000, 30000)
    for port in range(start, start + max_tries):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port
    raise RuntimeError(f"No free port found in range {start}-{start + max_tries}")
