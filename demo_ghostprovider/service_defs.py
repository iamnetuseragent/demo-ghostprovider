"""GhostProvider service definitions parser."""

import os
from pathlib import Path
from dataclasses import dataclass, field


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
