"""systemd service management helpers."""

import os
import re
import subprocess
import time


def _validate_unit_name(name: str) -> bool:
    """Validate that a string is a safe systemd unit name."""
    # Systemd unit names: [A-Za-z0-9_\-.]+
    return bool(re.match(r'^[A-Za-z0-9_\-.]+$', name)) and len(name) <= 255


def _escape_unit_value(value: str) -> str:
    """Escape a value for use in a systemd unit file."""
    # Replace newlines and backslashes
    value = value.replace("\\", "\\\\")
    value = value.replace("\n", "\\n")
    value = value.replace('"', '\\"')
    return value


def _validate_service_name(service_name: str) -> str:
    """Validate and sanitize a service name for systemd."""
    if not _validate_unit_name(service_name):
        # Sanitize: keep only safe characters
        return re.sub(r'[^a-zA-Z0-9_\-.]', '-', service_name)[:64]
    return service_name


def _create_systemd_service(service_name: str, working_dir: str,
                            exec_start: str, description: str = "",
                            port: int = 0,
                            extra_env: dict[str, str] | None = None,
                            env_file: str | None = None) -> None:
    """Create a systemd service unit file with security hardening."""
    # Validate service name
    service_name = _validate_service_name(service_name)

    user_dir = os.path.expanduser("~/.config/systemd/user")
    os.makedirs(user_dir, exist_ok=True)

    env_lines = ""
    if extra_env:
        for k, v in extra_env.items():
            safe_k = re.sub(r'[^A-Za-z0-9_]', '', k)
            safe_v = _escape_unit_value(v)
            env_lines += f'Environment="{safe_k}={safe_v}"\n'

    env_file_line = ""
    if env_file and os.path.isfile(env_file):
        safe_env_file = _escape_unit_value(os.path.abspath(env_file))
        env_file_line = f'EnvironmentFile={safe_env_file}\n'

    safe_desc = _escape_unit_value(description or service_name)
    safe_working = _escape_unit_value(working_dir)
    safe_exec = _escape_unit_value(exec_start)

    # Use ProtectSystem=full when env_file is outside working_dir (needs /etc read access)
    # ProtectSystem=strict makes /etc read-only, breaking DNS resolution for services
    # that read /etc/resolv.conf. ProtectSystem=full only protects /usr and /boot.
    protect_system = "full"
    user_home = os.path.expanduser("~")

    unit_content = f"""[Unit]
Description={safe_desc}
After=network.target

[Service]
Type=simple
WorkingDirectory={safe_working}
ExecStart={safe_exec}
Restart=on-failure
RestartSec=5
{env_lines}{env_file_line}
# ── Privacy & Security Hardening ──
NoNewPrivileges=yes
ProtectHome=read-only
ProtectSystem={protect_system}
ReadWritePaths={safe_working} {user_home}/.cache
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectControlGroups=yes
RestrictNamespaces=yes
LockPersonality=yes
RestrictRealtime=yes
RestrictSUIDSGID=yes
CapabilityBoundingSet=

[Install]
WantedBy=default.target
"""

    unit_path = os.path.join(user_dir, f"{service_name}.service")
    with open(unit_path, "w") as f:
        f.write(unit_content)

    try:
        subprocess.run(
            ["systemctl", "--user", "daemon-reload"],
            capture_output=True, text=True, timeout=10,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    try:
        subprocess.run(
            ["systemctl", "--user", "enable", service_name],
            capture_output=True, text=True, timeout=10,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass


def _check_service_started(service_name: str, delay: float = 5.0) -> bool:
    """Check if a systemd service is still running after a short delay."""
    time.sleep(delay)
    try:
        r = subprocess.run(
            ["systemctl", "--user", "is-active", service_name],
            capture_output=True, text=True, timeout=5,
        )
        return r.stdout.strip() == "active"
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def _get_service_logs(service_name: str, lines: int = 50) -> str:
    """Get recent logs from a systemd user service."""
    try:
        r = subprocess.run(
            ["journalctl", "--user", "-u", service_name, "-n", str(lines), "--no-pager"],
            capture_output=True, text=True, timeout=10,
        )
        if r.returncode == 0:
            return r.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return ""


def _discover_service_urls(service_name: str) -> list[str]:
    """Discover HTTP URLs for a systemd service."""
    urls: list[str] = []

    try:
        r = subprocess.run(
            ["systemctl", "--user", "show", service_name, "--property=MainPID", "--value"],
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
                        addr_port = parts[3]
                        if ":" in addr_port:
                            port_str = addr_port.rsplit(":", 1)[-1]
                            if port_str.isdigit():
                                urls.append(f"http://localhost:{port_str}")
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    return urls


def _cleanup_strategy(result) -> None:
    """Remove services started by a failed strategy attempt."""
    if result.service_names:
        for service_name in result.service_names:
            try:
                subprocess.run(
                    ["systemctl", "--user", "stop", service_name],
                    capture_output=True, text=True, timeout=10,
                )
                subprocess.run(
                    ["systemctl", "--user", "disable", service_name],
                    capture_output=True, text=True, timeout=10,
                )
                unit_file = os.path.expanduser(f"~/.config/systemd/user/{service_name}.service")
                if os.path.isfile(unit_file):
                    os.remove(unit_file)
            except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
                pass

        try:
            subprocess.run(
                ["systemctl", "--user", "daemon-reload"],
                capture_output=True, text=True, timeout=10,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass
