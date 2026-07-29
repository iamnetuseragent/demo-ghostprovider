"""GhostProvider service definitions parser."""

import hashlib
import logging
import os
import subprocess
from pathlib import Path
from dataclasses import dataclass, field

logger = logging.getLogger("demo_ghostprovider.service_defs")


@dataclass
class ServiceDef:
    """Definition of a deployable service."""
    name: str
    repo: str
    strategy: str
    port: int
    build: str = ""
    start: str = ""
    health: str = "/"
    sha256: str = ""
    ref: str = ""
    env: dict[str, str] = field(default_factory=dict)


def _find_ghostproviderfile() -> Path | None:
    """Find ghostproviderfile in project root or current directory."""
    candidates = [
        Path("ghostproviderfile"),
        Path(__file__).parent.parent.parent / "ghostproviderfile",
        Path.home() / "ghostproviderfile",
    ]
    for p in candidates:
        if p.exists():
            return p
    return None


def parse_ghostproviderfile(path: Path | None = None) -> list[ServiceDef]:
    """Parse ghostproviderfile and return list of service definitions."""
    if path is None:
        path = _find_ghostproviderfile()
    if path is None or not path.exists():
        return []

    services = []
    current = None

    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue

                if line.startswith("[") and line.endswith("]"):
                    if current:
                        services.append(current)
                    current = ServiceDef(
                        name=line[1:-1],
                        repo="",
                        strategy="python",
                        port=8080,
                    )
                elif current and "=" in line:
                    key, _, value = line.partition("=")
                    key = key.strip().lower()
                    value = value.strip()

                    if key == "repo":
                        current.repo = value
                    elif key == "strategy":
                        current.strategy = value
                    elif key == "port":
                        try:
                            current.port = int(value)
                        except ValueError:
                            pass
                    elif key == "build":
                        current.build = value
                    elif key == "start":
                        current.start = value
                    elif key == "health":
                        current.health = value
                    elif key == "sha256":
                        current.sha256 = value.lower()
                    elif key == "ref":
                        current.ref = value
                    elif key == "env" and "=" in value:
                        env_key, _, env_val = value.partition("=")
                        current.env[env_key.strip()] = env_val.strip()

            if current:
                services.append(current)
    except OSError:
        pass

    return services


def get_service_def(repo_url: str) -> ServiceDef | None:
    """Find service definition by repo URL."""
    for svc in parse_ghostproviderfile():
        if svc.repo == repo_url:
            return svc
    return None


def verify_repo_integrity(clone_path: str, svc_def: ServiceDef) -> list[str]:
    """Verify a cloned repository matches the expected integrity constraints.

    Supports:
    - ``sha256``: expected SHA256 of ``git rev-parse HEAD`` (commit pinning)
    - ``ref``: expected git ref (tag or branch name)

    Returns a list of error messages (empty means integrity is verified).
    """
    errors: list[str] = []

    if svc_def.ref:
        try:
            r = subprocess.run(
                ["git", "rev-parse", "--verify", f"refs/heads/{svc_def.ref}"],
                capture_output=True, text=True, timeout=10,
                cwd=clone_path,
            )
            if r.returncode != 0:
                r = subprocess.run(
                    ["git", "rev-parse", "--verify", f"refs/tags/{svc_def.ref}"],
                    capture_output=True, text=True, timeout=10,
                    cwd=clone_path,
                )
            if r.returncode != 0:
                errors.append(f"Required ref '{svc_def.ref}' not found in repository")
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            errors.append(f"Cannot verify ref '{svc_def.ref}': {e}")

    if svc_def.sha256:
        try:
            r = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                capture_output=True, text=True, timeout=10,
                cwd=clone_path,
            )
            if r.returncode == 0:
                head_sha = r.stdout.strip()
                computed = hashlib.sha256(head_sha.encode()).hexdigest()
                if computed != svc_def.sha256:
                    errors.append(
                        f"SHA256 mismatch: expected {svc_def.sha256}, "
                        f"got {computed} (HEAD is {head_sha[:12]})"
                    )
            else:
                errors.append("Cannot get HEAD commit hash from repository")
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            errors.append(f"Cannot verify SHA256: {e}")

    return errors
