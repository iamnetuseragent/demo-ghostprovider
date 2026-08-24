# GhostProvider distribution model

Canonical description of how the full (paid) GhostProvider reaches its
users. This document doubles as the public transparency page: prospective
buyers can read exactly what they receive, what runs on their machine,
and what they can verify — before paying.

## Why not a download token

The previous scheme — a bot handing out a URL containing an access token
to a private repository that then `pip install`ed itself — had two fatal
properties for a product whose brand is "private & local":

1. **Opaque to the buyer.** A raw token-URL reveals nothing: no source,
   no version, no author, no checksum. It is indistinguishable from a
   scam link and impossible to audit.
2. **Unverifiable code execution.** Whatever commit sat in the private
   repository ran with full user privileges. A compromised maintainer
   account or CI would be a supply-chain RCE under a security brand.

Both are fixed by moving distribution onto Codeberg invites plus signed,
tagged releases.

## What a buyer receives

After payment, the bot sends **one message** containing:

1. An **invite link** to collaborate on the private repository
   <https://codeberg.org/netuser/ghostprovider>.
2. A **card** stating exactly what will be installed:
   - repository path and current release tag (`vX.Y.Z`);
   - commit hash the tag points to;
   - SHA256 of `SHA256SUMS`;
   - the release signing key fingerprint;
   - a plain-language list of installer actions (clone, verify, build, install unit).

The user accepts the invite **under their own Codeberg account** and
clones over HTTPS **with their own credentials**. No author-side token
ever appears in any URL, script, or process listing.

## Installer contract (full version)

The paid installer must obey the same rules enforced by the demo build:

1. **Verify before running anything.** The first action is checking the
   tag signature and checksums. Nothing executes until that passes.
2. **Pinned tags by default.** Builds resolve to a signed `v*` tag;
   building untagged HEAD requires an explicit `--head` flag.
3. **No embedded secrets.** No tokens in argv, env dumps, or logs
   (same discipline as the demo's GIT_ASKPASS handling in gitclone.rs).
4. **User-level only.** Everything installs into the user session:
   systemd user units, XDG paths, no sudo, ever.

## Release process (maintainer)

1. Tag: `git tag -s vX.Y.Z` (or annotate + sign), push to GitHub and Codeberg.
2. Demo pipeline: pushing the tag builds static musl artifacts and
   attaches `SHA256SUMS` + `SHA256SUMS.minisig` (see `.github/workflows/release.yml`).
   Full-version releases follow the identical procedure from the private repo.
3. Local cross-check: `scripts/release-local.sh --sign` must produce the
   same binary hash as CI; investigate any mismatch before publishing.
4. Update the card data the bot serves (tag, commit, SHA256, fingerprint)
   in the same change set as the release itself.

## Signing identity

One minisign keypair covers demo and full-version artifacts.

```
public key : docs/release.pub (committed in both repositories)
fingerprint: <TO-BE-FILLED after scripts/keygen-release.sh>
```

Until a fingerprint is published here, treat unsigned artifacts as
unreleased builds, not as products.

## Threat model

**Mitigated:** scam-looking opaque links; silent code substitution
between release and install; author-side credential theft granting
persistent access (revocation = removing a collaborator); tag rewriting
(protected `v*` tags on Codeberg).

**Not mitigated:** a genuinely malicious *signed* release remains trusted
— signatures prove origin, not intent. That risk is addressed socially
(source visible to every licensee from day one) and structurally (the
installer contract keeps runtime behavior auditable against the demo).

## Status

| Step | State |
|---|---|
| Private repo `netuser/ghostprovider` | done |
| Protected `v*` tags | done |
| Release signing keys | pending — `scripts/keygen-release.sh`, then fill fingerprint above |
| First signed tag in the private repo | pending |
| Bot rework: invite + card instead of token URL | pending |
