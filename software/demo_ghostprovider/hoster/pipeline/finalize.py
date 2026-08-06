"""Post-deploy cleanup: relocate compiled binaries, move temp dirs, remove services."""

import os
import shutil
import subprocess
from pathlib import Path
from typing import Callable

from demo_ghostprovider.hoster.models import RepoAnalysis
from demo_ghostprovider.hoster.pipeline.analyze import _sanitize_dirname


def _relocate_compiled_binary(analysis: RepoAnalysis, service_name: str,
                               strategy: str, project_dir: Path,
                               on_status: Callable[[str], None] | None = None) -> None:
    """Copy the compiled binary to a managed bin dir and remove source files."""
    def _emit(msg: str) -> None:
        if on_status:
            on_status(msg)

    bin_dir = os.path.expanduser("~/.local/share/demo-ghostprovider/bin")
    service_bin_dir = os.path.join(bin_dir, service_name)
    os.makedirs(service_bin_dir, exist_ok=True)

    binary_path = None
    if strategy == "Go":
        candidates = [
            project_dir / "ghost-server",
            project_dir / "server",
            project_dir / "app",
        ]
        for c in candidates:
            if c.is_file() and os.access(c, os.X_OK):
                binary_path = c
                break
        if not binary_path:
            from demo_ghostprovider.hoster.strategies.go import _find_existing_go_binary
            found = _find_existing_go_binary(project_dir)
            if found:
                binary_path = Path(found)
    elif strategy == "Rust":
        release_dir = project_dir / "target" / "release"
        if release_dir.is_dir():
            for f in release_dir.iterdir():
                if f.is_file() and not f.name.endswith(".d"):
                    try:
                        content = f.read_bytes()
                        if b"ELF" in content[:20]:
                            binary_path = f
                            break
                    except OSError:
                        continue

    if not binary_path:
        _emit("compiled binary not found, keeping full project directory")
        managed_base = os.path.expanduser("~/.local/share/demo-ghostprovider/services")
        _finalize_temp_dir(analysis, service_name, permanent_base=managed_base, on_status=on_status)
        return

    _emit(f"installing binary to {service_bin_dir}...")
    dest = os.path.join(service_bin_dir, binary_path.name)
    try:
        shutil.copy2(str(binary_path), dest)
        os.chmod(dest, 0o755)
    except OSError as e:
        _emit(f"binary copy failed: {e}, keeping full project")
        managed_base = os.path.expanduser("~/.local/share/demo-ghostprovider/services")
        _finalize_temp_dir(analysis, service_name, permanent_base=managed_base, on_status=on_status)
        return

    # Update systemd unit to point to managed binary
    unit_file = os.path.expanduser(f"~/.config/systemd/user/{service_name}.service")
    if os.path.isfile(unit_file):
        try:
            content = Path(unit_file).read_text()
            old_bin = str(binary_path)
            new_bin = dest
            content = content.replace(old_bin, new_bin)
            content = content.replace(str(project_dir), service_bin_dir)
            Path(unit_file).write_text(content)
            subprocess.run(
                ["systemctl", "--user", "daemon-reload"],
                capture_output=True, text=True, timeout=10,
            )
            subprocess.run(
                ["systemctl", "--user", "restart", service_name],
                capture_output=True, text=True, timeout=30,
            )
        except OSError:
            pass

    # Update state
    from demo_ghostprovider.state import register as _register_state
    _register_state(service_name, dest, analysis.url)

    # Clean up source files
    _emit("removing source files...")
    if analysis.clone_path and os.path.isdir(analysis.clone_path):
        shutil.rmtree(analysis.clone_path, ignore_errors=True)
    if analysis._temp_base and os.path.isdir(analysis._temp_base):
        shutil.rmtree(analysis._temp_base, ignore_errors=True)
        analysis._temp_base = None
    analysis.clone_path = dest


