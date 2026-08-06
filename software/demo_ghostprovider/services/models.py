"""Data models for service discovery and management."""

from dataclasses import dataclass


@dataclass
class ServiceInfo:
    name: str
    unit_name: str
    status: str
    state: str
    description: str = ""
    ports: list[int] = None
    exec_start: str = ""
    urls: list[str] = None

    def __post_init__(self):
        if self.ports is None:
            self.ports = []
        if self.urls is None:
            self.urls = []
