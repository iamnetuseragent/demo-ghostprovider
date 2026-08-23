"""systemd service actions — start, stop, restart, remove, wait."""

import logging
import os
import re
import socket
import subprocess
import time

from demo_ghostprovider.services.units import _extract_working_dir

logger = logging.getLogger("demo_ghostprovider.services.actions")


def _exec_systemd_action(action: str, unit_name: str) -> str:
    """Execute a systemd action on a unit."""
    # Validate unit name to prevent injection
    if not re.match(r'^[A-Za-z0-9_\-.]+$', unit_name):
        return f"Invalid unit name: {unit_name}"
    try:
        result = subprocess.run(
            ["systemctl", "--user", action, unit_name],
            capture_output=True, text=True, timeout=30,
            check=False,
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
            check=False,
        )
        if r.returncode == 0 and r.stdout.strip() != "0":
            pid = r.stdout.strip()
            r2 = subprocess.run(
                ["ss", "-tlnp", f"pid={pid}"],
                capture_output=True, text=True, timeout=5,
                check=False,
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
    """Verify that previously occupied ports are now free (IPv4 + IPv6)."""
    if not ports:
        return ""
    time.sleep(0.5)
    still_used: list[int] = []
    for port in ports:
        in_use = False
        for host in ("127.0.0.1", "::1"):
            try:
                family = socket.AF_INET if host == "127.0.0.1" else socket.AF_INET6
                with socket.socket(family, socket.SOCK_STREAM) as s:
                    s.settimeout(1)
                    if s.connect_ex((host, port)) == 0:
                        in_use = True
                        break
            except OSError:
                pass
        if in_use:
            still_used.append(port)
    if still_used:
        return f" (warning: ports {still_used} still in use)"
    return ""


def remove_service(name: str) -> str:
    """Remove a demo_ghostprovider service: stop, disable, delete unit file, clean up all artifacts."""
    from demo_ghostprovider.hoster._helpers import _rmtree
    from demo_ghostprovider.hoster.systemd import _validate_unit_name
    from demo_ghostprovider.state import get_clone_path, get_unit_name
    from demo_ghostprovider.state import unregister as _unregister_state

    if not _validate_unit_name(name):
        return "remove failed: invalid service name"

    # Resolve the actual systemd unit name.  The state may store a different
    # unit_name (e.g. the TUI friendly name "demo-vert" may map to unit
    # "ghost-js-6a53ddda").  Fall back to the service key itself.
    unit_name = get_unit_name(name) or name

    cleanup_log: list[str] = []

    # Capture ports while unit is still loaded
    ports = _get_service_ports(unit_name)

    # Detect if this is a container service BEFORE stopping
    is_container = _is_container_unit(unit_name)

    # 1. Stop the service
    stop_result = _exec_systemd_action("stop", unit_name)
    if "Failed" in stop_result:
        cleanup_log.append("stop failed (may already be stopped)")

    # 1b. If container service, also stop and remove the podman container
    if is_container:
        _cleanup_container(unit_name, cleanup_log)

    # 2. Disable the service
    try:
        r = subprocess.run(
            ["systemctl", "--user", "disable", unit_name],
            capture_output=True, text=True, timeout=10,
            check=False,
        )
        if r.returncode == 0:
            cleanup_log.append("disabled")
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    # 3. Reset failed state (needed for services that crashed)
    try:
        subprocess.run(
            ["systemctl", "--user", "reset-failed", unit_name],
            capture_output=True, text=True, timeout=10,
            check=False,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    # 4. Read unit file BEFORE deleting (need WorkingDirectory and ExecStart)
    # Try unit_name first (may differ from the state key), then fall back to name.
    unit_file = _find_unit_file(unit_name, name)
    is_system_unit = unit_file.startswith("/etc/systemd/system/")

    working_dir = None
    if os.path.isfile(unit_file):
        working_dir = _extract_working_dir(unit_file)

        # Delete the unit file
        try:
            os.remove(unit_file)
            cleanup_log.append("unit file deleted")
        except OSError:
            cleanup_log.append("failed to delete unit file")

    # 4b. Remove Quadlet .container file if present (try both unit_name and name)
    for candidate in (unit_name, name):
        container_file = os.path.expanduser(f"~/.config/containers/systemd/{candidate}.container")
        if os.path.isfile(container_file):
            try:
                os.remove(container_file)
                cleanup_log.append("container definition removed")
            except OSError:
                cleanup_log.append("failed to remove container definition")
            break

    # 5. Reload systemd daemon (user + system if needed)
    _reload_systemd(is_system_unit)

    # 6. Clean up clone/working directory and .old backups
    clone_path = get_clone_path(name)
    if not clone_path and working_dir:
        clone_path = working_dir

    if clone_path:
        _cleanup_clone_dir(clone_path, cleanup_log)

    # 7. Unregister from state (AFTER directory cleanup to preserve state on failure)
    _unregister_state(name)

    # 7b. Remove the per-service secrets EnvironmentFile
    from demo_ghostprovider.hoster.secrets import remove_env_file
    remove_env_file(name)

    # 8. Kill any lingering processes on the freed ports
    if ports:
        for port in ports:
            try:
                subprocess.run(
                    ["fuser", "-k", f"{port}/tcp"],
                    capture_output=True, timeout=5,
                    check=False,
                )
            except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
                pass

    # 9. Verify ports are freed (IPv4 + IPv6)
    ports_after = _verify_ports_freed(ports)

    status = "removed successfully"
    if cleanup_log:
        status += f" ({', '.join(cleanup_log)})"
    return f"Service '{name}' {status}{ports_after}"


def _find_unit_file(unit_name: str, fallback_name: str = "") -> str:
    """Locate the systemd unit file on disk.

    Tries ``unit_name`` first (from state.json ``unit_name`` field), then
    ``fallback_name`` (the state key the TUI passed in), then scans user
    and system directories.  Returns the path if found, empty string otherwise.
    """
    candidates = [unit_name]
    if fallback_name and fallback_name != unit_name:
        candidates.append(fallback_name)

    for cname in candidates:
        user_path = os.path.expanduser(f"~/.config/systemd/user/{cname}.service")
        if os.path.isfile(user_path):
            return user_path
        sys_path = f"/etc/systemd/system/{cname}.service"
        if os.path.isfile(sys_path):
            return sys_path

    # Last resort: scan user dir for any unit referencing the fallback name
    user_dir = os.path.expanduser("~/.config/systemd/user")
    if os.path.isdir(user_dir):
        for fname in os.listdir(user_dir):
            if not fname.endswith(".service"):
                continue
            full = os.path.join(user_dir, fname)
            try:
                with open(full) as f:
                    content = f.read(4096)
                if fallback_name and fallback_name in content:
                    return full
            except OSError:
                pass

    return ""


def _is_container_unit(name: str) -> bool:
    """Check if a systemd unit is a container service (podman/docker/quadlet)."""
    try:
        r = subprocess.run(
            ["systemctl", "--user", "show", f"{name}.service",
             "--property=ExecStart", "--value"],
            capture_output=True, text=True, timeout=5,
            check=False,
        )
        if r.returncode == 0:
            exec_start = r.stdout.strip()
            desc_r = subprocess.run(
                ["systemctl", "--user", "show", f"{name}.service",
                 "--property=Description", "--value"],
                capture_output=True, text=True, timeout=5,
                check=False,
            )
            desc = desc_r.stdout.strip() if desc_r.returncode == 0 else ""
            haystack = f"{exec_start} {desc}".lower()
            container_runtimes = (
                "podman", "docker", "nerdctl", "containerd", "conmon",
                "systemd-nspawn", "machinectl", "crun", "runc",
            )
            return any(rt in haystack for rt in container_runtimes)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return False


def _cleanup_container(name: str, cleanup_log: list[str]) -> None:
    """Stop and remove a podman container, its volumes, and image if orphaned."""
    container_names = [f"systemd-{name}", name]

    for cname in container_names:
        try:
            r = subprocess.run(
                ["podman", "container", "exists", cname],
                capture_output=True, timeout=5, check=False,
            )
            if r.returncode != 0:
                continue
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return  # podman not available

        try:
            subprocess.run(
                ["podman", "stop", cname],
                capture_output=True, timeout=15, check=False,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass

        try:
            r = subprocess.run(
                ["podman", "rm", cname],
                capture_output=True, text=True, timeout=10, check=False,
            )
            if r.returncode == 0:
                cleanup_log.append(f"container removed: {cname}")
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass

        try:
            r = subprocess.run(
                ["podman", "inspect", cname, "--format",
                 "{{range .Mounts}}{{.Name}} {{end}}"],
                capture_output=True, text=True, timeout=5, check=False,
            )
            if r.returncode == 0 and r.stdout.strip():
                for vol in r.stdout.strip().split():
                    if vol:
                        try:
                            subprocess.run(
                                ["podman", "volume", "rm", vol],
                                capture_output=True, timeout=10, check=False,
                            )
                            cleanup_log.append(f"volume removed: {vol}")
                        except (subprocess.TimeoutExpired, FileNotFoundError):
                            pass
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass

        break


def _cleanup_clone_dir(clone_path: str, cleanup_log: list[str]) -> None:
    """Remove a clone directory and its .old backups."""
    from demo_ghostprovider.hoster._helpers import _rmtree

    if os.path.isdir(clone_path):
        try:
            _rmtree(clone_path)
            cleanup_log.append(f"directory removed: {clone_path}")
        except Exception:  # noqa: BLE001
            logger.warning("failed to remove clone dir: %s", clone_path, exc_info=True)
            cleanup_log.append(f"failed to remove: {clone_path}")

    old_path = clone_path + ".old"
    if os.path.isdir(old_path):
        try:
            _rmtree(old_path)
            cleanup_log.append(f"backup removed: {old_path}")
        except Exception:  # noqa: BLE001
            logger.warning("failed to remove .old backup: %s", old_path, exc_info=True)
            cleanup_log.append(f"failed to remove backup: {old_path}")


def _reload_systemd(system_unit: bool = False) -> None:
    """Reload systemd daemon (user + system if needed)."""
    try:
        subprocess.run(
            ["systemctl", "--user", "daemon-reload"],
            capture_output=True, text=True, timeout=10,
            check=False,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    if system_unit:
        try:
            subprocess.run(
                ["systemctl", "daemon-reload"],
                capture_output=True, text=True, timeout=10,
                check=False,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass


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
                check=False,
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
