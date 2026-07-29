"""Go hosting strategy."""

import os
import re
import shutil
import subprocess
import uuid
from pathlib import Path

from demo_ghostprovider.hoster.systemd import (
    _create_systemd_service,
    _check_service_started,
    _get_service_logs,
)
from demo_ghostprovider.hoster._helpers import _resolve_start_cmd, _run_build_cmd


def _host_go_systemd(project_dir: Path, port: int, repo_url: str = "",
                     build_cmd: str = "", start_cmd: str = "") -> str:
    """Host a Go project using systemd.

    Scans for existing binaries, tries multiple build targets,
    falls back to `go run` if compilation fails.

    If build_cmd/start_cmd are provided from ghostproviderfile, they take
    precedence over auto-detected commands.  When no build_cmd is given but
    the project uses //go:embed for a frontend directory that contains only
    a placeholder, the frontend is built automatically (pnpm/npm/yarn).
    """
    service_name = f"ghost-go-{uuid.uuid4().hex[:8]}"

    output_bin = str(project_dir / "ghost-server")

    # ── 1. Run optional build command from ghostproviderfile ──
    if build_cmd:
        _run_build_cmd_go(build_cmd, project_dir, output_bin)

    # ── 2. Check for existing compiled binary ──
    # When build_cmd is provided, always prefer the fresh ghost-server binary
    # to avoid using a stale one without embedded frontend
    if build_cmd:
        if os.path.isfile(output_bin):
            binary_path = output_bin
        else:
            binary_path = None
    else:
        binary_path = _find_existing_go_binary(project_dir)

    # ── 2b. Auto-build embedded frontend if needed (no build_cmd) ──
    # Projects like memos require `pnpm release` before `go build` so the
    # React SPA is embedded into the binary.  Detect this automatically.
    if not build_cmd and _needs_frontend_build(project_dir):
        _auto_build_frontend(project_dir)
        # Remove stale binary so go build re-embeds the fresh frontend
        if binary_path and os.path.isfile(binary_path):
            os.remove(binary_path)
            binary_path = None

    # ── 3. Build if no binary found ──
    if not binary_path:
        build_targets = _detect_go_build_targets(project_dir)
        proxies = ["https://proxy.golang.org,direct", "direct"]

        tmp_base = os.path.expanduser("~/localhosts/.tmp")
        os.makedirs(tmp_base, exist_ok=True)
        env = {
            **os.environ,
            "GOPROXY": ",".join(proxies),
            "TMPDIR": tmp_base,
            "GOTMPDIR": tmp_base,
            "GOCACHE": os.path.join(tmp_base, "gocache"),
            "GOMODCACHE": os.path.join(tmp_base, "gomodcache"),
        }

        for target in build_targets:
            cmd = ["go", "build", "-o", output_bin]
            if target != ".":
                cmd.append(target)
            try:
                r = subprocess.run(
                    cmd, capture_output=True, text=True, timeout=900,
                    cwd=str(project_dir), env=env,
                )
                if r.returncode == 0 and os.path.isfile(output_bin):
                    binary_path = output_bin
                    break
            except (subprocess.TimeoutExpired, FileNotFoundError):
                continue

    # ── 3b. Verify embedded frontend directories are populated ──
    # Always verify — not just when build_cmd is provided — to catch
    # projects where go build succeeded but the frontend is still a placeholder.
    if binary_path:
        _verify_embedded_assets(project_dir)

    # ── 4. Determine exec_start ──
    if start_cmd:
        exec_start = _resolve_start_cmd(start_cmd, project_dir)
    elif binary_path:
        # Try common port flag formats
        exec_start = f"{binary_path} --port {port}"
    else:
        # Fallback: go run (slower startup, but works)
        target = _detect_go_build_targets(project_dir)[0] if _detect_go_build_targets(project_dir) else "."
        exec_start = f"go run {target} --port {port}"

    # ── 5. Create and start ──
    _create_systemd_service(
        service_name=service_name,
        working_dir=str(project_dir),
        exec_start=exec_start,
        description=f"GhostProvider: {repo_url}",
        port=port,
    )

    try:
        try:
            r = subprocess.run(
                ["systemctl", "--user", "start", service_name],
                capture_output=True, text=True, timeout=30,
            )
            if r.returncode != 0:
                raise RuntimeError(f"Failed to start service: {r.stderr}")
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            raise RuntimeError(f"Failed to start service: {e}")

        if not _check_service_started(service_name):
            logs = _get_service_logs(service_name, 15)
            raise RuntimeError(f"Service crashed immediately after start: {logs[:300]}")
    except RuntimeError:
        from demo_ghostprovider.hoster.systemd import _cleanup_strategy
        from demo_ghostprovider.hoster.analysis import HostResult
        _cleanup_strategy(HostResult(service_names=[service_name]))
        raise

    return service_name


