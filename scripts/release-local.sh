#!/bin/sh
# Local reproducible release build: static musl binary + SHA256SUMS,
# optionally minisign-signed. Produces byte-for-byte what release.yml
# builds in CI — run both and compare `sha256sum` to audit the pipeline.
#
# Usage:
#   scripts/release-local.sh            build + checksums into dist/
#   scripts/release-local.sh --sign     sign dist/SHA256SUMS and stage
#                                       release/SHA256SUMS (+ .minisig)
#                                       for commit
#
# Local-only signing policy: the release secret key NEVER lives on GitHub.
# `--sign` also writes the signed checksums into release/ so they can be
# committed; CI then REQUIRES them (release.yml "Verify committed
# signature") and never publishes an unsigned release.
#
# Signing key default: ~/.config/demo-ghostprovider/release.key
# (generate once with scripts/keygen-release.sh)
#
# Requirements: podman (or docker; adjust below), git tag for VERSION, minisign (with --sign).
set -eu

# Same pinned digest as scripts/build-musl.sh and .github/workflows/release.yml.
# Bump all three together, consciously, and record it in the commit message.
IMAGE="ghcr.io/archlinux/archlinux@sha256:f0e768f473fdef45e43f0d33edd2138bd6f72fd59039f202870a5ed9367497e5"
CACHE_DIR="${XDG_CACHE_HOME:-$HOME/.cache}/demo-ghostprovider-build/cargo"
BIN_NAME="demo-ghostprovider"
KEY="${GHOSTPROVIDER_RELEASE_KEY:-${HOME}/.config/demo-ghostprovider/release.key}"
SIGN=0
[ "${1:-}" = "--sign" ] && SIGN=1

VERSION="$(git describe --tags --abbrev=0 2>/dev/null || echo dev)"
ART="${BIN_NAME}-${VERSION}-x86_64-linux-musl"

mkdir -p "$CACHE_DIR"
podman run --rm \
    -v "$(pwd)":/src:Z \
    -v "$CACHE_DIR":/cargo:Z \
    -e CARGO_HOME=/cargo \
    -w /src \
    "$IMAGE" sh -c '
        set -eu
        # -Syu and the pinned rustc must match scripts/build-musl.sh and
        # .github/workflows/release.yml byte-for-byte. Bump all three
        # together, consciously.
        pacman -Syu --noconfirm --needed rustup musl >/dev/null
        rustup default 1.98.0 >/dev/null 2>&1
        rustup target add x86_64-unknown-linux-musl >/dev/null 2>&1
        cargo build --release --locked --target x86_64-unknown-linux-musl
    '

mkdir -p dist
cp "target/x86_64-unknown-linux-musl/release/${BIN_NAME}" "dist/${ART}"
( cd dist && sha256sum "${ART}" > SHA256SUMS && sha256sum -c SHA256SUMS )
echo "artifact: dist/${ART}"

if [ "$SIGN" -eq 1 ]; then
    if [ ! -f "$KEY" ]; then
        echo "error: no signing key at $KEY" >&2
        echo "       generate one first: scripts/keygen-release.sh" >&2
        exit 1
    fi
    # Local signing is interactive by design: the passphrase never leaves
    # this machine and is not stored anywhere.
    minisign -Sm "dist/SHA256SUMS" -s "$KEY"
    echo "signed:   dist/SHA256SUMS.minisig"

    # Stage the signed checksums for commit so CI can both verify the
    # signature and compare it against its own byte-identical build.
    mkdir -p release
    cp dist/SHA256SUMS dist/SHA256SUMS.minisig release/
    echo "staged:   release/SHA256SUMS, release/SHA256SUMS.minisig"
    echo "          -> commit them and push the tag (CI will refuse an unsigned release)"
fi
