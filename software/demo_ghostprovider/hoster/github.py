"""GitHub URL parsing and repository metadata fetching."""

import re
from typing import Any

import requests

from demo_ghostprovider.hoster.http import _http_get_with_curl_fallback

GITHUB_URL_RE = re.compile(
    r"^https?://github\.com/([^/]+)/([^/]+?)(?:\.git)?/?$"
)


def parse_github_url(url: str) -> tuple[str, str] | None:
    m = GITHUB_URL_RE.match(url.strip())
    if m:
        return m.group(1), m.group(2).rstrip("/")
    return None


def fetch_repo_metadata(owner: str, name: str) -> tuple[dict[str, Any] | None, str | None]:
    """Fetch repo metadata from GitHub API.

    Returns (metadata, error_message).
    On success: (dict, None).
    On 404/private: (None, "not found").
    On network error: (None, "network error: <detail>").
    """
    try:
        r = _http_get_with_curl_fallback(
            f"https://api.github.com/repos/{owner}/{name}",
            headers={"Accept": "application/vnd.github.v3+json"},
        )
        if r is None:
            return (None, "Repository does not exist or is private")
        if r.status_code == 200:
            return (r.json(), None)
        if r.status_code == 404:
            return (None, "Repository does not exist or is private")
        if r.status_code == 403:
            return (None, "GitHub API rate limit exceeded — try again later or use a token")
        return (None, f"GitHub API returned HTTP {r.status_code}")
    except requests.ConnectionError as e:
        return (None, f"Network error: check your internet connection ({type(e).__name__})")
    except requests.Timeout:
        return (None, "Network error: request timed out")
    except requests.RequestException as e:
        return (None, f"Network error: {e}")


def _check_root_files_via_api(owner: str, name: str) -> set[str] | None:
    """Fetch root directory listing via GitHub Contents API (no clone needed)."""
    try:
        r = _http_get_with_curl_fallback(
            f"https://api.github.com/repos/{owner}/{name}/contents/",
            headers={"Accept": "application/vnd.github.v3+json"},
        )
        if r is None or r.status_code != 200:
            return None
        return {item["name"] for item in r.json() if isinstance(item, dict)}
    except (requests.RequestException, ValueError, TypeError):
        return None
