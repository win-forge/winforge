# Autounattend Templates

This directory contains the **public** (non-credentialed) autounattend templates.

The private repo (`winforge-private`) overrides these with per-product files
under `winforge-private/autounattend/<product>.xml`.

If a private override exists, the build workflow uses it. Otherwise the public
template is used (installs will prompt for credentials during OOBE).

The template uses `{{PLACEHOLDER}}` syntax. These are replaced at build time
by `inject_autounattend.py` with values from GitHub Actions Secrets.
