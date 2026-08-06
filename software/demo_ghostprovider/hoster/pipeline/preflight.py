"""Pre-flight environment checks before deployment."""

import subprocess


def preflight_check() -> list[str]:
    """Run pre-flight checks before deployment. Returns list of issues."""
    issues: list[str] = []

    try:
        r = subprocess.run(
            ["systemctl", "--user", "is-system-running"],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode != 0 and "degraded" not in r.stdout:
            issues.append("systemd not running properly")
    except FileNotFoundError:
        issues.append("systemd not found")
    except subprocess.TimeoutExpired:
        issues.append("systemd not responding")

    try:
        r = subprocess.run(
            ["systemd-nspawn", "--version"],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode != 0:
            issues.append("systemd-nspawn not available")
    except FileNotFoundError:
        issues.append("systemd-nspawn not installed")
    except subprocess.TimeoutExpired:
        issues.append("systemd-nspawn not responding")

    try:
        r = subprocess.run(
            ["python3", "-c", "import urllib.request; urllib.request.urlopen('https://pypi.org', timeout=5)"],
            capture_output=True, timeout=10,
        )
        if r.returncode != 0:
            issues.append("No network connectivity")
    except (subprocess.TimeoutExpired, FileNotFoundError):
        issues.append("No network connectivity")

    return issues
