"""System probing — prerequisite checks, network scan, port fingerprinting."""

import re
import shutil
import socket
import subprocess
from typing import Any

from demo_ghostprovider.analyzer.models import (
    AnalysisResult,
    InterfaceInfo,
    ListeningPort,
    NetworkInfo,
    ServiceFingerprint,
)
from demo_ghostprovider.analyzer.signatures import SERVICE_SIGNATURES


def fingerprint_port(port: int, proto: str = "tcp") -> ServiceFingerprint | None:
    """Try to fingerprint an HTTP service on a given port."""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=3) as sock:
            sock.settimeout(5)
            sock.sendall(b"GET / HTTP/1.0\r\nHost: localhost\r\n\r\n")
            response = sock.recv(8192)
    except (TimeoutError, OSError):
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
        with socket.create_connection(("127.0.0.1", 80), timeout=2):
            return True
    except (OSError, ConnectionRefusedError):
        pass
    try:
        with socket.create_connection(("127.0.0.1", 8080), timeout=2):
            return True
    except (OSError, ConnectionRefusedError):
        return False


def _check_systemd_nspawn() -> bool:
    """Check if systemd-nspawn is available."""
    try:
        r = subprocess.run(
            ["systemd-nspawn", "--version"],
            capture_output=True, timeout=5,
            check=False,
        )
        return r.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _check_network() -> bool:
    try:
        subprocess.run(
            ["ping", "-c", "1", "-W", "2", "8.8.8.8"],
            capture_output=True, timeout=5,
            check=False,
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
            check=False,
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
                    )
                    )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return interfaces


def _detect_listening_ports() -> list[ListeningPort]:
    ports: list[ListeningPort] = []
    try:
        result = subprocess.run(
            ["ss", "-tlnp4"],
            capture_output=True, text=True, timeout=5,
            check=False,
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
                            )
                            )
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
            check=False,
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
