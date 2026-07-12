"""System & environment analysis for ghostprovider."""

import re
import subprocess
import shutil
import socket
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


# ── Well-known service fingerprints ─────────────────────────────────

SERVICE_SIGNATURES: list[tuple[re.Pattern, str, str, int]] = [
    # Search engines
    (re.compile(rb"<title>.*SearXNG?.*</title>", re.I), "search_engine", "SearXNG", 95),
    (re.compile(rb"SearXNG?[/\s]", re.I), "search_engine", "SearXNG", 85),
    (re.compile(rb"<title>.*Whoogle.*</title>", re.I), "search_engine", "Whoogle Search", 95),
    (re.compile(rb"Whoogle", re.I), "search_engine", "Whoogle Search", 80),
    (re.compile(rb"<title>.*YaCy.*</title>", re.I), "search_engine", "YaCy", 95),
    (re.compile(rb"libre(y|Y)ou", re.I), "search_engine", "LibreY", 90),
    (re.compile(rb"<title>.*Shiori.*</title>", re.I), "search_engine", "Shiori", 90),

    # Media servers
    (re.compile(rb"<title>.*Jellyfin.*</title>", re.I), "media_server", "Jellyfin", 95),
    (re.compile(rb"Jellyfin[/\s]", re.I), "media_server", "Jellyfin", 90),
    (re.compile(rb"<title>.*Plex.*</title>", re.I), "media_server", "Plex", 95),
    (re.compile(rb"<title>.*Navidrome.*</title>", re.I), "media_server", "Navidrome", 95),
    (re.compile(rb"<title>.*Airsonic.*</title>", re.I), "media_server", "Airsonic", 95),
    (re.compile(rb"<title>.*Funkwhale.*</title>", re.I), "media_server", "Funkwhale", 95),
    (re.compile(rb"<title>.*Koel.*</title>", re.I), "media_server", "Koel", 95),
    (re.compile(rb"<title>.*Black Candy.*</title>", re.I), "media_server", "Black Candy", 95),
    (re.compile(rb"<title>.*Sonarr.*</title>", re.I), "media_server", "Sonarr", 95),
    (re.compile(rb"<title>.*Radarr.*</title>", re.I), "media_server", "Radarr", 95),
    (re.compile(rb"<title>.*SABnzbd.*</title>", re.I), "media_server", "SABnzbd", 95),
    (re.compile(rb"<title>.*Transmission.*</title>", re.I), "media_server", "Transmission", 95),
    (re.compile(rb"<title>.*qBittorrent.*</title>", re.I), "media_server", "qBittorrent", 95),

    # Dashboards & monitoring
    (re.compile(rb"<title>.*Home[ -]?[Aa]ssistant.*</title>", re.I), "dashboard", "Home Assistant", 95),
    (re.compile(rb"<title>.*Grafana.*</title>", re.I), "dashboard", "Grafana", 95),
    (re.compile(rb"grafana", re.I), "dashboard", "Grafana", 80),
    (re.compile(rb"<title>.*Prometheus.*</title>", re.I), "dashboard", "Prometheus", 95),
    (re.compile(rb"<title>.*Netdata.*</title>", re.I), "dashboard", "Netdata", 95),
    (re.compile(rb"<title>.*Portainer.*</title>", re.I), "dashboard", "Portainer", 95),
    (re.compile(rb"<title>.*phpMyAdmin.*</title>", re.I), "dashboard", "phpMyAdmin", 90),

    # Web servers / proxies
    (re.compile(rb"nginx[/\s]", re.I), "proxy", "Nginx", 80),
    (re.compile(rb"Apache[/\s]", re.I), "web_app", "Apache HTTPD", 80),
    (re.compile(rb"Caddy[/\s]", re.I), "proxy", "Caddy", 80),
    (re.compile(rb"Traefik", re.I), "proxy", "Traefik", 80),

    # Dev servers / tools
    (re.compile(rb"<title>.*phpinfo.*</title>", re.I), "dev_server", "PHP info", 95),
    (re.compile(rb"Vite|vite", re.I), "dev_server", "Vite Dev Server", 80),
    (re.compile(rb"webpack", re.I), "dev_server", "Webpack Dev Server", 80),

    # File sharing
    (re.compile(rb"<title>.*File[Gg]ator.*</title>", re.I), "file_server", "FileGator", 95),
    (re.compile(rb"<title>.*Nextcloud.*</title>", re.I), "file_server", "Nextcloud", 95),
    (re.compile(rb"<title>.*OwnCloud.*</title>", re.I), "file_server", "ownCloud", 95),

    # RSS readers
    (re.compile(rb"<title>.*Miniflux.*</title>", re.I), "web_app", "Miniflux", 95),
    (re.compile(rb"<title>.*Tiny[ -]?Tiny[ -]?RSS.*</title>", re.I), "web_app", "Tiny Tiny RSS", 95),
    (re.compile(rb"<title>.*FreshRSS.*</title>", re.I), "web_app", "FreshRSS", 95),

    # Password managers
    (re.compile(rb"<title>.*Bitwarden.*</title>", re.I), "web_app", "Bitwarden", 95),
    (re.compile(rb"<title>.*Vaultwarden.*</title>", re.I), "web_app", "Vaultwarden", 95),

    # Git services
    (re.compile(rb"<title>.*Gitea.*</title>", re.I), "web_app", "Gitea", 95),
    (re.compile(rb"<title>.*GitLab.*</title>", re.I), "web_app", "GitLab", 95),
    (re.compile(rb"<title>.*Gogs.*</title>", re.I), "web_app", "Gogs", 95),

    # Note-taking / wikis
    (re.compile(rb"<title>.*Outline.*</title>", re.I), "web_app", "Outline", 85),
    (re.compile(rb"<title>.*Bookstack.*</title>", re.I), "web_app", "BookStack", 95),
    (re.compile(rb"<title>.*Wiki\.js.*</title>", re.I), "web_app", "Wiki.js", 95),
    (re.compile(rb"<title>.*Docum?ent.*</title>", re.I), "web_app", "Documenso", 80),

    # Generic services
    (re.compile(rb"<title>.*Syncthing.*</title>", re.I), "web_app", "Syncthing", 95),
    (re.compile(rb"syncthing", re.I), "web_app", "Syncthing", 80),
    (re.compile(rb"<title>.*AdGuard.*</title>", re.I), "proxy", "AdGuard Home", 95),
    (re.compile(rb"<title>.*Pi-?hole.*</title>", re.I), "proxy", "Pi-hole", 95),
    (re.compile(rb"<title>.*Uptime[ -]?[Kk]uma.*</title>", re.I), "dashboard", "Uptime Kuma", 95),
    (re.compile(rb"<title>.*Changedetection.*</title>", re.I), "web_app", "Changedetection.io", 95),

    # DB admin tools
    (re.compile(rb"<title>.*Adminer.*</title>", re.I), "dashboard", "Adminer", 90),
    (re.compile(rb"<title>.*PgAdmin.*</title>", re.I), "dashboard", "pgAdmin", 90),

    # System services (non-hostable)
    (re.compile(rb"<title>.*Router.*</title>", re.I), "system_service", "Router Admin", 70),
    (re.compile(rb"<title>.*Printer.*</title>", re.I), "system_service", "Printer Interface", 70),

    # Desktop/GUI apps with web UI
    (re.compile(rb"<title>.*Jupyter.*</title>", re.I), "desktop_app", "Jupyter Notebook", 80),
]