def _finalize_temp_dir(analysis: RepoAnalysis, service_name: str,
                       permanent_base: str = "",
                       on_status: Callable[[str], None] | None = None) -> None:
    """Move project from temp dir to a permanent location after successful deploy.

    Updates the systemd unit file to reference the new paths, restarts
    the service, and removes the temp directory.
    """
    if not analysis._temp_base or not analysis.clone_path:
        return

    def _emit(msg: str) -> None:
        if on_status:
            on_status(msg)

    if not permanent_base:
        permanent_base = os.path.expanduser("~/localhosts")
    os.makedirs(permanent_base, exist_ok=True)
    safe_name = _sanitize_dirname(analysis.name)
    final_dir = os.path.join(permanent_base, safe_name)

    # If permanent dir already exists with a different clone, back it up
    if os.path.isdir(final_dir) and os.path.abspath(final_dir) != os.path.abspath(analysis.clone_path):
        backup = final_dir + ".old"
        if os.path.isdir(backup):
            shutil.rmtree(backup, ignore_errors=True)
        os.rename(final_dir, backup)

    _emit(f"moving project to {permanent_base}...")
    try:
        os.rename(analysis.clone_path, final_dir)
    except OSError:
        # Cross-device move fallback
        shutil.copytree(analysis.clone_path, final_dir, dirs_exist_ok=True)
        shutil.rmtree(analysis.clone_path, ignore_errors=True)

    # Update systemd unit file paths
    unit_file = os.path.expanduser(f"~/.config/systemd/user/{service_name}.service")
    if os.path.isfile(unit_file):
        try:
            content = Path(unit_file).read_text()
            old_base = os.path.abspath(analysis.clone_path)
            new_base = os.path.abspath(final_dir)
            content = content.replace(old_base, new_base)
            Path(unit_file).write_text(content)

            subprocess.run(
                ["systemctl", "--user", "daemon-reload"],
                capture_output=True, text=True, timeout=10,
            )
            subprocess.run(
                ["systemctl", "--user", "restart", service_name],
                capture_output=True, text=True, timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired, FileNotFoundError):
            pass

    # Patch any .env files that still reference the old temp dir path
    old_base = os.path.abspath(analysis.clone_path)
    new_base = os.path.abspath(final_dir)
    if old_base != new_base:
        for env_name in (".env", ".env.local", ".env.production"):
            env_path = os.path.join(final_dir, env_name)
            if os.path.isfile(env_path):
                try:
                    env_content = Path(env_path).read_text()
                    if old_base in env_content:
                        env_content = env_content.replace(old_base, new_base)
                        Path(env_path).write_text(env_content)
                except OSError:
                    pass

    # Update analysis
    analysis.clone_path = final_dir

    # Update state registry with new path
    from demo_ghostprovider.state import register as _register_state
    _register_state(service_name, final_dir, analysis.url)

    # Clean up temp dir
    _emit("cleaning up temp directory...")
    try:
        shutil.rmtree(analysis._temp_base, ignore_errors=True)
    except OSError:
        pass
    analysis._temp_base = None


def cleanup(analysis: RepoAnalysis, service_names: list[str] | None = None) -> None:
    """Clean up services and clone directory."""
    if service_names:
        for service_name in service_names:
            try:
                subprocess.run(
                    ["systemctl", "--user", "stop", service_name],
                    capture_output=True, text=True, timeout=10,
                )
                subprocess.run(
                    ["systemctl", "--user", "disable", service_name],
                    capture_output=True, text=True, timeout=10,
                )
                unit_file = os.path.expanduser(f"~/.config/systemd/user/{service_name}.service")
                if os.path.isfile(unit_file):
                    os.remove(unit_file)
            except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
                pass

        try:
            subprocess.run(
                ["systemctl", "--user", "daemon-reload"],
                capture_output=True, text=True, timeout=10,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass

    if analysis.clone_path and os.path.isdir(analysis.clone_path):
        shutil.rmtree(analysis.clone_path, ignore_errors=True)

    # Also clean up temp dir if it exists
    if analysis._temp_base and os.path.isdir(analysis._temp_base):
        shutil.rmtree(analysis._temp_base, ignore_errors=True)
        analysis._temp_base = None
