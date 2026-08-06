<h1 align="center">Automated self-hosting platform</h1>

> <p align="center">GhostProvider is an open-source platform that simplifies self-hosting by automating deployment, service management, discovery, and clean removal.</p>

![GHOST PROVIDER Panel](assets/GHOSTPROVIDER%20PANEL.JPEG)

## One-Click Deploy

Paste a GitHub URL — get a host score — deploy as a systemd service.
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
- **Clean removal** — `systemctl --user disable` + delete unit file = zero leftovers
- **Sandboxing** — built-in security directives (NoNewPrivileges, ProtectHome, ProtectSystem)

This is the standard on Arch, Ubuntu, Fedora, Debian, and most modern Linux distributions.

## Security Model

- **All data stays local** — no telemetry, no external requests beyond GitHub API
- **No root required** — services run as systemd user-level units
- **Explicit confirmation before deploy** — the software always asks YES/NO before hosting a service
- **Service sandboxing:**
  - `NoNewPrivileges=yes` — prevents privilege escalation
  - `ProtectHome=read-only` — no write access to home directory
  - `ProtectSystem=read-only` — read-only filesystem except working directory
  - `ReadWritePaths` — restricted to deployed project directory only

## System Scan

Scans your machine for prerequisites, detects all listening ports, fingerprints known services (VERT, SearXNG, Memos...) and maps your network — gateway, DNS.

### Why System Scan?

Before deploying a new service, GhostProvider checks what's already running on your machine:
- **Prerequisites** — do you have Python, systemd, git installed?
- **Listening ports** — which ports are already in use?
- **Known services** — is SearXNG, Memos, or another app already running?

This avoids port conflicts and helps GhostProvider choose the right deployment strategy. All data stays on your machine — nothing is sent anywhere.

## Control panel

Full dashboard for all deployed services. Start, stop, restart, or remove — one click cleans the service, unit file, cloned repo, and lingering ports. Zero leftovers.

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
makepkg -si -p installation/PKGBUILD
```

## Usage

```bash
demo-ghostprovider
```
