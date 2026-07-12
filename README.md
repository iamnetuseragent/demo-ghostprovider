# Demo GhostProvider

A demo version of GhostProvider with limited service support.

## What is this

This is a restricted demo version of GhostProvider that only supports deploying the following services:

- **VERT** - https://github.com/VERT-sh/VERT
- **SearXNG** - https://github.com/searxng/searxng
- **Memos** - https://github.com/usememos/memos

All other repositories will be rejected with a clear error message.

## Install

```bash
cd demo-ghostprovider
python3 -m venv .venv
.venv/bin/pip install .
ln -s "$(pwd)/.venv/bin/demo-ghostprovider" ~/.local/bin/demo-ghostprovider
```

## Usage

```bash
demo-ghostprovider
```

## Features

- Full TUI interface with cyberpunk theme
- System scanning and analysis
- Service management (start, stop, restart, remove)
- Only 3 allowed repositories can be deployed
- All other GhostProvider features remain intact

## Differences from full version

- Restricted to 3 specific repositories
- Config stored in `~/.config/demo-ghostprovider/`
- Entry point: `demo-ghostprovider`

## License

MIT
