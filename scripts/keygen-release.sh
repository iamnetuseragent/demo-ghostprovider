#!/bin/sh
# One-time setup: generate the minisign keypair used to sign release
# artifacts (demo and paid distribution share this single identity).
#
# After running:
#   1. copy ~/.config/demo-ghostprovider/release.pub -> docs/release.pub
#      in BOTH repositories (demo + private) and commit it;
#   2. put the fingerprint (first line of release.pub) into
#      docs/DISTRIBUTION.md and the README trust-model section;
#   3. signing is LOCAL-ONLY by design: the secret key stays on this
#      machine, never in CI. `scripts/release-local.sh --sign` writes
#      dist/SHA256SUMS(.minisig) and stages signed copies into release/ —
#      commit those with the tag; CI only verifies, it never signs.
#   4. back the private key up offline; losing it forfeits the identity.
set -eu

DIR="${HOME}/.config/demo-ghostprovider"
umask 077
mkdir -p "$DIR"

if [ -e "$DIR/release.key" ]; then
    echo "error: $DIR/release.key already exists — refusing to overwrite" >&2
    exit 1
fi

minisign -G -p "$DIR/release.pub" -s "$DIR/release.key"

echo ""
echo "generated:"
echo "  public  $DIR/release.pub"
echo "  private $DIR/release.key   (back it up; never commit or share)"
echo ""
echo "next steps are listed at the top of this script."
