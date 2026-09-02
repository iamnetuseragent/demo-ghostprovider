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

After payment the sales bot (`botpay`) runs a short dialogue:

1. It asks for the buyer's **codeberg.org username** (charset-validated,
   checked against the Forgejo API).
2. It adds that account as a **read-only collaborator** of the private
   repository <https://codeberg.org/netuser/ghostprovider> through the
   Forgejo API. The invitation appears in the buyer's own Codeberg UI;
   no clickable token-link exists at any point.
3. It sends an **access card** stating exactly what will be installed:
   - repository path and latest release tag (`vX.Y.Z`) + commit it points
     to (or an explicit "no release yet" notice);
   - SHA256 checksums live on the release page next to `SHA256SUMS.minisig`;
   - the release signing key fingerprint and the verify commands;
   - a plain-language list of installer actions (clone, verify, build, install unit)
     and a pointer to this document.

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
2. **Build and sign locally first** — the signing key never lives on GitHub.
   `scripts/release-local.sh --sign` produces `dist/SHA256SUMS` +
   `dist/SHA256SUMS.minisig` and stages signed copies into `release/`.
   Commit `release/SHA256SUMS` + `release/SHA256SUMS.minisig` in the same
   change set as the tag.
3. Pushing the `v*` tag makes CI build the static musl binary and then
   **verify** the committed signature against `docs/release.pub` and match
   its own checksums against the signed ones (the reproducibility audit).
   An unsigned or mismatched release FAILS the workflow — with this policy
   an unsigned release is never published at all. Full-version releases
   follow the identical procedure from the private repo.
4. Local cross-check confirmed by the identical procedure above:
   `scripts/release-local.sh --sign` produces the same binary hash as CI;
   investigate any mismatch before publishing.
5. Update the card data the bot serves (tag, commit, SHA256, fingerprint)
   in the same change set as the release itself.

## Signing identity

One minisign keypair covers demo and full-version artifacts.

```
public key : docs/release.pub (committed in both repositories)
fingerprint: D734132609C90194
```

Verify any release artifact:

```sh
minisign -Vm SHA256SUMS -p docs/release.pub
sha256sum -c SHA256SUMS
```

The one-shot demo installer (`install.sh`) is signed with the same key too
(`install.sh.minisig`, committed next to it), so the installer itself can be
verified **before** it is executed instead of being piped to `sh` unchecked:

```sh
curl -fsSL -o /tmp/dgp-install.sh https://raw.githubusercontent.com/iamnetuseragent/demo-ghostprovider/main/install.sh
curl -fsSL -o /tmp/dgp-install.sh.minisig https://raw.githubusercontent.com/iamnetuseragent/demo-ghostprovider/main/install.sh.minisig
minisign -Vm /tmp/dgp-install.sh -s /tmp/dgp-install.sh.minisig -P "$(sed -n 2p docs/release.pub)"
sh /tmp/dgp-install.sh
```

If the signature does not verify, do **not** run it — the script has been
tampered with or the key has rotated. The `installation/install.sh` (source /
full product path) is also signed the same way (`installation/install.sh.minisig`).

## Git tag signing (GPG)

Source releases are delivered as **tags**, and the source installer
(`installation/install.sh`) runs `git verify-tag` before building anything.
Tags must therefore be signed, and the signer's fingerprint must be
publically verifiable — otherwise "verify the tag" is a ceremony over an
unverifiable identity.

* The maintainer GPG key (RSA/Ed25519, `git tag -s`) is created and its
  fingerprint published here *before the first release tag is cut*. Until
  then, no tag should advertise verification.
* Users import the key out-of-band (from this doc/repo, never from a
  transcript someone pasted) and run:

  ```sh
  gpg --keyserver keys.openpgp.org --recv-keys <FINGERPRINT>
  git verify-tag vX.Y.Z            # after cloning
  ```

* The minisign key `RWSUAckJJhM011XphIH3LQE0Ebn62qqMMQej4Ong52/rGNw/rxRKniqA`
  (fingerprint `D734132609C90194`) signs the *binary* checksums; the GPG key
  signs the *source* tag. They are separate identities on purpose: the binary
  key can be rotated without invalidating installed source builds.
* **Status: pending** — the GPG fingerprint will be pasted into this section
  when the key is provisioned; `installation/install.sh` prints a pointer to
  this document when verification fails.

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
| Release signing keys | done — `docs/release.pub`, fingerprint above |
| First signed tag in the private repo | pending |
| Bot rework: username + Forgejo invite + card (`botpay`) | done |
