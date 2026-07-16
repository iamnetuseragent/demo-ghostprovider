![GHOST PROVIDER Panel](GHOSTPROVIDER%20PANEL.JPEG)

> TUI for self-hosting & localhost management.
> Your data stays yours — local, private, under your control.

## One-Click Deploy

Paste a GitHub URL — get a host score — deploy as a systemd service.
Private, local, no third parties.

![Demo GhostProvider](demo-ghostprovider.png)

## System Scan

Scans your machine for prerequisites, detects all listening ports, fingerprints 40+ known services (Jellyfin, SearXNG, Grafana, Nextcloud, Gitea, Vaultwarden...) and maps your network — gateway, DNS.

## Control panel

Full dashboard for all deployed services. Start, stop, restart, or remove — one click cleans the service, unit file, cloned repo, and lingering ports. Zero leftovers.

## Service support

This is a restricted demo version of GhostProvider that only supports deploying the following services:

- **VERT** - https://github.com/VERT-sh/VERT
- **SearXNG** - https://github.com/searxng/searxng
- **Memos** - https://github.com/usememos/memos

## Quick Start (Linux)

```bash
curl -sSL https://raw.githubusercontent.com/iamnetuseragent/demo-ghostprovider/main/install.sh | bash
```

## Uninstall

```bash
curl -sSL https://raw.githubusercontent.com/iamnetuseragent/demo-ghostprovider/main/uninstall.sh | bash
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