# ── Build helpers ──────────────────────────────────────────────────────


def _run_build_cmd_go(build_cmd: str, project_dir: Path, output_bin: str) -> None:
    """Execute a Go build command from ghostproviderfile."""
    for tool in ("pnpm", "npm", "yarn", "bun"):
        if tool in build_cmd and not shutil.which(tool):
            raise RuntimeError(
                f"Build command requires '{tool}' but it is not installed"
            )
    build_env = os.environ.copy()
    tmp_base = os.path.expanduser("~/localhosts/.tmp")
    os.makedirs(tmp_base, exist_ok=True)
    build_env["TMPDIR"] = tmp_base
    build_env["GOTMPDIR"] = tmp_base
    build_env["GOCACHE"] = os.path.join(tmp_base, "gocache")
    build_env["GOMODCACHE"] = os.path.join(tmp_base, "gomodcache")
    try:
        r = _run_build_cmd(build_cmd, project_dir, timeout=900, env=build_env)
        if r.returncode != 0:
            if os.path.isfile(output_bin):
                os.remove(output_bin)
            raise RuntimeError(
                f"Build command failed (exit {r.returncode}):\n{r.stderr[:500]}"
            )
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        if os.path.isfile(output_bin):
            os.remove(output_bin)
        raise RuntimeError(f"Build command failed: {e}")


def _detect_embedded_frontends(project_dir: Path) -> list[Path]:
    """Find //go:embed directories that likely contain frontend assets.

    Returns a list of directories referenced by //go:embed directives
    (typically ``dist/`` under a ``frontend/`` package).
    """
    embed_re = re.compile(r'//go:embed\s+(\S+)')
    embed_dirs: list[Path] = []
    seen: set[str] = set()

    for gofile in project_dir.rglob("*.go"):
        if "vendor" in str(gofile) or "node_modules" in str(gofile):
            continue
        try:
            content = gofile.read_text(errors="replace")
        except OSError:
            continue
        for m in embed_re.finditer(content):
            pattern = m.group(1)
            if pattern in ("all:", "ignore:") or pattern.startswith("!"):
                continue
            if pattern in seen:
                continue
            seen.add(pattern)
            embed_dir = gofile.parent / pattern.rstrip("/*")
            if embed_dir.is_dir():
                embed_dirs.append(embed_dir)

    return embed_dirs


def _dir_has_placeholder(directory: Path) -> bool:
    """Check if a directory contains only a placeholder index.html."""
    if not directory.is_dir():
        return False
    files = list(directory.iterdir())
    if not files:
        return True
    if len(files) == 1 and files[0].name == "index.html":
        try:
            html = files[0].read_text(errors="replace")
        except OSError:
            return False
        if "No embeddable frontend" in html or len(html) < 500:
            return True
    return False


def _needs_frontend_build(project_dir: Path) -> bool:
    """Return True when //go:embed dirs exist but contain only placeholders.

    This catches the common case with memos where ``go build`` succeeds but
    the frontend was never built via ``pnpm release``.
    """
    for embed_dir in _detect_embedded_frontends(project_dir):
        if _dir_has_placeholder(embed_dir):
            return True
    return False


