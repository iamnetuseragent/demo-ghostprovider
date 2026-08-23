# demo-ghostprovider (Rust edition)

Local-first demo hosting panel. Deploys three curated self-hostable services
as hardened systemd **user** services from a terminal UI:

| Service | Source |
|---|---|
| VERT (file converter) | `VERT-sh/VERT` |
| SearXNG (metasearch) | `searxng/searxng` |
| Memos (note taking) | `usememos/memos` |

This is a ground-up rewrite of the Python demo-ghostprovider in Rust,
driven by an independent code audit. The legacy Python implementation is
preserved, frozen and unmaintained, in the [`archive/python`](https://github.com/iamnetuseragent/demo-ghostprovider/tree/archive/python)
branch of this same repository — full git history included.

## Requirements

- Linux with a working systemd **user session**
  (`systemctl --user is-system-running` must not report `offline`)
- git, cargo (see [rustup](https://rustup.rs)); no Python needed anymore

## Install

```sh
git clone https://github.com/iamnetuseragent/demo-ghostprovider.git \
  || git clone https://codeberg.org/iamnetuseragent/demo-ghostprovider.git
cd demo-ghostprovider
./installation/install.sh
```

The installer clones from GitHub and falls back to the Codeberg mirror.
It never asks for credentials and never reads `/dev/tty`.

## Transparency

This program makes exactly three kinds of outbound network contacts:

1. `api.github.com` — repo metadata during scan/deploy
2. `github.com` — `git clone` of the service you chose to deploy
3. `raw.githubusercontent.com` — fetching build files for analysis

That list is **compiled into the binary** (`src/hoster/httpclient.rs`) and the
HTTP client refuses every other host — including redirects to them. Loopback
health checks against your own services are separate (`LOCAL_ENDPOINTS`) and
are never used for API calls.

Verify without trusting this README:

```sh
demo-ghostprovider --show-endpoints   # print the compiled-in allowlist + session counters
tail -f ~/.local/state/demo-ghostprovider/net.log   # every request, logged locally
GHOSTPROVIDER_NO_NETLOG=1 demo-ghostprovider          # disable local logging if you want
```

A test (`tests/pin_allowlist.rs`) fails CI if anyone adds an endpoint without
updating the documented allowlist.

## Honest threat model — read before trusting any "privacy tool"

What this software actually protects:

- your machine from services running as root (everything runs as your user,
  in `systemd-nspawn` isolation where available)
- you from silent telemetry *by this program*: deny-by-default networking +
  local audit log make that checkable instead of believable

What it does **not** protect:

- **the deployed services themselves** still phone home if their authors made
  them do so (analytics inside VERT/SearXNG/Memos is out of our control);
  run them on a VLAN or with an egress firewall if you care
- **your identity at the network layer**: your ISP / VPN provider sees the
  connections; this tool does not route traffic through anything
- **compromised upstream sources**: we pin recipes to known repos but cannot
  audit upstream code you choose to deploy

Anyone claiming more than this is selling you something.

## Development

```sh
make build    # debug build
make test     # unit tests (30)
make clippy   # lint gate, zero warnings expected
make musl     # reproducible static-pie release (pinned container image)
./target/debug/demo-ghostprovider --selftest   # live-systemd E2E, loopback only
```

The static release build runs inside a digest-pinned Arch container image;
the pin lives in `scripts/build-musl.sh` and is bumped consciously.
Empirical notes on systemd unit semantics discovered while building the
deploy pipeline are in [`docs/FINDINGS-systemd-stand.md`](docs/FINDINGS-systemd-stand.md).

## License

See [LICENSE](LICENSE).
