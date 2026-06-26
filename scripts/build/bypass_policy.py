"""Determine whether a product+edition needs DLL-based Win11 bypass.

The registry tweak (LabConfig keys in autounattend.xml) is always on for
Win11 products. It works for most editions on 21H2-23H2 and 24H2 Home/Pro.

Some editions -- typically Enterprise, Enterprise N, and IoT Enterprise
LTSC -- have the registry trick blocked by Microsoft on 24H2+. For those,
the DLL patch (replacing appraiserres.dll + appraiser.dll in install.wim)
is required.

This module reads the ``needs_dll_bypass`` flag from editions.yaml so the
build pipeline can:

1. Skip the DLL staging + WIM mount/patch steps entirely for editions
   that don't need it (saves ~30s of WIM mount/unmount per index).
2. Fail early with a clear error if an edition NEEDS the DLL patch but
   the vendored DLLs are not present at ``bypass/<product>/``.

Usage as a CLI (from CI):
    python -m scripts.build.bypass_policy --product win11-24h2 --edition enterprise
    # exits 0 + prints "needs_dll_bypass=true" to stdout
    # exits 0 + prints "needs_dll_bypass=false" if not needed
    # exits 1 if needed but DLLs missing at bypass/<product>/

Usage as a module:
    from scripts.build.bypass_policy import needs_dll_bypass, dlls_available
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from scripts.lib.log import info, warn, error
from scripts.lib.yaml import load


def _find_editions_file() -> Path:
    """Locate editions.yaml — same resolution order as profiles.load."""
    explicit = ""
    # Don't import os at module level for testability; the profile loader
    # does the same env-var check. We keep it local to avoid coupling.
    import os
    explicit = os.environ.get("WINFORGE_CONFIG_ROOT", "").strip()
    if explicit:
        p = Path(explicit).resolve()
        if (p / "config" / "editions.yaml").is_file():
            return p / "config" / "editions.yaml"
        if (p / "editions.yaml").is_file():
            return p / "editions.yaml"
    cwd = Path.cwd().resolve()
    for candidate in (
        cwd / "config" / "editions.yaml",
        cwd / ".winforge" / "config" / "editions.yaml",
    ):
        if candidate.is_file():
            return candidate
    return cwd / "config" / "editions.yaml"


def needs_dll_bypass(product: str, edition: str, editions_file: Path | None = None) -> bool:
    """Return True if this product+edition needs the DLL patch.

    Reads ``needs_dll_bypass`` from the edition entry in editions.yaml.
    Defaults to False if the field is absent (backward-compatible with
    editions.yaml entries that don't set it).

    Win10 products never need DLL bypass — the hardware check is Win11-only.
    """
    if product.startswith("win10"):
        return False

    path = editions_file or _find_editions_file()
    if not path.is_file():
        warn("bypass_policy.no_editions_file", path=str(path))
        return False

    data = load(path)
    editions = data.get("editions", {}).get(product, [])
    for ed in editions:
        if ed.get("id") == edition:
            return bool(ed.get("needs_dll_bypass", False))

    warn("bypass_policy.edition_not_found", product=product, edition=edition)
    return False


def dlls_available(product: str, bypass_root: Path) -> bool:
    """Check whether both bypass DLLs exist at bypass/<product>/."""
    bypass_dir = bypass_root / product
    return (
        bypass_dir.is_dir()
        and (bypass_dir / "appraiserres.dll").is_file()
        and (bypass_dir / "appraiser.dll").is_file()
    )


def check(product: str, edition: str, bypass_root: Path, editions_file: Path | None = None) -> dict:
    """Full policy check. Returns a dict with:
    - needs_dll_bypass: bool
    - dlls_available: bool
    - action: "skip" | "patch" | "fail"
      - skip: edition doesn't need DLLs, don't stage them
      - patch: edition needs DLLs and they're present, stage + patch
      - fail: edition needs DLLs but they're missing
    """
    need = needs_dll_bypass(product, edition, editions_file)
    avail = dlls_available(product, bypass_root)

    if not need:
        action = "skip"
    elif avail:
        action = "patch"
    else:
        action = "fail"

    result = {
        "needs_dll_bypass": need,
        "dlls_available": avail,
        "action": action,
    }
    info("bypass_policy.check", product=product, edition=edition, **result)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check whether a product+edition needs DLL-based Win11 bypass."
    )
    parser.add_argument("--product", required=True, help="e.g. win11-24h2")
    parser.add_argument("--edition", required=True, help="e.g. enterprise")
    parser.add_argument(
        "--bypass-root",
        type=Path,
        default=Path("bypass"),
        help="Directory containing per-product bypass DLL subdirs (default: bypass/)",
    )
    parser.add_argument(
        "--editions-file",
        type=Path,
        default=None,
        help="Path to editions.yaml (default: auto-detect)",
    )
    args = parser.parse_args(argv)

    result = check(args.product, args.edition, args.bypass_root, args.editions_file)

    # Print machine-readable output for CI
    for k, v in result.items():
        print(f"{k}={v}")

    if result["action"] == "fail":
        error(
            "bypass_policy.missing_dlls",
            product=args.product,
            edition=args.edition,
            hint=f"This edition needs DLL bypass but bypass/{args.product}/ "
            f"is missing appraiserres.dll and/or appraiser.dll. "
            f"See bypass/README.md for sources.",
        )
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