def fingerprint_port(port: int, proto: str = "tcp") -> ServiceFingerprint | None:
    """Try to fingerprint an HTTP service on a given port."""
    try:
        sock = socket.create_connection(("127.0.0.1", port), timeout=3)
        sock.settimeout(5)
        sock.sendall(b"GET / HTTP/1.0\r\nHost: localhost\r\n\r\n")
        response = sock.recv(8192)
        sock.close()
    except (OSError, socket.timeout):
        return None

    headers_end = response.find(b"\r\n\r\n")
    if headers_end == -1:
        return None

    body = response[headers_end + 4:]
    headers_raw = response[:headers_end].decode("utf-8", errors="replace")
    status_line = headers_raw.split("\r\n")[0] if headers_raw else ""

    details: dict[str, Any] = {
        "status_line": status_line,
        "server_header": "",
    }

    for line in headers_raw.split("\r\n")[1:]:
        if line.lower().startswith("server:"):
            details["server_header"] = line.split(":", 1)[1].strip()
            break

    # Match against known signatures
    for sig_pattern, svc_type, svc_name, confidence in SERVICE_SIGNATURES:
        if sig_pattern.search(body):
            return ServiceFingerprint(
                port=port,
                proto=proto,
                service_type=svc_type,
                service_name=svc_name,
                confidence=confidence,
                details=details,
            )

    # Fallback: detect generic web server from server header
    server = details.get("server_header", "").lower()
    if server:
        if any(x in server for x in ("nginx", "apache", "caddy", "iis")):
            return ServiceFingerprint(
                port=port, proto=proto,
                service_type="web_app",
                service_name=server.split("/")[0].title(),
                confidence=60,
                details=details,
            )
        if "gunicorn" in server:
            return ServiceFingerprint(
                port=port, proto=proto,
                service_type="web_app",
                service_name="Python WSGI (gunicorn)",
                confidence=70,
                details=details,
            )
        if "uvicorn" in server:
            return ServiceFingerprint(
                port=port, proto=proto,
                service_type="web_app",
                service_name="Python ASGI (uvicorn)",
                confidence=70,
                details=details,
            )
        if "node" in server.lower() or "express" in server.lower():
            return ServiceFingerprint(
                port=port, proto=proto,
                service_type="web_app",
                service_name="Node.js HTTP Server",
                confidence=65,
                details=details,
            )
        if "python" in server.lower():
            return ServiceFingerprint(
                port=port, proto=proto,
                service_type="web_app",
                service_name="Python HTTP Server",
                confidence=60,
                details=details,
            )

    # Generic HTTP response → unknown web app
    return ServiceFingerprint(
        port=port, proto=proto,
        service_type="web_app",
        service_name="Unknown HTTP Service",
        confidence=30,
        details=details,
    )


