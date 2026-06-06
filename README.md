# WinForge — Automated Windows ISO Build Pipeline

Polls [UUP-dump](https://uupdump.net), rebuilds Windows 10/11 ISOs for a matrix of editions whenever Microsoft ships cumulative updates. Injects Intel RST/VMD NVMe drivers, enables Microsoft XPS Document Writer, drops in an autounattend.xml for hands-off install, and uploads finished ISOs to a fleet of Google Drive accounts via rclone.

## Architecture

- **Public repo (this one)** — the engine. Reusable workflows, build scripts, UUP-dump scraper, driver pack, generic autounattend templates, config. No secrets.
- **Private repo (`winforge-private`)** — credentialed autounattend XMLs, rclone config, Actions Secrets. Calls public workflows via `repository_dispatch`.

## Repos

| Repo | Visibility | Contents |
|------|-----------|----------|
| `yoav/winforge` | Public | Engine (workflows, scripts, config) |
| `yoav/winforge-private` | Private | Credentials, rclone conf, secrets |

## Quick Start

```
pip install -e ".[dev]"
pytest -q
```

See `.github/workflows/` for CI entry points.
