<h1 align="center">Automated self-hosting platform</h1>

> <p align="center">demo-ghostprovider is a restricted demo build of GhostProvider that automates deployment and management of a curated set of demo services (VERT, SearXNG, Memos, the official Svelte starter template) as systemd user units.</p>

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

### Distribution trust model

- The demo ships only from this repository (GitHub + Codeberg mirror); release
  artifacts are minisign-signed with the identity documented in
  [docs/DISTRIBUTION.md](docs/DISTRIBUTION.md) (`docs/release.pub`).
- The full version never uses download-token URLs. It is distributed via
  Codeberg collaborator invites to a private repository, cloned with the
  buyer's own credentials, and installed only after signature verification —
  same discipline, same key.

## Security Model

- **All data stays local** — no telemetry. Outbound requests are locked to a compiled-in allowlist (`api.github.com`, `github.com`, `raw.githubusercontent.com`, `codeload.github.com`, `proxy.golang.org`, `storage.googleapis.com`) and to **HTTPS only**. Redirects do not bypass it: they are followed one hop at a time, and every redirect target is re-checked against the allowlist *before* a connection to it opens. Each request (each redirect hop separately) is logged to `~/.local/state/demo-ghostprovider/net.log`, and `demo-ghostprovider --show-endpoints` prints the allowlist plus this session's counters so you can verify instead of trust. `codeload.github.com` is the download host GitHub points archive requests at; `proxy.golang.org` and `storage.googleapis.com` (its signed-URL redirect) are seeded only by the Go toolchain auto-provisioner — both reachable only as explicitly re-checked hops, and their bytes are re-verified by `go` against its checksum database before running.
- **No root required** — services run as systemd user-level units
- **Explicit confirmation before deploy** — the software always asks YES/NO before hosting a service
- **Service sandboxing:**
  - `NoNewPrivileges=yes` — prevents privilege escalation
  - `ProtectHome=read-only` — no write access to home directory
  - `ProtectSystem=full` — /usr, /boot, and /etc are read-only
  - `ReadWritePaths` — restricted to the deployed project directory; caches stay inside it (`XDG_CACHE_HOME`, npm/pnpm/yarn/bun/cargo/go cache dirs are redirected to `~project/.ghost-cache`, persist between deployments so repeated builds reuse downloaded artifacts, and are removed together with the clone when the service is deleted). Go builds go further: every module zip listed in `go.sum` is pre-seeded into `$GOMODCACHE/cache/download` as parallel, resumable byte-range fetches (6 workers, `proxy.golang.org` → `storage.googleapis.com` hops, `content-range`-verified sizes), so a slow link never serializes ~200 downloads behind one socket the way `go` alone would; the bytes are still verified by `go` against its checksum database before running
  - Kernel surface locked: `ProtectKernelTunables/Modules/ControlGroups=yes`, `RestrictNamespaces`, `LockPersonality`, `RestrictRealtime`, `RestrictSUIDSGID`, empty `CapabilityBoundingSet`
  - **Credential scrub** — build steps never inherit `GITHUB_TOKEN`, `GH_TOKEN`, `NPM_TOKEN`, `NODE_AUTH_TOKEN`, `DOCKER_AUTH_CONFIG` or `BUN_AUTH_TOKEN` (a hostile build step could otherwise exfiltrate a deployment credential); deployed services get the same via `UnsetEnvironment` plus `ProtectProc=invisible`
  - **Honest degradation** — `GHOSTPROVIDER_NO_SANDBOX=1` or a missing `systemd-run` prints a `warn:` line on every deploy instead of silently weakening isolation; the same applies to `GHOSTPROVIDER_NO_NETLOG`
  - **Loopback where possible, never a silent LAN exposure** — VERT is served by our own loopback-only server and its unit gets `IPAddressAllow=127.0.0.1 ::1` (runtime egress locked to loopback). Apps that must reach the internet (SearXNG, Memos) cannot be network-locked; after start every deployment's listener address is re-checked, and a service found binding a non-loopback address prints an explicit `warn:` instead of quietly exposing the port to your LAN
  - **Threat model, stated plainly** — cloned third-party code builds and runs with your user's *read* access to `$HOME` (`ProtectHome=read-only` and `ProtectSystem=full` stop writes, not reads). Treat every deployed upstream as untrusted: never rely on files under `$HOME` being invisible to a service you host, and pin sensitive work to a dedicated user. Deployments fetch each recipe's default branch without commit pinning (TOFU), so whatever `main` moves to is what you get; review service diffs before redeploying.
  - **Deadline, not hang** — every build command runs under a timeout (900s), enforced in both the sandboxed (`RuntimeMaxSec`) and plain paths, so an untrusted build cannot wedge your user session
  - **Audit the sandbox itself** — `demo-ghostprovider --verify-sandbox` (needs `strace` on the host) runs a trivial command inside the *same* hardened unit used for deploys and inspects the syscall trace: any outbound `connect()` to a non-loopback address, or any `execve()` resolving into the project tree, is reported as a failure. It is a runtime escape-detector for the sandbox configuration, distinct from the E2E systemd check (`--selftest`).
- **Release supply chain** — the minisign secret key never lives on GitHub: releases are signed locally (`scripts/release-local.sh --sign`), the signature files are committed, and CI refuses to publish anything unsigned; `install.sh` requires the signature too (`--allow-unsigned` is the explicit opt-out)

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

Options:

```bash
... | sh -s -- --tag v0.0.15        # pin a version
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
