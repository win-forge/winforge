# Contributing to WinForge

Thanks for your interest. This repo is the **tool**; consumer profiles,
autounattend templates, and secrets live in
[win-forge/winforge-configs](https://github.com/win-forge/winforge-configs)
(or your own fork of it). Pipeline feature work belongs here — profile and
template edits belong in a config repo.

## Development setup

```bash
# Python 3.11+
pip install -e ".[dev]"
pytest -q
```

Or with [uv](https://docs.astral.sh/uv/):

```bash
uv venv && uv pip install -e ".[dev]"
.venv/bin/python -m pytest -q
```

## CI layout

- **`ci.yml`** (PRs + main): `ruff check .`, `mypy scripts`, `pytest -q`.
- **`boot-test`** job: installs the real ISO toolchain (xorriso, genisoimage,
  p7zip-full, isolinux, syslinux-common) and runs the bootability test suite.
  Locally those tests **auto-skip** unless the tools are installed:

  ```bash
  sudo apt-get install -y xorriso genisoimage p7zip-full isolinux syslinux-common
  pytest tests/test_iso_bootability.py tests/test_repack_uefi.py tests/test_verify_iso_bootable.py -v
  ```

## Commit style

Conventional-ish prefixes with scopes, matching the existing history:

```
fix(ci): install isolinux/syslinux-common + add Rock Ridge/Joliet to test ISOs
feat(bypass): data-driven DLL bypass policy
test(bootability): comprehensive ISO bootability test suite
```

Common scopes: `build`, `ci`, `bypass`, `lint`, `test`, `verify`.

## Pull requests

1. Fork / branch off `main`.
2. `ruff check .` and `mypy scripts` must pass locally.
3. Add tests for behavior changes — the suite is fast (~8s) and there's no
   excuse. Bootability-critical changes need a test in
   `tests/test_iso_bootability.py` or friends.
4. Keep PRs focused; one logical change per PR.

## Releases

Releases are cut by dispatching `.github/workflows/release.yml`
(`workflow_dispatch`) with a `tag` input (`vX.Y.Z`). Bare major tags
(`v1`, `v2`, …) are movable: re-dispatching `release.yml` with `v1`
re-points the tag at current HEAD so consumers pinning `@v1` track
current stable.

## Reporting issues

Open a GitHub issue with the failed run link and the relevant log excerpt.
Security-sensitive findings: see
[SECURITY.md](SECURITY.md) — do not open public issues for them.
