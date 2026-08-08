"""Per-service environment files for secrets.

Secrets are written to a per-service EnvironmentFile with mode 0600 and
referenced from the systemd unit via ``EnvironmentFile=``. This keeps secrets
out of the unit file (``~/.config/systemd/user/*.service``) and out of
``systemctl show`` output.
"""

import os
from pathlib import Path

from demo_ghostprovider.state import STATE_DIR

SECRETS_DIR = STATE_DIR / "secrets"


def _escape(value: str) -> str:
    """Escape a value for systemd EnvironmentFile double-quoted syntax."""
    value = value.replace("\\", "\\\\")
    value = value.replace('"', '\\"')
    value = value.replace("$", "\\$")
    value = value.replace("%", "%%")
    return value


def env_file_for(service_name: str) -> Path:
    return SECRETS_DIR / f"{service_name}.env"


def write_env_file(service_name: str, env: dict[str, str]) -> str | None:
    """Write env vars to a per-service EnvironmentFile (mode 0600).

    Returns the path as a string, or None when ``env`` is empty.
    """
    if not env:
        return None
    SECRETS_DIR.mkdir(parents=True, exist_ok=True)
    lines = []
    for key, value in env.items():
        safe_key = "".join(c for c in key if c.isalnum() or c == "_")
        if not safe_key:
            continue
        lines.append(f'{safe_key}="{_escape(value)}"')
    path = env_file_for(service_name)
    try:
        path.write_text("\n".join(lines) + "\n")
        os.chmod(path, 0o600)
    except OSError:
        return None
    return str(path)


def remove_env_file(service_name: str) -> None:
    """Remove the EnvironmentFile for a service (if any)."""
    try:
        env_file_for(service_name).unlink(missing_ok=True)
    except OSError:
        pass
