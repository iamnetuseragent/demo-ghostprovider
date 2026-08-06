"""Base class for hosting strategies."""

from pathlib import Path
from typing import Any


class Hoster:
    """Base class for all hosting strategies.

    Subclasses implement ``host()`` to provision a systemd service for a
    project of their language and return the created service name.
    """

    #: canonical name used in the strategy registry
    name: str = ""

    #: alternate names accepted when canonicalizing a strategy key
    aliases: tuple[str, ...] = ()

    #: human-readable description shown in plans
    description: str = ""

    #: relative priority used when several strategies could host a project
    priority: int = 100

    def host(self, project_dir: Path, port: int, repo_url: str = "",
             build_cmd: str = "", start_cmd: str = "", **kwargs: Any) -> str:
        """Host a project into a systemd service; returns the service name."""
        raise NotImplementedError
