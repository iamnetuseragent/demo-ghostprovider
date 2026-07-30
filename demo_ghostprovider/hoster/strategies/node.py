"""Node.js hosting strategy."""

import glob
import re
import shutil
import subprocess
import uuid
from pathlib import Path

from demo_ghostprovider.hoster._helpers import _read_package_json, _resolve_start_cmd
from demo_ghostprovider.hoster.systemd import (
    _create_systemd_service,
    _check_service_started,
    _get_service_logs,
)


def _resolve_serve_dir(project_dir: Path, candidates: list[str]) -> str | None:
    """Find first candidate dir under project_dir that contains index.html."""
    for d in candidates:
        if (project_dir / d / "index.html").exists():
            return d
    return None


def _host_node_systemd(project_dir: Path, port: int, repo_url: str = "",
                       build_cmd: str = "", start_cmd: str = "") -> str:
    """Host a Node.js project using systemd.

    Handles monorepos (yarn workspaces, npm workspaces), SvelteKit,
    Next.js, Yarn 4 projects (e.g. AFFiNE), and generic Node apps.
    Detects package manager (npm/yarn/pnpm/bun) from lock files.

    If build_cmd/start_cmd are provided from ghostproviderfile, they take
    precedence over auto-detected commands.
    """
    service_name = f"ghost-js-{uuid.uuid4().hex[:8]}"

    pkg = _read_package_json(project_dir)

    # ── Detect package manager ──
    pm = _detect_node_pm(project_dir)
    run_cmd = pm["run"]
    install_cmd = pm["install"]
    serve_cmd = pm["serve"]

    # ── Detect Yarn 4 (bundled in .yarn/releases/) ──
    is_yarn4 = (project_dir / ".yarn" / "releases").is_dir()
    yarn4_bin = None
    if is_yarn4:
        for f in (project_dir / ".yarn" / "releases").iterdir():
            if f.suffix == ".cjs" and "yarn" in f.name:
                yarn4_bin = str(f)
                break
    if yarn4_bin:
        run_cmd = f"node {yarn4_bin}"
        install_cmd = f"node {yarn4_bin} install --no-immutable --mode skip-build"

    # ── Detect monorepo ──
    workspaces = _detect_workspaces(project_dir, pkg)
    if workspaces:
        # In a monorepo, try to find the web app package
        app_dir = _find_webapp_in_monorepo(project_dir, workspaces)
        if app_dir and app_dir != project_dir:
            project_dir = app_dir
            pkg = _read_package_json(project_dir)

    # ── Detect Electron ──
    all_deps = {}
    if pkg:
        all_deps.update(pkg.get("dependencies", {}))
        all_deps.update(pkg.get("devDependencies", {}))
    is_electron = "electron" in all_deps

    scripts = (pkg or {}).get("scripts", {})
    has_build = "build" in scripts
    has_start = "start" in scripts
    has_dev = "dev" in scripts
    has_preview = "preview" in scripts

    # ── Detect SvelteKit ──
    is_sveltekit = (project_dir / "svelte.config.js").exists() or (
        project_dir / "svelte.config.ts").exists()
    if pkg:
        is_sveltekit = is_sveltekit or "@sveltejs/kit" in all_deps

    # ── Auto-create .env ──
    _auto_env(project_dir)

    # ── Build ──
    # Use ghostproviderfile overrides if provided
    if build_cmd:
        build_layer = build_cmd
    else:
        build_layer = ""

    if start_cmd:
        serve_full = start_cmd
    else:
        serve_full = ""

    # Auto-detect serve command when not provided but build is
    static_serve = False
    if build_layer and not serve_full:
        if is_sveltekit:
            serve_full = f"{serve_cmd} -s build -l {port}"
            static_serve = True
        elif has_start:
            serve_full = f"{run_cmd} run start"
        elif has_preview:
            serve_full = f"{run_cmd} run preview --host 127.0.0.1 --port {port}"
        elif has_dev:
            serve_full = f"{run_cmd} run dev --host 127.0.0.1 --port {port}"
        else:
            serve_full = f"{serve_cmd} -s . -l {port}"
            static_serve = True
    elif not build_layer and not serve_full:
        if is_sveltekit:
            build_layer = f"{run_cmd} run build"
            serve_full = f"{serve_cmd} -s build -l {port}"
            static_serve = True
        elif has_build and has_start:
            build_layer = f"{run_cmd} run build"
            serve_full = f"{run_cmd} run start"
        elif has_build:
            build_layer = f"{run_cmd} run build"
            serve_full = f"{serve_cmd} -s build -l {port}"
            static_serve = True
        elif has_preview:
            serve_full = f"{run_cmd} run preview --host 127.0.0.1 --port {port}"
        elif has_dev:
            serve_full = f"{run_cmd} run dev --host 127.0.0.1 --port {port}"
        elif has_start:
            serve_full = f"{run_cmd} run start"
        else:
            serve_full = f"{serve_cmd} -s . -l {port}"

    # ── Install deps ──
    _install_project_deps(project_dir)

    # ── Build ──
    if build_layer:
        build_cmd = build_layer.split()
        r = subprocess.run(
            build_cmd, capture_output=True, text=True,
            timeout=1800, cwd=str(project_dir),
        )
        # Retry without paraglide compile if it fails
        if r.returncode != 0 and "paraglide" in r.stderr.lower():
            vite_cmd_str = build_layer.replace("paraglide-js compile && ", "").replace(
                "paraglide-js compile &&", "")
            if vite_cmd_str != build_layer:
                r = subprocess.run(
                    vite_cmd_str.split(), capture_output=True, text=True,
                    timeout=1800, cwd=str(project_dir),
                )
        if r.returncode != 0:
            raise RuntimeError(f"Build failed: {r.stderr[:300]}")

        # After build, find actual output dir for static-serve cases
        if static_serve:
            build_dir = _resolve_serve_dir(project_dir, ["build", "dist", "public", "dist/renderer", "build/client"])
            if build_dir:
                serve_full = f"{serve_cmd} -s {build_dir} -l {port}"
            else:
                serve_full = f"{serve_cmd} -s . -l {port}"

    # ── Create systemd service ──
    # Set BUN_TMPDIR for Bun projects (needed with PrivateTmp=yes)
    extra_env = {}
    if (project_dir / "bun.lock").exists() or (project_dir / "bun.lockb").exists():
        bun_tmp = project_dir / ".bun-tmp"
        bun_tmp.mkdir(exist_ok=True)
        extra_env["BUN_TMPDIR"] = str(bun_tmp)
        extra_env["BUN_INSTALL"] = str(bun_tmp)

    _create_systemd_service(
        service_name=service_name,
        working_dir=str(project_dir),
        exec_start=serve_full,
        description=f"GhostProvider: {repo_url}",
        port=port,
        extra_env=extra_env if extra_env else None,
    )

    # ── Start ──
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


