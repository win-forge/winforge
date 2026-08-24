# Security Policy

## Supported versions

WinForge is a single-branch project. Only the latest code on `main` and the
newest `vX.Y.Z` tag receive security fixes. Consumers should pin a
major-version tag (`@v1`) and keep it current — the major tag moves with
`main` by design (see `.github/workflows/release.yml`).

## Reporting a vulnerability

**Report privately via GitHub's "Report a vulnerability" button**
(Security → Advisories → New draft security advisory) on this repository.

Do **not** open public issues for exploitable flaws. Expect an initial
response within 7 days.

## In scope

- **Workflow injection / untrusted inputs** — anything that lets a PR, issue,
  or dispatch payload execute arbitrary code in CI with access to secrets.
- **Secret leakage through rendered output** — paths where
  `LOCAL_ADMIN_PASS` / `PRODUCT_KEY` could end up somewhere unintended
  (logs, artifacts beyond the documented ISO embedding, step summaries).
- **Bypass DLL integrity** (`bypass/<product>/`) — the vendored binaries are
  Microsoft-signed files mirrored from public sources; tampering or a
  malicious substitution is treated as supply-chain compromise.
- **Supply chain** — GitHub Actions refs (we pin third-party actions to
  release tags), pip dependencies.

## Known-by-design (not vulnerabilities)

These are documented product decisions, not flaws — reports restating them
will be closed:

- **The Win11 hardware-check bypass itself.** That is the entire point of the
  tool; see [`docs/bypass.md`](docs/bypass.md) and the decision record in
  [`bypass/README.md`](bypass/README.md).
- **Plaintext local-admin password embedded in built ISOs.**
  `autounattend.xml` carries `LOCAL_ADMIN_PASS` in plaintext inside every
  credential-bearing ISO; this is inherent to autounattend and warned about
  in the README. Distribute links accordingly.
- **Public gofile.io download links.** Uploads are unlisted-but-public by
  nature of the host; treat links as credentials.
