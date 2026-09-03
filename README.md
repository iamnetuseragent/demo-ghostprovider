<h1 align="center">Automated self-hosting platform</h1>

> <p align="center">GhostProvider is an open-source platform that simplifies self-hosting by automating deployment, service management, discovery, and clean removal.</p>

![GHOST PROVIDER Panel](assets/GHOSTPROVIDER%20PANEL.JPEG)

## One-Click Deploy

Paste a GitHub URL — deploy one of the three supported services as a systemd service.
Private, local, no third parties.

![Demo GhostProvider](assets/demo-experience.gif)

## Requirements

- SystemD (user-level)
- Git
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

- **All data stays local** — no telemetry. Outbound requests are locked to a compiled-in HTTPS allowlist (`api.github.com`, `github.com`, `raw.githubusercontent.com`, `codeload.github.com`, `proxy.golang.org`, `storage.googleapis.com`), and every redirect hop is re-checked against it before a connection opens. Every request is logged to net.log; `demo-ghostprovider --show-endpoints` prints the allowlist and session counters so you can verify instead of trust.
- **No root required** — services run as systemd user-level units
- **Explicit confirmation before deploy** — always asks YES/NO first
- **Service sandboxing:**
  - `NoNewPrivileges=yes`; `ProtectHome=read-only`; `ProtectSystem=full` (/usr, /boot, /etc read-only)
  - `ReadWritePaths` restricted to the project directory — caches stay inside `.ghost-cache` and are deleted with the service
  - **Offline build** — dependencies are pre-fetched before the sandboxed build (Go module zips, pip wheelhouse, bun/pnpm stores), which then runs under `PrivateNetwork=yes`; downloaded code never executes during fetching (pip `--only-binary`, bun/pnpm skip lifecycle scripts)
  - Kernel locked: `ProtectKernelTunables/Modules/ControlGroups`, `RestrictNamespaces`, `LockPersonality`, `RestrictRealtime/SUIDSGID`, empty `CapabilityBoundingSet`
  - **Credential scrub** — builds and services never inherit `GITHUB_TOKEN`, `GH_TOKEN`, `NPM_TOKEN`, `NODE_AUTH_TOKEN`, `BUN_AUTH_TOKEN`, `DOCKER_AUTH_CONFIG`
  - **`$HOME` redirected** in the build sandbox to `.ghost-cache/home`, so build code cannot read `~/.ssh`, `~/.netrc` or `~/.config`
  - **No silent weakening** — a missing sandbox/`netlog` prints an explicit `warn:`; a non-root panel can't silently drop to a dedicated build user
  - **Loopback where possible** — VERT is loopback-only; a service binding a non-loopback port prints an explicit `warn:` instead of quietly exposing your LAN
  - **Deadline** — every build command runs under a 900s timeout, so an untrusted build can't wedge your session
  - **Auditable** — `--verify-sandbox` detects sandbox escapes under strace; `--selftest` is the E2E systemd check
- **Fixed-commit builds** — each service is pinned to an exact commit SHA, so redeploys are reproducible and a moved `main` can't silently change what you build
- **Release supply chain** — the minisign secret key never lives on GitHub; releases are signed locally, signatures are committed, and CI refuses to publish anything unsigned

## System Scan

Scans your machine for prerequisites and maps occupied ports with their owning processes — nothing more. Deliberately: no VPN detection, no service fingerprinting, so the report stays useless to anyone but you. "Network" is measured with the same allowlisted, net.log-recorded HTTPS GET to github.com the fetches use — never ICMP ping or raw DNS.

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

## Install (Arch Linux)

Build from source with pacman managing the files:

```bash
git clone https://github.com/iamnetuseragent/demo-ghostprovider.git
cd demo-ghostprovider
makepkg -si
```

## Usage

```bash
demo-ghostprovider                                  # launch the interactive panel
demo-ghostprovider --show-endpoints                 # allowlist + session request counters
demo-ghostprovider --selftest                       # E2E check against live systemd (loopback only)
demo-ghostprovider --verify-sandbox                 # audit the build sandbox under strace (needs strace)
demo-ghostprovider --version                        # print version
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