def _detect_node_pm(project_dir: Path) -> dict[str, str]:
    """Detect Node.js package manager from lock files.

    Returns full paths to executables so systemd services can find them.
    """
    has_bun = (project_dir / "bun.lock").exists() or (project_dir / "bun.lockb").exists()
    has_pnpm = (project_dir / "pnpm-lock.yaml").exists()
    has_yarn = (project_dir / "yarn.lock").exists()
    # Yarn 4: bundled binary in .yarn/releases/
    is_yarn4 = has_yarn and (project_dir / ".yarn" / "releases").is_dir()

    if has_bun:
        bun = shutil.which("bun")
        if bun:
            return {"run": bun, "install": f"{bun} install", "serve": f"{bun}x serve"}
    if has_pnpm:
        pnpm = shutil.which("pnpm")
        if pnpm:
            return {"run": pnpm, "install": f"{pnpm} install", "serve": f"{pnpm} dlx serve"}
    if is_yarn4:
        # Yarn 4: use bundled binary via node
        for f in (project_dir / ".yarn" / "releases").iterdir():
            if f.suffix == ".cjs" and "yarn" in f.name:
                return {"run": f"node {f}", "install": f"node {f} install --no-immutable --mode skip-build", "serve": "npx serve"}
    if has_yarn:
        yarn = shutil.which("yarn")
        if yarn:
            return {"run": yarn, "install": f"{yarn} install", "serve": "npx serve"}
    npm = shutil.which("npm") or "npm"
    npx = shutil.which("npx") or "npx"
    return {"run": npm, "install": f"{npm} install", "serve": f"{npx} serve"}


