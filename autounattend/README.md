# Autounattend — Private Repo Contract

WinForge ships **template** autounattend XMLs in the public repo (no secrets).
Your **private** repo (`winforge-private`) stores the actual per-product XMLs.

## How it works

1. `autounattend/base.xml` (public) — generic OOBE-skip template with `{{PLACEHOLDER}}` variables
2. `winforge-private/autounattend/<product>.xml` — per-product override with real credentials
3. Build workflow copies the **private** version over the **public** template

If a product XML is absent from the private repo, the public template is used
(no credential substitution — installs will prompt during OOBE).

## Placeholder variables

| Variable | Description |
|---|---|
| `{{LOCAL_ADMIN_NAME}}` | Local admin username (e.g. "sysadmin") |
| `{{LOCAL_ADMIN_PASS}}` | Local admin password (PlainText) |
| `{{COMPUTER_NAME}}` | Desired computer name |
| `{{PRODUCT_KEY}}` | KMS client setup key or retail key |

These are rendered at build time by `scripts/build/inject-autounattend.py`.

## File naming

Place files under `winforge-private/autounattend/<product>.xml` where `<product>`
matches the product name in `config/products.yaml` (e.g. `win11-24h2.xml`, `win11-ltsc.xml`).
