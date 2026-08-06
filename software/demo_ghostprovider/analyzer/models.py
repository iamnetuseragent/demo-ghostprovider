"""Data models for system & environment analysis."""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class InterfaceInfo:
    name: str
    ip: str
    netmask: str
    status: str


@dataclass
class ListeningPort:
    port: int
    proto: str
    address: str
    process: str


@dataclass
class ServiceFingerprint:
    port: int
    proto: str
    service_type: str
    service_name: str
    confidence: int
    details: dict[str, Any] = field(default_factory=dict)

    HOSTABLE_TYPES = frozenset({
        "web_app", "api_server", "media_server", "search_engine",
        "dashboard", "dev_server", "proxy", "file_server",
    })
    NON_HOSTABLE_TYPES = frozenset({
        "system_service", "desktop_app", "game_server",
        "database", "message_broker", "vpn", "unknown",
    })

    @property
    def can_host(self) -> bool:
        return self.service_type in self.HOSTABLE_TYPES


@dataclass
class NetworkInfo:
    interfaces: list[InterfaceInfo] = field(default_factory=list)
    listening_ports: list[ListeningPort] = field(default_factory=list)
    services: list[ServiceFingerprint] = field(default_factory=list)
    vpn_active: bool = False
    vpn_interfaces: list[str] = field(default_factory=list)
    gateway: str = ""
    dns: list[str] = field(default_factory=list)


@dataclass
class AnalysisResult:
    systemd: bool = False
    systemd_nspawn: bool = False
    git: bool = False
    python3: bool = False
    node: bool = False
    localhost: bool = False
    network: bool = False
    network_info: NetworkInfo = field(default_factory=NetworkInfo)
    errors: list[str] = field(default_factory=list)

    @property
    def all_ok(self) -> bool:
        return all([
            self.systemd, self.systemd_nspawn,
            self.git, self.python3,
            self.localhost, self.network,
        ])

    @property
    def summary_items(self) -> list[tuple[str, bool]]:
        return [
            ("🐍 Python 3", self.python3),
            ("⚙️  systemd", self.systemd),
            ("📦 systemd-nspawn", self.systemd_nspawn),
            ("🔧 Git", self.git),
            ("🟢 Node.js", self.node),
            ("🌐 Localhost", self.localhost),
            ("📡 Network", self.network),
        ]

    @property
    def hostable_services(self) -> list[ServiceFingerprint]:
        return [s for s in self.network_info.services if s.can_host]

    @property
    def non_hostable_services(self) -> list[ServiceFingerprint]:
        return [s for s in self.network_info.services if not s.can_host]
