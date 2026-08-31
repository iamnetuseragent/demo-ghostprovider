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
#
# Default (reproducible): installs rustup and downloads the PINNED rustc
#     1.98.0 fresh into a clean /cargo inside the container, so the result is
#     byte-identical to what release.yml builds in CI. On a throttled link the
#     initial toolchain download can take a while.
#
#   --fast : reuse THIS host's rustup toolchain + cargo cache by mounting them
#     read-only into the container (the host here already runs the exact
#     pinned rustc 1.98.0, so output is equivalent, just far quicker). Use for
#     routine local iteration; use the default form to audit reproducibility.

set -eu

IMAGE="ghcr.io/archlinux/archlinux@sha256:f0e768f473fdef45e43f0d33edd2138bd6f72fd59039f202870a5ed9367497e5"
CACHE_DIR="${XDG_CACHE_HOME:-$HOME/.cache}/demo-ghostprovider-build/cargo"
mkdir -p "$CACHE_DIR"

FAST=0
[ "${1:-}" = "--fast" ] && FAST=1

if [ "$FAST" -eq 1 ]; then
    # Host reuse path: the container installs only musl (small) and builds with
    # the host's rustc/cargo, which already match the pinned 1.98.0 here.
    podman run --rm \
        -v "$(pwd)":/src:Z \
        -v "${RUSTUP_HOME:-$HOME/.rustup}":/rustup:Z \
        -v "$HOME/.cargo":/cargo:Z \
        -e RUSTUP_HOME=/rustup \
        -e CARGO_HOME=/cargo \
        -w /src \
        "$IMAGE" sh -c '
            set -eu
            pacman -Syu --noconfirm --needed musl >/dev/null
            PATH="/rustup/toolchains/1.98.0-x86_64-unknown-linux-gnu/bin:$PATH" \
            cargo build --release --locked --target x86_64-unknown-linux-musl
        '
else
    podman run --rm \
        -v "$(pwd)":/src:Z \
        -v "$CACHE_DIR":/cargo:Z \
        -e CARGO_HOME=/cargo \
        -w /src \
        "$IMAGE" sh -c '
            set -eu
            # -Syu and a PINNED rustc must match scripts/release-local.sh and
            # .github/workflows/release.yml byte-for-byte. Bump all together,
            # consciously — `stable` here would silently diverge from CI.
            pacman -Syu --noconfirm --needed rustup musl >/dev/null
            rustup default 1.98.0 >/dev/null 2>&1
            rustup target add x86_64-unknown-linux-musl >/dev/null 2>&1
            cargo build --release --locked --target x86_64-unknown-linux-musl
        '
fi

BIN="target/x86_64-unknown-linux-musl/release/demo-ghostprovider"
file "$BIN"
sha256sum "$BIN"
