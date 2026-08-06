"""systemd service actions — start, stop, restart, remove, wait."""

import os
import re
import shutil
import socket
import subprocess
import time

from demo_ghostprovider.services.units import _extract_working_dir, _read_unit_file


def _exec_systemd_action(action: str, unit_name: str) -> str:
    """Execute a systemd action on a unit."""
    # Validate unit name to prevent injection
    if not re.match(r'^[A-Za-z0-9_\-.]+$', unit_name):
        return f"Invalid unit name: {unit_name}"
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


def remove_service(name: str) -> str:
    """Remove a demo_ghostprovider service: stop, disable, delete unit file, clean up all artifacts."""
    from demo_ghostprovider.hoster.systemd import _validate_unit_name
    from demo_ghostprovider.state import unregister as _unregister_state, get_clone_path

    if not _validate_unit_name(name):
        return "remove failed: invalid service name"

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
    # Check user systemd dir first (demo_ghostprovider always uses user services)
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