def _detect_workspaces(project_dir: Path, pkg: dict | None) -> list[str] | None:
    """Detect monorepo workspaces from package.json."""
    if not pkg:
        return None
    ws = pkg.get("workspaces")
    if isinstance(ws, list):
        return ws
    if isinstance(ws, dict):
        return ws.get("packages", [])
    return None


def _find_webapp_in_monorepo(project_dir: Path, workspaces: list[str]) -> Path | None:
    """In a monorepo, find the package most likely to be the web app."""
    candidates: list[Path] = []
    for pattern in workspaces:
        matches = glob.glob(str(project_dir / pattern), recursive=False)
        for m in matches:
            p = Path(m)
            if not p.is_dir():
                continue
            # Check if this package has a web app
            sub_pkg = _read_package_json(p)
            if not sub_pkg:
                continue
            sub_deps = {}
            sub_deps.update(sub_pkg.get("dependencies", {}))
            sub_deps.update(sub_pkg.get("devDependencies", {}))
            sub_scripts = sub_pkg.get("scripts", {})
            # Prefer packages with web framework deps or web scripts
            score = 0
            web_deps = {"react", "vue", "svelte", "@sveltejs/kit", "next", "nuxt",
                         "angular", "@angular/core", "@nestjs/core", "express", "fastify"}
            if sub_deps.keys() & web_deps:
                score += 10
            if any(s in sub_scripts for s in ("dev", "build", "start")):
                score += 5
            if (p / "src").is_dir():
                score += 3
            candidates.append((score, p))

    if candidates:
        candidates.sort(key=lambda x: x[0], reverse=True)
        return candidates[0][1]
    return None


def _install_project_deps(project_dir: Path, on_status=None) -> None:
    """Install Node.js project dependencies.

    Detects package managers from lock files and installs accordingly.
    """
    def _emit(msg: str) -> None:
        if on_status:
            on_status(msg)

    npm_timeout = 1800  # 30 minutes for heavy projects

    if (project_dir / "package.json").exists():
        # Detect Yarn 4 (bundled in .yarn/releases/)
        is_yarn4 = (project_dir / ".yarn" / "releases").is_dir()
        if is_yarn4:
            yarn4_bin = None
            for f in (project_dir / ".yarn" / "releases").iterdir():
                if f.suffix == ".cjs" and "yarn" in f.name:
                    yarn4_bin = str(f)
                    break
            if yarn4_bin:
                _emit("installing Node.js dependencies (yarn 4)...")
                try:
                    subprocess.run(
                        ["node", yarn4_bin, "install", "--no-immutable", "--mode", "skip-build"],
                        capture_output=True, text=True, timeout=npm_timeout,
                        cwd=str(project_dir),
                    )
                    _emit("Node.js dependencies installed")
                except (subprocess.TimeoutExpired, FileNotFoundError):
                    _emit("Node.js dependency install failed or timed out")
            else:
                _emit("yarn 4 binary not found, falling back to npm...")
                pm = _detect_node_pm(project_dir)
                try:
                    subprocess.run(
                        pm["install"].split(),
                        capture_output=True, text=True, timeout=npm_timeout,
                        cwd=str(project_dir),
                    )
                except (subprocess.TimeoutExpired, FileNotFoundError):
                    _emit("Node.js dependency install failed or timed out")
        else:
            pm = _detect_node_pm(project_dir)
            _emit(f"installing Node.js dependencies ({pm['run']})...")
            try:
                subprocess.run(
                    pm["install"].split(),
                    capture_output=True, text=True, timeout=npm_timeout,
                    cwd=str(project_dir),
                )
                _emit("Node.js dependencies installed")
            except (subprocess.TimeoutExpired, FileNotFoundError):
                _emit("Node.js dependency install failed or timed out")


def _auto_env(project_dir: Path) -> None:
    """Create .env from .env.example if it doesn't exist."""
    env_file = project_dir / ".env"
    env_example = project_dir / ".env.example"
    if env_file.exists() or not env_example.exists():
        return
    try:
        content = env_example.read_text()
        env_file.write_text(content)
    except OSError:
        pass
