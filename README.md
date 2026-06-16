# WinForge — Windows ISO Build Pipeline (Reusable Workflow)

Polls [UUP-dump](https://uupdump.net), rebuilds Windows 10/11 ISOs for a matrix of editions whenever Microsoft ships cumulative updates. Injects Intel RST/VMD NVMe drivers, drops in an `autounattend.xml` for hands-off install, **bypasses the Win11 system-requirement check** (registry + DLL patch), and uploads finished ISOs to **gofile.io** (and optionally a pool of Google Drive accounts via rclone).

## Two-Repo Architecture

**This repo (`win-forge/winforge`)** is the *tool*. It contains:
- The reusable workflow (`.github/workflows/build.yml`)
- The build scripts (`scripts/`)
- The vendored default autounattend template (`autounattend/base.xml`)
- The catalog of products/editions (`config/products.yaml`, `config/editions.yaml`)

You do **not** fork this repo to use WinForge.

**[win-forge/winforge-configs](https://github.com/win-forge/winforge-configs)** is the *config repo*. It contains:
- Your build profiles (`config/profiles/*.yaml`)
- Your autounattend templates (`autounattend/*.xml`)
- A thin workflow that calls winforge's reusable workflow
- Your secrets (set at the repo level)

You fork `winforge-configs` (or use it as a template) and edit it. The winforge
repo stays up-to-date independently and your config repo just tracks a
`@v1` (or `@main`, or `@<sha>`) ref.

```
┌─────────────────────────┐                  ┌──────────────────────────┐
│  winforge-configs (you) │  workflow_call   │  winforge (the tool)     │
│                         │ ────────────────▶│                          │
│  config/profiles/*.yaml │                  │  scripts/                │
│  autounattend/*.xml     │                  │  reusable workflow       │
│  secrets: RCLONE_CONF,  │                  │  (does the heavy lifting)│
│           ACCOUNTS_YAML │                  │                          │
└─────────────────────────┘                  └──────────────────────────┘
```

## Quick Start

```bash
# 1. Create your config repo from the template
gh repo create myorg/my-winforge-configs --template win-forge/winforge-configs --public

# 2. Set secrets on YOUR repo (not on winforge)
gh secret set RCLONE_CONF -R myorg/my-winforge-configs < ~/.config/rclone/rclone.conf
gh secret set ACCOUNTS_YAML -R myorg/my-winforge-configs < my-accounts.yaml
gh secret set LOCAL_ADMIN_NAME -R myorg/my-winforge-configs
gh secret set LOCAL_ADMIN_PASS -R myorg/my-winforge-configs

# 3. Edit your profile
$EDITOR my-winforge-configs/config/profiles/win11-prod.yaml

# 4. Trigger a build
gh workflow run build.yml -R myorg/my-winforge-configs -f profile=win11-prod
```

## Self-Build Mode (Advanced)

You can also run builds directly on this repo using `repository_dispatch` or
`workflow_dispatch`. This uses the bundled `config/profiles/*.yaml` and
`autounattend/*.xml` and is useful for:
- Testing winforge changes end-to-end
- Building ISOs without spinning up a config repo

```bash
# Trigger via the winforge repo itself
gh api repos/win-forge/winforge/dispatches \
  -f event_type=build-request \
  -f client_payload[profile]=win11-prod
```

## What the build does

1. **Frees disk space** (`easimon/maximize-build-space@master`) — concatenates `/` and `/mnt` via LVM to give ~100GB usable (UUP→WIM conversion can hit 8GB+ intermediate files; GitHub's default 14GB temp disk + ~29GB root is not enough).
2. **Dual checkout** — when called from a config repo (`workflow_call`), checks out the caller's repo (for `config/`, `autounattend/`) AND this repo (for `scripts/`) into `.winforge/`. In self-build mode, just the standard checkout.
3. **Renders `{{PLACEHOLDER}}` tokens** in the autounattend template against GitHub Actions Secrets from the caller's repo.
4. **Downloads UUPs** → runs the UUP-dump converter → produces a stock ISO.
5. **Injects Intel RST drivers** into the WIM (gracefully skips if Intel's CDN WAF-blocks the request).
6. **Bypasses the Win11 system-requirement check** (TPM 2.0 / Secure Boot / 4 GB RAM / CPU) — always via the `LabConfig` registry keys in the autounattend, plus an optional DLL patch for 24H2+ SKUs that block the registry trick. See [`docs/bypass.md`](docs/bypass.md).
7. **Repacks the ISO** with the rendered autounattend baked in.
8. **Uploads to gofile.io** (new — primary destination) and optionally to a pool of Google Drive accounts via rclone.
9. **Uploads ISO as a debug artifact** (7-day retention).

## Required Secrets (set on the caller/config repo)

| Secret | Required? | Used for |
|---|---|---|
| `RCLONE_CONF` | yes | rclone config (Google Drive account pool) |
| `ACCOUNTS_YAML` | yes | `config/accounts.yaml` content (account pool metadata) |
| `GOFILE_TOKEN` | optional | gofile.io JWT (free account). If empty, uploads as guest and content expires in ~10 days. |
| `BYPASS_DLLS_B64` | optional | base64 tarball of bypass DLLs from `winforge-private/bypass/<product>/`. If empty, only the registry tweak is applied. |
| `LOCAL_ADMIN_NAME` | if your autounattend uses `{{LOCAL_ADMIN_NAME}}` |
| `LOCAL_ADMIN_PASS` | if your autounattend uses `{{LOCAL_ADMIN_PASS}}` (PlainText) |
| `COMPUTER_NAME` | optional | `{{COMPUTER_NAME}}` in autounattend |
| `PRODUCT_KEY` | optional | `{{PRODUCT_KEY}}` in autounattend |

*Required if your autounattend template uses those placeholders. If you only
use `oobe-skip.xml` (no placeholders), the build skips rendering and uses
the template as-is.

## Upgrading winforge

WinForge uses major-version tags (`v1`, `v2`, ...) for stable releases.
Bump the ref in your config repo's `.github/workflows/build.yml`:

```yaml
# Stable (recommended for production)
uses: win-forge/winforge/.github/workflows/build.yml@v1

# Bleeding edge
uses: win-forge/winforge/.github/workflows/build.yml@main

# Exact pin
uses: win-forge/winforge/.github/workflows/build.yml@a1b2c3d
```

Renovate / Dependabot will detect new tags and open PRs on your config repo.

## Development

```
pip install -e ".[dev]"
pytest -q
```

See `.github/workflows/` for CI entry points. `build.yml` is the reusable
workflow (consumed by config repos); `check-updates.yml` runs daily to detect
new UUP-dump builds; `ci.yml` is PR-time linting.

Additional docs:
- [`docs/gofile.md`](docs/gofile.md) — gofile.io upload (guest vs account mode, API quirks)
- [`docs/bypass.md`](docs/bypass.md) — Win11 system-requirement bypass (registry + DLL patch)

## Disk Space Background

The original `easimon/maximize-build-space` action handles `/` and `/mnt`
but the runner's `_diag` log volume is separate — that fills up on long
builds and crashes the runner. WinForge adds a second step that
truncates old `_diag/Worker_*.log` files.

Also: the action creates a 100GB LVM image mounted at `$GITHUB_WORKSPACE`,
which leaves `/tmp` (on `/dev/root`) with only ~2GB. WinForge's `convert.sh`
and `repack.sh` use `${GITHUB_WORKSPACE}` for temp dirs (via the
`WORKDIR` env var override) so the UUP download + ISO repack happen on
the LVM volume.
