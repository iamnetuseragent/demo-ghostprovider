"""Deployment verification."""

import subprocess
import time
from typing import Callable

from demo_ghostprovider.hoster.analysis import _http_get_with_curl_fallback
from demo_ghostprovider.hoster.systemd import _get_service_logs, _discover_service_urls


def verify_url(url: str, timeout: int = 15) -> tuple[bool, str]:
    """Check if a URL responds with HTTP 200. Returns (ok, detail)."""
    try:
        r = _http_get_with_curl_fallback(url, timeout=timeout, headers={"User-Agent": "demo_ghostprovider/1.0"})
        if r is not None and r.status_code == 200:
            return True, "HTTP 200 OK"
        return False, f"HTTP {r.status_code}" if r else "Connection refused"
    except Exception as e:
        return False, str(e)


def verify_deployment(result, timeout: int = 300,
                      on_status: Callable[[str], None] | None = None) -> "HostResult":
    """Verify a deployment is healthy."""
    from demo_ghostprovider.hoster.analysis import HostResult

    done_callback = on_status or (lambda _: None)

    if not result.service_names:
        result.errors.append("No services to verify")
        return result

    deadline = time.time() + timeout
    for service_name in result.service_names:
        while time.time() < deadline:
            try:
                r = subprocess.run(
                    ["systemctl", "--user", "is-active", service_name],
                    capture_output=True, text=True, timeout=5,
                )
                status = r.stdout.strip()
                if status == "active":
                    done_callback(f"service {service_name} is active")
                    break
                if status in ("failed", "inactive"):
                    logs = _get_service_logs(service_name, 20)
                    result.errors.append(f"Service {service_name} {status}: {logs[:200]}")
                    break
            except (subprocess.TimeoutExpired, FileNotFoundError):
                pass
            time.sleep(2)
        else:
            result.errors.append(f"Service {service_name} not active after {timeout}s")

    if not result.urls:
        for service_name in result.service_names:
            result.urls.extend(_discover_service_urls(service_name))

    if not result.urls:
        result.errors.append("No exposed ports found — service may not be a web service")
        for service_name in result.service_names:
            logs = _get_service_logs(service_name)
            if logs:
                result.errors.append(f"Service {service_name} logs:\n{logs[:300]}")
                break
        return result

    for url in result.urls:
        ok = False
        detail = ""
        retries = 0
        while time.time() < deadline and retries < 30:
            done_callback(f"checking {url} (attempt {retries + 1})...")
            ok, detail = verify_url(url, timeout=10)
            if ok:
                result.healthy = True
                done_callback(f"{url} is responding")
                break
            retries += 1
            sleep_time = min(5 * retries, 30)
            time.sleep(sleep_time)
        if ok:
            break
        result.healthy = False
        done_callback(f"health check failed for {url}")

    if not result.healthy:
        for service_name in result.service_names:
            logs = _get_service_logs(service_name)
            if logs:
                result.errors.append(f"Service {service_name} logs:\n{logs[:500]}")
                break
        if result.urls:
            result.errors.append(f"Health check failed for {result.urls}: {detail}")

    return result
