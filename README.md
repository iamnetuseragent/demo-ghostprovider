<h1 align="center">Automated self-hosting platform</h1>

> <p align="center">demo-ghostprovider is a restricted demo build of GhostProvider that automates deployment and management of exactly three services as systemd user units.</p>

![GHOST PROVIDER Panel](assets/GHOSTPROVIDER%20PANEL.JPEG)

## One-Click Deploy

Paste a GitHub URL — deploy one of the three supported services as a systemd service.
Private, local, no third parties.

![Demo GhostProvider](assets/demo-experience.gif)

## Requirements

- SystemD (user-level)
- GIT
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

### Distribution trust model

- The demo ships only from this repository (GitHub + Codeberg mirror); release
  artifacts are minisign-signed with the identity documented in
  [docs/DISTRIBUTION.md](docs/DISTRIBUTION.md) (`docs/release.pub`).
- The full version never uses download-token URLs. It is distributed via
  Codeberg collaborator invites to a private repository, cloned with the
  buyer's own credentials, and installed only after signature verification —
  same discipline, same key.

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

Scans your machine for prerequisites and maps occupied ports with their owning processes — nothing more. Deliberately: no VPN detection, no service fingerprinting, so the report stays useless to anyone but you.

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

## Install

One line — static binary, verified, ready to run:

```bash
curl -sSL https://raw.githubusercontent.com/iamnetuseragent/demo-ghostprovider/main/install.sh | sh
```

Options:

```bash
... | sh -s -- --tag v0.0.14        # pin a version
... | sh -s -- --mirror codeberg    # prefer the Codeberg mirror
... | sh -s -- --uninstall          # remove the binary
```

## Install (Arch Linux)

Build from source with pacman managing the files:

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

Static-binary install:

```bash
curl -sSL https://raw.githubusercontent.com/iamnetuseragent/demo-ghostprovider/main/install.sh | sh -s -- --uninstall
```

Source install (`installation/install.sh`) — full cleanup, including all
deployed service data:

```bash
curl -sSL https://raw.githubusercontent.com/iamnetuseragent/demo-ghostprovider/main/installation/uninstall.sh | bash
```
