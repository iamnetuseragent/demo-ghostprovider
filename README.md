<h1 align="center">Automated self-hosting platform</h1>

> <p align="center">demo-ghostprovider is a restricted demo build of GhostProvider that automates deployment and management of exactly three services as systemd user units.</p>

![GHOST PROVIDER Panel](assets/GHOSTPROVIDER%20PANEL.JPEG)

## One-Click Deploy

Paste a GitHub URL — deploy one of the three supported services as a systemd service.
Private, local, no third parties.

![Demo GhostProvider](assets/demo-ghostprovider.webp)

## Requirements

- Rust toolchain via [rustup](https://rustup.rs) (the installer builds from source)
- systemd (user-level)
- git
- Linux (tested on Arch, Ubuntu, Fedora)

## Tech Stack

- Rust / [ratatui](https://github.com/ratatui/ratatui) (TUI framework)
- ureq + rustls — HTTPS client locked to a compile-time host allowlist
- systemd (user-level service management)

## Why systemd?

GhostProvider uses systemd user-level services because they provide:
- **No root required** — every user can manage their own services
- **Auto-start on login** — services survive reboots without manual config
- **Clean removal** — `systemctl --user disable` + delete unit file; demo-ghostprovider also cleans the cloned repo, secrets file, and lingering ports
- **Sandboxing** — built-in security directives (NoNewPrivileges, ProtectHome, ProtectSystem)

This is the standard on Arch, Ubuntu, Fedora, Debian, and most modern Linux distributions.

## Security Model

- **All data stays local** — no telemetry. Outbound requests are locked to a compiled-in allowlist (`api.github.com`, `github.com`, `raw.githubusercontent.com`) — every other host is refused by the HTTP client itself, redirects included. Each request is logged to `~/.local/state/demo-ghostprovider/net.log`, and `demo-ghostprovider --show-endpoints` prints the allowlist plus this session's counters so you can verify instead of trust.
- **No root required** — services run as systemd user-level units
- **Explicit confirmation before deploy** — the software always asks YES/NO before hosting a service
- **Service sandboxing:**
  - `NoNewPrivileges=yes` — prevents privilege escalation
  - `ProtectHome=read-only` — no write access to home directory
  - `ProtectSystem=full` — /usr, /boot, and /etc are read-only
  - `ReadWritePaths` — restricted to the deployed project directory; caches stay inside it (`XDG_CACHE_HOME`, npm/pnpm/yarn/bun/cargo/go cache dirs are redirected to `~project/.ghost-cache`, persist between deployments so repeated builds reuse downloaded artifacts, and are removed together with the clone when the service is deleted)
  - Kernel surface locked: `ProtectKernelTunables/Modules/ControlGroups=yes`, `RestrictNamespaces`, `LockPersonality`, `RestrictRealtime`, `RestrictSUIDSGID`, empty `CapabilityBoundingSet`

## System Scan

Scans your machine for prerequisites, detects all listening ports, fingerprints known services (VERT, SearXNG, Memos) and maps your network — gateway, DNS.

### Why System Scan?

Before deploying a new service, demo-ghostprovider checks what's already running on your machine:
- **Prerequisites** — do you have cargo, systemd, git installed?
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

## Uninstall

```bash
curl -sSL https://raw.githubusercontent.com/iamnetuseragent/demo-ghostprovider/main/installation/uninstall.sh | bash
```

This stops and removes all `demo-*` systemd units, the launcher binary, the installation directory with all deployed service data, and the state directory (including per-service secrets).
