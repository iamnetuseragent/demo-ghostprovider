"""systemd unit file reading helpers."""

import os


def _read_unit_file(unit_file: str) -> dict[str, str]:
    """Parse a systemd unit file and return key=value pairs from [Service] section."""
    props: dict[str, str] = {}
    try:
        with open(unit_file, encoding="utf-8") as f:
            in_service = False
            for line in f:
                line = line.strip()
                if line == "[Service]":
                    in_service = True
                    continue
                if line.startswith("[") and line.endswith("]"):
                    in_service = False
                    continue
                if in_service and "=" in line:
                    key, _, value = line.partition("=")
                    props[key.strip()] = value.strip()
    except OSError:
        pass
    return props


def _extract_working_dir(unit_file: str) -> str | None:
    """Extract WorkingDirectory from a unit file."""
    props = _read_unit_file(unit_file)
    wd = props.get("WorkingDirectory", "")
    if wd:
        return os.path.expanduser(wd)
    return None


def get_service_unit_content(unit_name: str) -> str | None:
    """Read the systemd unit file content."""
    # Check system unit files first
    paths = [
        f"/etc/systemd/system/{unit_name}.service",
        os.path.expanduser(f"~/.config/systemd/user/{unit_name}.service"),
    ]

    for path in paths:
        if os.path.isfile(path):
            try:
                with open(path, encoding="utf-8") as f:
                    return f.read()
            except OSError:
                pass
    return None
