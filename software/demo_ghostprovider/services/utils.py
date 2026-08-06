"""Small string parsing helpers for host/port and container mappings."""

import re


def _parse_host_port(addr: str) -> str | None:
    """Extract port from a host:port address string."""
    if not addr:
        return None
    # Handle [ipv6]:port
    m = re.search(r"\]:(\d+)$", addr)
    if m:
        return m.group(1)
    # Handle :::port or 0.0.0.0:port
    parts = addr.rsplit(":", 1)
    if len(parts) == 2 and parts[1].isdigit():
        return parts[1]
    # Plain port number
    if addr.isdigit():
        return addr
    return None


def container_urls(port_mappings: str) -> list[str]:
    """Parse Docker-style port mapping string and return HTTP URLs."""
    urls: list[str] = []
    for part in port_mappings.split(","):
        part = part.strip()
        if not part:
            continue
        # Format: "0.0.0.0:3000->3000/tcp" or "3000->3000/tcp"
        m = re.search(r":?(\d+)->", part)
        if m:
            host_port = m.group(1)
            urls.append(f"http://localhost:{host_port}")
    return urls
