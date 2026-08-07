"""HTTP helpers for GitHub metadata and repository checks."""

import os
import shutil
import subprocess
import time

import requests


def _is_allowed_url(url: str) -> bool:
    """Check if URL is allowed (prevents SSRF)."""
    allowed_hosts = {"api.github.com", "github.com", "raw.githubusercontent.com",
                     "127.0.0.1", "localhost"}
    try:
        from urllib.parse import urlparse
        parsed = urlparse(url)
        return parsed.hostname in allowed_hosts
    except (TypeError, ValueError):
        return False


def _http_get_with_curl_fallback(url: str, timeout: int = 10,
                                  headers: dict[str, str] | None = None,
                                  retries: int = 2) -> requests.Response | None:
    """GET a URL with retry, falling back to curl when Python SSL is broken.

    Automatically injects a ``GITHUB_TOKEN`` (or ``GH_TOKEN``) for GitHub API
    calls so the 60-req/h unauthenticated rate limit is avoided.
    Only allows requests to whitelisted hosts (SSRF protection).
    """
    if not _is_allowed_url(url):
        return None

    headers = dict(headers) if headers else {}
    # Auto-inject GitHub token for api.github.com requests
    if "api.github.com" in url and "Authorization" not in headers:
        token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""
        if token:
            headers["Authorization"] = f"token {token}"
    last_exc: Exception | None = None
    for attempt in range(1 + retries):
        try:
            r = requests.get(url, timeout=timeout, headers=headers)
            if r.status_code == 403 and attempt < retries:
                time.sleep(2 * (attempt + 1))
                continue
            return r
        except (requests.ConnectionError, requests.Timeout) as exc:
            last_exc = exc
            if attempt < retries:
                time.sleep(1)
                continue
            if not shutil.which("curl"):
                raise
            break

    try:
        cmd = ["curl", "-s", "-f", "--max-time", str(timeout)]
        for k, v in headers.items():
            cmd += ["-H", f"{k}: {v}"]
        cmd.append(url)
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 5, check=False)
        if proc.returncode == 0 and proc.stdout.strip():
            resp = requests.Response()
            resp.status_code = 200
            resp._content = proc.stdout.encode("utf-8")
            resp.encoding = "utf-8"
            return resp
        return None
    except (subprocess.TimeoutExpired, FileNotFoundError):
        if last_exc:
            raise last_exc
        return None
