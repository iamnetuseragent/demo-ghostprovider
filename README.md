<h1 align="center">Automated self-hosting platform</h1>

> <p align="center">GhostProvider is an open-source platform that simplifies self-hosting</p>

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

The guarantees below are commitments, not settings: they are enforced by code
and by tests in this repository, so they hold across releases without this
document being updated. The concrete mechanisms (systemd directives in
`src/hoster/units.rs`, build-sandbox properties in `src/hoster/sandbox.rs`,
the network allowlist in `src/netlog.rs`) may evolve — they are the
implementation; the invariants below are the contract.

- **All data stays local** — no telemetry, ever. This binary's only network
  contacts are to a small allowlist compiled in at build time; every request is
  re-checked against it on each redirect hop and written to net.log. Verify
  instead of trust: `demo-ghostprovider --show-endpoints` prints the allowlist
  and this session's request counters.
- **No root required** — everything runs as systemd user-level units; no step
  in deploy, run, or cleanup ever elevates privileges.
- **Explicit confirmation before deploy** — the panel always asks a clear
  YES/NO before touching the machine; there are no silent defaults.
- **Service sandboxing** — code from an upstream you did not write runs only
  under hard isolation that cannot be opted out of or silently skipped:
  - *Mandatory build sandbox* — fetching and building run inside an isolated
    environment (no network, `$HOME` redirected to a disposable directory). If
    that isolation cannot be provided, the deploy is rejected; a build can
    never run as a plain unisolated host process, by construction.
  - *Private by default* — invoker secret roots (`~/.ssh`, `~/.config`, gpg/SSH
    agent sockets, …) are blanked from every unit, private credentials are
    scrubbed from build and service environments, and filesystem writes are
    confined to the project's `.ghost-cache`.
  - *No runtime egress* — deployed services are locked to loopback, so a
    compromised service cannot call out to the internet. Where a kernel cannot
    enforce the lock (unprivileged eBPF disabled), that is surfaced as an
    explicit warning — never silently assumed; a hard guarantee there needs a
    host firewall.
  - *Constrained and deadlined* — every unit carries per-service resource caps
    and the build has a hard deadline, so nothing untrusted can wedge the
    session or the machine.
  - *Verified, not asserted* — `--verify-sandbox` audits the sandbox under
    strace; `--selftest` proves unit generation → start → serve against the
    live systemd manager.
- **Fixed-commit builds** — each service is pinned to an exact commit SHA, so
  redeploys are reproducible and a moved upstream `main` cannot change what is
  built.
- **Signed releases** — the signing key never lives on GitHub or CI; releases
  are signed locally, signatures are committed, and CI refuses to publish
  anything unsigned.

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

One command:

```bash
curl -fsSL https://raw.githubusercontent.com/iamnetuseragent/demo-ghostprovider/main/install.sh | sh
```

`install.sh` fails closed by default: it downloads the release and its minisign
signature, verifies the signature (with system `minisign`/`rsign`, or a pinned
static minisign it fetches on demand from jedisct1/minisign) and only then
installs. A missing signature, a missing verifier, or any verification failure
aborts unless you explicitly opt out with `--allow-checksum-only`.

If you want to verify the installer script itself *before* executing it, the
installed script is also signed (`install.sh.minisig`) — see
`docs/DISTRIBUTION.md`.

## Usage

```bash
demo-ghostprovider                                  # launch the interactive panel
demo-ghostprovider --show-endpoints                 # allowlist + session request counters
demo-ghostprovider --selftest                       # E2E check against live systemd (loopback only)
demo-ghostprovider --verify-sandbox                 # audit the build sandbox under strace (needs strace)
demo-ghostprovider --version                        # print version
```

## Uninstall

`install.sh` is the single installer and uninstaller — the same signature verifier
from above covers `--uninstall`, which fully removes the binary, all demo-*
systemd user units, the deploy registry/secrets state and installed service data:

```bash
curl -fsSL https://raw.githubusercontent.com/iamnetuseragent/demo-ghostprovider/main/install.sh | sh -s -- --uninstall
```
