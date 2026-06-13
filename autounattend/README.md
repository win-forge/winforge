# Autounattend — Single-Repo Templates

WinForge ships **template** autounattend XMLs in this repo. `{{PLACEHOLDER}}`
variables are rendered at build time against GitHub Actions Secrets. No
separate private repo is required.

## How it works

1. `autounattend/base.xml` — generic OOBE-skip template, has `{{LOCAL_ADMIN_NAME}}` + `{{LOCAL_ADMIN_PASS}}` placeholders
2. `autounattend/oobe-skip.xml` — even-shorter template, no placeholders (used as a no-credential fallback)
3. `autounattend/<product>.xml` — optional per-product override (e.g. `win11-24h2.xml`, `win11-ltsc.xml`). Must live in this repo.
4. Build workflow's `Render autounattend from template + secrets` step picks `autounattend/${PRODUCT}.xml` (or falls back to `base.xml`), reads the secrets listed below, and renders the result. Rendered XML is written to `artifacts/autounattend/win11.xml` for the repack step.

## Placeholder variables

| Variable | Required? | Description |
|---|---|---|
| `{{LOCAL_ADMIN_NAME}}` | yes | Local admin username (e.g. "sysadmin") |
| `{{LOCAL_ADMIN_PASS}}` | yes | Local admin password (PlainText) |
| `{{COMPUTER_NAME}}` | optional | Desired computer name |
| `{{PRODUCT_KEY}}` | optional | KMS client setup key or retail key |

These are rendered at build time by `scripts/build/inject_autounattend.py`,
called from the `Render autounattend from template + secrets` step in
`build.yml`. The render step uses the same library that powers
`tests/test_inject_autounattend.py` — render behavior is unit-tested.

If a template contains no `{{...}}` placeholders, it's copied verbatim
(useful for keeping product-specific tweaks that don't need secrets).

## Required GitHub Actions Secrets

| Secret | Used for |
|---|---|
| `LOCAL_ADMIN_NAME` | Rendered into `{{LOCAL_ADMIN_NAME}}` |
| `LOCAL_ADMIN_PASS` | Rendered into `{{LOCAL_ADMIN_PASS}}` (PlainText — this is fine for unattend but use a strong password) |
| `COMPUTER_NAME` | Rendered into `{{COMPUTER_NAME}}` |
| `PRODUCT_KEY` | Rendered into `{{PRODUCT_KEY}}` |
| `RCLONE_CONF` | rclone config (Google Drive accounts) — used by upload step |
| `ACCOUNTS_YAML` | `config/accounts.yaml` content (OneDrive/Google Drive account pool) — used by assign step |

**`INTEL_RST_TOKEN` is not required.** Intel RST driver injection is opt-in
and silently skipped if Intel's download CDN blocks the runner (WAF
challenge). See `scripts/drivers/sync_intel_rst.py` for the graceful-skip
behavior.

Set these at: **Settings → Secrets and variables → Actions → New repository secret**
on this repo (`win-forge/winforge`).

If a placeholder is referenced in a template but the corresponding secret is
not set, the build fails with a clear error listing the missing variables.

## File naming

Place files under `autounattend/<product>.xml` where `<product>` matches the
product name in `config/products.yaml` (e.g. `win11-24h2.xml`, `win11-ltsc.xml`).
