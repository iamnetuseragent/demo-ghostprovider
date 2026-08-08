<h1 align="center">Automated self-hosting platform</h1>

> <p align="center">demo-ghostprovider is a restricted demo build of GhostProvider that automates deployment and management of exactly three services as systemd user units.</p>

![GHOST PROVIDER Panel](assets/GHOSTPROVIDER%20PANEL.JPEG)

## One-Click Deploy

Paste a GitHub URL — deploy one of the three supported services as a systemd service.
Private, local, no third parties.

![Demo GhostProvider](assets/demo-ghostprovider.webp)

## Requirements

- Python 3.10+
- systemd (user-level)
- git
- Linux (tested on Arch, Ubuntu, Fedora)

## Tech Stack

- Python 3.10+ / [Textual](https://github.com/Textualize/textual) (TUI framework)
- systemd (user-level service management)
- requests (GitHub API interaction)

## Why systemd?

GhostProvider uses systemd user-level services because they provide:
- **No root required** — every user can manage their own services
- **Auto-start on login** — services survive reboots without manual config
- **Clean removal** — `systemctl --user disable` + delete unit file; demo-ghostprovider also cleans the cloned repo, secrets file, and lingering ports
- **Sandboxing** — built-in security directives (NoNewPrivileges, ProtectHome, ProtectSystem)

This is the standard on Arch, Ubuntu, Fedora, Debian, and most modern Linux distributions.

## Security Model

- **All data stays local** — no telemetry, no external requests beyond GitHub API
- **No root required** — services run as systemd user-level units
- **Explicit confirmation before deploy** — the software always asks YES/NO before hosting a service
- **Service sandboxing:**
  - `NoNewPrivileges=yes` — prevents privilege escalation
  - `ProtectHome=read-only` — no write access to home directory
  - `ProtectSystem=full` — /usr, /boot, and /etc are read-only
  - `ReadWritePaths` — restricted to the deployed project directory; caches stay inside it (`XDG_CACHE_HOME`, npm/go/cargo/pnpm cache dirs are redirected to `~project/.ghost-cache` and removed after each build)

### Threat model (please read)

The systemd sandbox above protects the **running service**, not what happens
*before* it starts. Deploying one of the three supported services builds it
from source on your machine, and those build steps (`pip install`, `npm
install`, `cargo build`, `go build`) execute scripts shipped in the
repository **with your user's permissions**. The dangerous-command blocklist is
a safety net against accidents, not a security boundary.

Build steps run sandboxed by default inside a hardened `systemd-run --user`
transient unit (`NoNewPrivileges=yes`, `ProtectSystem=strict`,
`ProtectHome=read-only`, `PrivateTmp=yes`, `PrivateDevices=yes`, empty
capability set; read-write only in the project directory, and all tool caches
(pip, npm/yarn/bun, cargo, go, pnpm, TMPDIR) are redirected to
`<project>/.ghost-cache` and removed when the build finishes, so nothing is
written to your `~/.cache`, `~/.npm`, or `~/.cargo`). Set
the environment variable `GHOSTPROVIDER_NO_SANDBOX=1` to opt out per
invocation. When `systemd-run` is unavailable, execution falls back to direct
execution with a warning. The sandbox still runs as the same user. For
anything untrusted, run this tool in a dedicated user, VM, or container.

## System Scan

Scans your machine for prerequisites, detects all listening ports, fingerprints known services (VERT, SearXNG, Memos) and maps your network — gateway, DNS.

### Why System Scan?

Before deploying a new service, demo-ghostprovider checks what's already running on your machine:
- **Prerequisites** — do you have Python, systemd, git installed?
- **Listening ports** — which ports are already in use?
- **Known services** — is SearXNG, Memos, or VERT already running?

This avoids port conflicts and helps GhostProvider choose the right deployment strategy. All data stays on your machine — nothing is sent anywhere.

## Control panel

Full dashboard for all deployed services. Start, stop, restart, or remove — one click cleans the service, unit file, cloned repo, secrets file, and lingering ports. GhostProvider cleans up the resources it manages; applications may still leave their own state (databases, caches, external sockets) elsewhere.

## Service support

This is a restricted demo version of GhostProvider that only supports deploying the following services:

- **VERT** - https://github.com/VERT-sh/VERT
- **SearXNG** - https://github.com/searxng/searxng
- **Memos** - https://github.com/usememos/memos

## Quick Start (Linux)

```bash
curl -sSL https://raw.githubusercontent.com/iamnetuseragent/demo-ghostprovider/main/installation/install.sh | bash
```

## Uninstall

```bash
curl -sSL https://raw.githubusercontent.com/iamnetuseragent/demo-ghostprovider/main/installation/uninstall.sh | bash
```

## Install (Arch Linux)

```bash
git clone https://github.com/iamnetuseragent/demo-ghostprovider.git
cd demo-ghostprovider
makepkg -si
```

## Usage

```bash
demo-ghostprovider
```
