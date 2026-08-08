"""Runtime data locations — deployed services and binaries live outside the install dir."""

import logging
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger("demo_ghostprovider.paths")

HOME = Path.home()
DATA_DIR = HOME / ".local/share/demo-ghostprovider-data"
SERVICES_DIR = DATA_DIR / "services"
BIN_DIR = DATA_DIR / "bin"

_LEGACY_DATA_DIR = HOME / ".local/share/demo-ghostprovider"
_LEGACY_SERVICES_DIR = _LEGACY_DATA_DIR / "services"
_LEGACY_BIN_DIR = _LEGACY_DATA_DIR / "bin"
_STATE_FILE = HOME / ".config" / "demo-ghostprovider" / "state.json"
_SYSTEMD_USER_DIR = HOME / ".config" / "systemd" / "user"


def ensure_dirs() -> None:
    """Create the runtime data directories if missing."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SERVICES_DIR.mkdir(parents=True, exist_ok=True)
    BIN_DIR.mkdir(parents=True, exist_ok=True)


def migrate_legacy_data() -> None:
    """Move runtime data out of the install dir into the dedicated data dir.

    Idempotent. Also rewrites clone paths in the state file and ghost-* unit
    files so existing deployments keep working after the move.
    """
    try:
        _migrate_dirs()
        _migrate_state()
        _migrate_unit_files()
    except OSError as e:
        logger.warning("legacy data migration skipped: %s", e)


def _migrate_dirs() -> None:
    moved = False
    if not SERVICES_DIR.exists() and _LEGACY_SERVICES_DIR.is_dir():
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        shutil.move(str(_LEGACY_SERVICES_DIR), str(SERVICES_DIR))
        moved = True
    if not BIN_DIR.exists() and _LEGACY_BIN_DIR.is_dir():
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        shutil.move(str(_LEGACY_BIN_DIR), str(BIN_DIR))
        moved = True
    if moved:
        logger.info("moved runtime data to %s", DATA_DIR)


def _migrate_state() -> None:
    from demo_ghostprovider import state as state_mod

    st = state_mod.load()
    changed = False
    old_base = str(_LEGACY_SERVICES_DIR)
    new_base = str(SERVICES_DIR)
    for key, entry in st.items():
        if key == "version":
            continue
        if isinstance(entry, dict):
            clone_path = entry.get("clone_path")
            if isinstance(clone_path, str) and clone_path.startswith(old_base):
                entry["clone_path"] = new_base + clone_path[len(old_base):]
                changed = True
    if changed:
        state_mod.save(st)


def _migrate_unit_files() -> None:
    if not _SYSTEMD_USER_DIR.is_dir():
        return
    replacements = {
        str(_LEGACY_SERVICES_DIR): str(SERVICES_DIR),
        str(_LEGACY_BIN_DIR): str(BIN_DIR),
    }
    changed_any = False
    for unit in sorted(_SYSTEMD_USER_DIR.glob("ghost-*.service")):
        try:
            content = unit.read_text()
        except OSError:
            continue
        new_content = content
        for old, new in replacements.items():
            new_content = new_content.replace(old, new)
        if new_content != content:
            try:
                unit.write_text(new_content)
                changed_any = True
            except OSError as e:
                logger.warning("could not rewrite %s: %s", unit, e)
    if changed_any:
        subprocess.run(
            ["systemctl", "--user", "daemon-reload"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