def _auto_build_frontend(project_dir: Path) -> None:
    """Detect and run the frontend build for Go projects with embedded SPAs.

    Looks for a ``web/`` (or ``frontend/``, ``client/``) subdirectory with
    a ``package.json`` and runs the appropriate package manager to build
    the assets into the embed directory.
    """
    web_dirs = ["web", "frontend", "client"]
    web_dir = None
    for name in web_dirs:
        candidate = project_dir / name
        if candidate.is_dir() and (candidate / "package.json").is_file():
            web_dir = candidate
            break

    if web_dir is None:
        return

    # Detect package manager
    pkg_manager = None
    if (web_dir / "pnpm-lock.yaml").is_file():
        pkg_manager = "pnpm"
    elif (web_dir / "yarn.lock").is_file():
        pkg_manager = "yarn"
    elif (web_dir / "bun.lockb").is_file() or (web_dir / "bun.lock").is_file():
        pkg_manager = "bun"
    else:
        pkg_manager = "npm"

    if not shutil.which(pkg_manager):
        return

    # Check package.json scripts for a "release" or "build" target
    import json
    try:
        pkg_json = json.loads((web_dir / "package.json").read_text())
        scripts = pkg_json.get("scripts", {})
    except (OSError, json.JSONDecodeError):
        return

    build_script = None
    for name in ("release", "build"):
        if name in scripts:
            build_script = name
            break

    if build_script is None:
        return

    build_env = os.environ.copy()
    tmp_base = os.path.expanduser("~/localhosts/.tmp")
    os.makedirs(tmp_base, exist_ok=True)
    build_env["TMPDIR"] = tmp_base
    build_env["GOTMPDIR"] = tmp_base
    build_env["GOCACHE"] = os.path.join(tmp_base, "gocache")
    build_env["GOMODCACHE"] = os.path.join(tmp_base, "gomodcache")

    try:
        subprocess.run(
            [pkg_manager, "install"], capture_output=True, text=True,
            timeout=300, cwd=str(web_dir), env=build_env,
        )
        subprocess.run(
            [pkg_manager, "run", build_script], capture_output=True, text=True,
            timeout=600, cwd=str(web_dir), env=build_env,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass


# ── Verification ───────────────────────────────────────────────────────


def _verify_embedded_assets(project_dir: Path) -> None:
    """Raise if Go //go:embed directories are empty or contain placeholders.

    Scans for //go:embed directives in .go files and checks that the
    referenced directories have at least one file. Common issue with
    projects like memos where frontend build (pnpm/npm) fails silently
    but go build succeeds with a placeholder.
    """
    embed_re = re.compile(r'//go:embed\s+(\S+)')
    checked: set[str] = set()

    for gofile in project_dir.rglob("*.go"):
        if "vendor" in str(gofile) or "node_modules" in str(gofile):
            continue
        try:
            content = gofile.read_text(errors="replace")
        except OSError:
            continue
        for m in embed_re.finditer(content):
            pattern = m.group(1)
            if pattern in ("all:", "ignore:") or pattern.startswith("!"):
                continue
            if pattern in checked:
                continue
            checked.add(pattern)
            # Resolve relative to the .go file's directory
            embed_dir = gofile.parent / pattern.rstrip("/*")
            if embed_dir.is_dir():
                files = list(embed_dir.iterdir())
                if not files:
                    raise RuntimeError(
                        f"Embedded directory '{embed_dir.name}/' is empty after build.\n"
                        f"Frontend build (pnpm/npm) likely failed silently.\n"
                        f"Check build output for errors."
                    )
                if len(files) == 1 and files[0].name == "index.html":
                    html_content = files[0].read_text(errors="replace")
                    if "No embeddable frontend" in html_content or len(html_content) < 500:
                        raise RuntimeError(
                            f"Embedded directory '{embed_dir.name}/' contains only a placeholder.\n"
                            f"Frontend build (pnpm/npm) likely failed.\n"
                            f"Re-deploy after fixing the build environment."
                        )


# ── Binary detection ───────────────────────────────────────────────────


def _find_existing_go_binary(project_dir: Path) -> str | None:
    """Find a pre-compiled Go binary in the project."""
    common_names = ("server", "app", "main", project_dir.name, "ghost-server")
    search_dirs = [project_dir, project_dir / "bin", project_dir / "target", project_dir / "cmd"]

    for d in search_dirs:
        if not d.is_dir():
            continue
        for name in common_names:
            p = d / name
            if p.is_file() and os.access(p, os.X_OK):
                return str(p)
    return None


def _detect_go_build_targets(project_dir: Path) -> list[str]:
    """Detect possible Go build targets (cmd directories, main.go locations)."""
    targets: list[str] = []

    # Check cmd/ directory (standard Go convention)
    cmd_dir = project_dir / "cmd"
    if cmd_dir.is_dir():
        for entry in cmd_dir.iterdir():
            if entry.is_dir() and (entry / "main.go").exists():
                targets.append(f"./cmd/{entry.name}")

    # Check for main.go in root
    if (project_dir / "main.go").exists():
        targets.append(".")

    # Check for any .go file with func main()
    if not targets:
        for gofile in project_dir.rglob("*.go"):
            if gofile.stat().st_size > 50000:
                continue
            try:
                if "func main()" in gofile.read_text(errors="replace"):
                    # Relative path from project_dir
                    rel = gofile.relative_to(project_dir)
                    targets.append(f"./{rel.parent}" if rel.parent != Path(".") else ".")
                    break
            except OSError:
                continue

    if not targets:
        targets.append(".")

    return targets
