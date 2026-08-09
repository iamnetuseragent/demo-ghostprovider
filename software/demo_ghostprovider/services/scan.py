"""systemd service discovery — enumerate units, resolve listening ports."""

import re
import subprocess

from demo_ghostprovider.services.models import ServiceInfo


def _is_systemd_service(unit_name: str) -> bool:
    """Check if a systemd unit is a service (not socket, timer, etc)."""
    try:
        subprocess.run(
            ["systemctl", "--user", "is-enabled", "--quiet", unit_name],
            capture_output=True, timeout=5,
            check=False,
        )
        # Also check if it's a .service unit
        r2 = subprocess.run(
            ["systemctl", "--user", "list-unit-files", f"{unit_name}.service"],
            capture_output=True, text=True, timeout=5,
            check=False,
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
            check=False,
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
            check=False,
        )
        if r.returncode == 0:
            for line in r.stdout.strip().split("\n"):
                parts = line.split()
                if len(parts) >= 1:
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
                    check=False,
                )
                if cg.returncode == 0 and cg.stdout.strip():
                    cg_path = f"/sys/fs/cgroup{cg.stdout.strip()}"
                    procs = subprocess.run(
                        ["bash", "-c", f"cat {cg_path}/cgroup.procs 2>/dev/null"],
                        capture_output=True, text=True, timeout=5,
                        check=False,
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
                check=False,
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


def _friendly_name(unit_name: str, repo_url: str) -> str:
    """Human-readable name for a state-tracked unit.

    Prefers the recipe display name (matched by service name first, then by
    the state's ``repo_url``) and falls back to the raw unit name.
    """
    from demo_ghostprovider.hoster.recipes import DEMO_SERVICES

    for recipe in DEMO_SERVICES:
        if recipe.service_name == unit_name:
            return recipe.display_name or recipe.name
    if repo_url:
        base = repo_url.rstrip("/").lower()
        for recipe in DEMO_SERVICES:
            if base.endswith(f"/{recipe.owner.lower()}/{recipe.name.lower()}"):
                return recipe.display_name or recipe.name
    return unit_name


def list_services() -> list[ServiceInfo]:
    """List services that demo_ghostprovider can manage.

    Shows only services tracked in this instance's own state
    (``~/.config/demo-ghostprovider/state.json``). Services deployed by the
    full ghostprovider (its own state file) are never shown here.
    """
    from demo_ghostprovider.state import load as _load_state
    services: list[ServiceInfo] = []

    # Load state to know which services belong to this instance
    state = _load_state()
    gp_services = {k for k in state if k != "version"}

    if not gp_services:
        return services

    # Only units that are actually loaded (running or stopped since a start)
    # are shown — installed-but-never-loaded unit files from old experiments
    # stay hidden. Tracked in state => owned by this demo instance.
    try:
        r = subprocess.run(
            ["systemctl", "--user", "list-units", "--type=service", "--plain", "--no-legend",
             "--all"],
            capture_output=True, text=True, timeout=10, check=False,
        )
        if r.returncode != 0:
            return services

        for line in r.stdout.strip().split("\n"):
            parts = line.split()
            if len(parts) < 4:
                continue

            unit_name = parts[0].replace(".service", "")
            if unit_name not in gp_services:
                continue

            status, state_val = parts[2], parts[3]

            ports = _get_unit_ports(unit_name)
            exec_start = _get_unit_property(f"{unit_name}.service", "ExecStart")

            repo_url = ""
            entry = state.get(unit_name)
            if isinstance(entry, dict):
                repo_url = entry.get("repo_url", "")

            description = _friendly_name(unit_name, repo_url)

            urls: list[str] = []
            for port in ports:
                if port > 0:
                    urls.append(f"http://localhost:{port}")

            services.append(ServiceInfo(
                name=unit_name,
                unit_name=unit_name,
                status=status,
                state=state_val,
                description=description,
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
