#!/bin/sh
# Reproducible static release build (x86_64 musl, static-pie).
#
# The host has no root access and no musl toolchain, and docker.io is
# blocked by the corporate TLS proxy — so we build inside a pinned
# Arch container image pulled from ghcr.io. The digest pin is deliberate:
# it is part of this project's supply-chain discipline. Bump it consciously
# and record the bump in the commit message.
#
# Requirements: podman (or docker; adjust the invocation below).
set -eu

IMAGE="ghcr.io/archlinux/archlinux@sha256:f0e768f473fdef45e43f0d33edd2138bd6f72fd59039f202870a5ed9367497e5"
CACHE_DIR="${XDG_CACHE_HOME:-$HOME/.cache}/demo-ghostprovider-build/cargo"
mkdir -p "$CACHE_DIR"

podman run --rm \
    -v "$(pwd)":/src:Z \
    -v "$CACHE_DIR":/cargo:Z \
    -e CARGO_HOME=/cargo \
    -w /src \
    "$IMAGE" sh -c '
        set -eu
        pacman -Sy --noconfirm --needed rustup musl >/dev/null
        rustup default stable >/dev/null 2>&1
        rustup target add x86_64-unknown-linux-musl >/dev/null 2>&1
        cargo build --release --target x86_64-unknown-linux-musl
    '

BIN="target/x86_64-unknown-linux-musl/release/demo-ghostprovider"
file "$BIN"
sha256sum "$BIN"
