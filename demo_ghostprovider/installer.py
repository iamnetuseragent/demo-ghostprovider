"""Smart dependency installer for ghostprovider."""

import os
import shutil
import subprocess


def required_tools(has_package_json: bool, has_requirements: bool,
                   has_go_mod: bool, has_cargo: bool,
                   has_index: bool = False) -> list[str]:
    """Return list of required tool names based on repo analysis."""
    tools = ["git"]
    return tools


def tool_display_name(tool: str) -> str:
    names = {
        "git": "Git",
        "systemd": "systemd",
        "systemd-nspawn": "systemd-nspawn",
    }
    return names.get(tool, tool)


def tool_description(tool: str) -> str:
    desc = {
        "git": "Git — version control system (cloning repositories)",
        "systemd": "systemd — service management",
        "systemd-nspawn": "systemd-nspawn — lightweight containerization",
    }
    return desc.get(tool, tool)


def is_installed(tool: str) -> bool:
    return shutil.which(tool) is not None


def missing_tools(tools: list[str]) -> list[str]:
    return [t for t in tools if not is_installed(t)]


def detect_pm() -> str | None:
    """Detect available package manager."""
    for cmd in ("yay", "paru", "pacman", "apt", "dnf", "zypper"):
        if shutil.which(cmd):
            return cmd
    return None


_PM_PKGS: dict[str, dict[str, str]] = {
    "pacman": {
        "git": "git",
    },
    "yay": {
        "git": "git",
    },
    "paru": {
        "git": "git",
    },
    "apt": {
        "git": "git",
    },
    "dnf": {
        "git": "git",
    },
    "zypper": {
        "git": "git",
    },
}

_PM_BASE: dict[str, list[str]] = {
    "pacman": ["pacman", "-S", "--noconfirm"],
    "yay": ["yay", "-S", "--noconfirm", "--needed"],
    "paru": ["paru", "-S", "--noconfirm", "--needed"],
    "apt": ["apt", "install", "-y"],
    "dnf": ["dnf", "install", "-y"],
    "zypper": ["zypper", "install", "-y"],
}


def _pm_install_cmd(pm: str, tool: str) -> list[str]:
    pkg = _PM_PKGS.get(pm, {}).get(tool)
    if not pkg:
        return []
    return _PM_BASE.get(pm, []) + [pkg]


def _run_sudo(cmd: list[str], pw_bytes: bytearray | None,
              sudo_path: str | None) -> tuple[int, str]:
    """Run a command with sudo, using password if available."""
    if pw_bytes is not None and sudo_path:
        full_cmd = ["sudo", "-S"] + cmd
        proc = subprocess.Popen(
            full_cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        proc.communicate(input=pw_bytes + b"\n", timeout=120)
        return proc.returncode, proc.stderr.decode(errors="replace")
    elif sudo_path:
        result = subprocess.run(
            [sudo_path] + cmd,
            capture_output=True, text=True, timeout=120,
        )
        return result.returncode, result.stderr
    else:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=120,
        )
        return result.returncode, result.stderr


def install_tools(tools: list[str], password: str | None = None) -> tuple[list[str], list[str]]:
    """Install missing tools. Returns (failed_tools, warnings)."""
    pm = detect_pm()
    if not pm:
        return (tools, [])

    pw_bytes: bytearray | None = None
    if password is not None:
        pw_bytes = bytearray(password, "utf-8")

    sudo_path = shutil.which("sudo")
    failed: list[str] = []
    installed: list[str] = []
    for tool in tools:
        if is_installed(tool):
            continue
        cmd = _pm_install_cmd(pm, tool)
        if not cmd:
            failed.append(tool)
            continue

        try:
            _run_sudo(cmd, pw_bytes, sudo_path)
            if is_installed(tool):
                installed.append(tool)
            else:
                failed.append(tool)
        except (subprocess.TimeoutExpired, OSError, FileNotFoundError):
            failed.append(tool)

    warnings: list[str] = []

    if pw_bytes is not None:
        for i in range(len(pw_bytes)):
            pw_bytes[i] = 0

    return (failed, warnings)


def post_install_actions(tools: list[str], password: str | None = None) -> list[str]:
    """Run post-install setup. Returns warning messages for the user."""
    warnings: list[str] = []
    # No special post-install actions needed for systemd-based setup
    return warnings