def _fingerprint_all_services(ports: list[ListeningPort]) -> list[ServiceFingerprint]:
    services: list[ServiceFingerprint] = []
    for p in ports:
        fp = fingerprint_port(p.port, p.proto)
        if fp is not None:
            services.append(fp)
    return services


def _check_cmd(name: str) -> bool:
    return shutil.which(name) is not None


def _check_localhost() -> bool:
    try:
        sock = socket.create_connection(("127.0.0.1", 80), timeout=2)
        sock.close()
        return True
    except (OSError, ConnectionRefusedError):
        pass
    try:
        sock = socket.create_connection(("127.0.0.1", 8080), timeout=2)
        sock.close()
        return True
    except (OSError, ConnectionRefusedError):
        return False


def _check_systemd_nspawn() -> bool:
    """Check if systemd-nspawn is available."""
    try:
        r = subprocess.run(
            ["systemd-nspawn", "--version"],
            capture_output=True, timeout=5,
        )
        return r.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _check_network() -> bool:
    try:
        subprocess.run(
            ["ping", "-c", "1", "-W", "2", "8.8.8.8"],
            capture_output=True, timeout=5,
        )
        return True
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def _detect_interfaces() -> list[InterfaceInfo]:
    interfaces: list[InterfaceInfo] = []
    try:
        result = subprocess.run(
            ["ip", "-br", "addr", "show"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            for line in result.stdout.strip().split("\n"):
                parts = line.split()
                if len(parts) >= 3:
                    name = parts[0]
                    status = "up" if parts[1] == "UP" else "down"
                    ip_info = parts[2] if len(parts) > 2 else ""
                    ip = ip_info.split("/")[0] if ip_info else ""
                    netmask = f"/{ip_info.split('/')[1]}" if "/" in ip_info else ""
                    interfaces.append(InterfaceInfo(
                        name=name, ip=ip, netmask=netmask, status=status,
                    ))
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return interfaces


def _detect_listening_ports() -> list[ListeningPort]:
    ports: list[ListeningPort] = []
    try:
        result = subprocess.run(
            ["ss", "-tlnp4"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            for line in result.stdout.strip().split("\n")[1:]:
                if not line.strip():
                    continue
                parts = line.split()
                if len(parts) >= 4:
                    proto = "tcp"
                    addr_port = parts[3]
                    process = ""
                    if len(parts) > 4:
                        proc_match = re.search(r'users:\(\("(.+?)"', parts[-1])
                        if proc_match:
                            process = proc_match.group(1)
                    if ":" in addr_port:
                        addr, port_str = addr_port.rsplit(":", 1)
                        try:
                            port = int(port_str)
                            ports.append(ListeningPort(
                                port=port, proto=proto,
                                address=addr, process=process,
                            ))
                        except ValueError:
                            pass
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return ports


def _detect_vpn(interfaces: list[InterfaceInfo]) -> tuple[bool, list[str]]:
    vpn_keywords = {"tun", "tap", "wg", "ppp", "vpn", "virbr"}
    vpn_ifaces = []
    for iface in interfaces:
        name_lower = iface.name.lower()
        if any(kw in name_lower for kw in vpn_keywords):
            vpn_ifaces.append(iface.name)
    return (len(vpn_ifaces) > 0), vpn_ifaces


def _get_gateway() -> str:
    try:
        result = subprocess.run(
            ["ip", "route", "show", "default"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            parts = result.stdout.strip().split()
            if len(parts) >= 3:
                return parts[2]
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return ""


def _get_dns() -> list[str]:
    dns_servers = []
    try:
        with open("/etc/resolv.conf") as f:
            for line in f:
                if line.startswith("nameserver "):
                    dns_servers.append(line.split()[1])
    except (FileNotFoundError, OSError):
        pass
    return dns_servers


def run_analysis() -> AnalysisResult:
    interfaces = _detect_interfaces()
    ports = _detect_listening_ports()
    vpn_active, vpn_ifaces = _detect_vpn(interfaces)

    services = _fingerprint_all_services(ports)

    net_info = NetworkInfo(
        interfaces=interfaces,
        listening_ports=ports,
        services=services,
        vpn_active=vpn_active,
        vpn_interfaces=vpn_ifaces,
        gateway=_get_gateway(),
        dns=_get_dns(),
    )

    result = AnalysisResult(
        systemd=_check_cmd("systemctl"),
        systemd_nspawn=_check_systemd_nspawn(),
        git=_check_cmd("git"),
        python3=_check_cmd("python3"),
        node=_check_cmd("node"),
        localhost=_check_localhost(),
        network=_check_network(),
        network_info=net_info,
    )
    if not result.systemd:
        result.errors.append("systemd not found — required for service management")
    if not result.systemd_nspawn:
        result.errors.append("systemd-nspawn not found — recommended for isolated hosting")
    if not result.git:
        result.errors.append("Git not found — cannot clone repositories")
    if not result.network:
        result.errors.append("No network — cannot fetch remote repositories")
    return result
