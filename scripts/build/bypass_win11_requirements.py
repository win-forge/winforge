"""Apply Win11 system-requirement bypass to install.wim.

Two layers (orthogonal — both can be on, either, or neither):

  1. Registry tweak in autounattend.xml
     `HKLM\\SYSTEM\\Setup\\LabConfig\\BypassTPMCheck` /
     `BypassSecureBootCheck` / `BypassRAMCheck` — baked into
     `autounattend/base.xml` and always applied to Win11 products.

  2. DLL patch in install.wim
     Replaces `Windows/System32/appraiserres.dll` and `appraiser.dll`
     with community-bypass versions. Bypasses the compatibility check
     for Win11 builds where Microsoft has blocked the registry trick
     (e.g. some 24H2/25H2 SKUs). Source DLLs come from the private repo
     (`winforge-private/bypass/<product>/`) and are passed in as a
     base64 tarball secret (`BYPASS_DLLS_B64`).

This script does (2). Works on the Windows runner via `dism`, on Linux
via `wimlib-imagex`. Auto-detected by platform.
"""
from __future__ import annotations
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from scripts.lib.log import info, warn, error


PATCHABLE_DLLS = ("appraiserres.dll", "appraiser.dll")


@dataclass
class BypassConfig:
    wim_path: Path
    mount_dir: Path
    bypass_dir: Path | None  # None = skip DLL patch; registry tweak is in autounattend


def _run(cmd: list[str]) -> None:
    info("bypass.cmd", cmd=" ".join(cmd))
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        error("bypass.cmd_failed", cmd=cmd, stderr=res.stderr.strip())
        raise RuntimeError(f"command failed: {cmd}\n{res.stderr}")
    if res.stdout.strip():
        for line in res.stdout.splitlines()[:20]:
            info("bypass.cmd_out", line=line)


def detect_tool() -> str:
    """Return 'dism' on Windows, 'wimlib-imagex' elsewhere."""
    if sys.platform == "win32":
        return "dism"
    return "wimlib-imagex"


def get_wim_indexes(wim: Path, tool: str) -> list[int]:
    if tool == "dism":
        r = subprocess.run(
            ["dism", "/Get-WimInfo", f"/WimFile:{wim}"],
            capture_output=True, text=True, check=False,
        )
    else:
        r = subprocess.run(
            ["wimlib-imagex", "info", str(wim)],
            capture_output=True, text=True, check=False,
        )
    if r.returncode != 0:
        raise RuntimeError(f"wim info failed: {r.stderr}")
    idxs: list[int] = []
    for line in r.stdout.splitlines():
        line = line.strip()
        if line.lower().startswith("index"):
            parts = line.split(":", 1)
            if len(parts) == 2:
                try:
                    idxs.append(int(parts[1].strip()))
                except ValueError:
                    pass
    return idxs


def mount_wim(wim: Path, mount: Path, index: int, tool: str) -> None:
    mount.mkdir(parents=True, exist_ok=True)
    if tool == "dism":
        _run(["dism", "/Mount-Wim", f"/WimFile:{wim}", f"/Index:{index}",
              f"/MountDir:{mount}", "/ReadOnly:NO"])
    else:
        _run(["wimlib-imagex", "mount", str(wim), str(index), str(mount), "--readwrite"])


def unmount_wim(mount: Path, tool: str, commit: bool = True) -> None:
    if tool == "dism":
        action = "/Commit" if commit else "/Discard"
        _run(["dism", "/Unmount-Wim", f"/MountDir:{mount}", action])
    else:
        action = "--commit" if commit else "--discard"
        _run(["wimlib-imagex", "unmount", str(mount), action])


def patch_dlls(mount: Path, bypass_dir: Path) -> list[str]:
    target = mount / "Windows" / "System32"
    target.mkdir(parents=True, exist_ok=True)
    patched: list[str] = []
    for dll in PATCHABLE_DLLS:
        src = bypass_dir / dll
        if not src.exists():
            warn("bypass.dll_missing", dll=dll, expected=str(src))
            continue
        dst = target / dll
        shutil.copy2(src, dst)
        info("bypass.dll_patched", dll=dll, dst=str(dst), size=src.stat().st_size)
        patched.append(dll)
    return patched


def apply(cfg: BypassConfig) -> dict[str, Any]:
    """Mount + patch + unmount each WIM index. Returns a summary dict.

    The registry tweak in autounattend.xml is always on for Win11 products;
    this function handles only the DLL patch.
    """
    tool = detect_tool()
    info("bypass.tool", tool=tool, wim=str(cfg.wim_path))
    cfg.mount_dir.mkdir(parents=True, exist_ok=True)

    do_dll_patch = bool(cfg.bypass_dir and cfg.bypass_dir.exists())
    if not do_dll_patch:
        warn("bypass.no_dlls",
             hint="registry tweak still applied via autounattend. "
                  "Provide BYPASS_DLLS_B64 secret to also patch appraiser DLLs.")

    indexes = get_wim_indexes(cfg.wim_path, tool)
    if not indexes:
        raise RuntimeError(f"no indexes found in {cfg.wim_path}")

    patched: list[str] = []
    for idx in indexes:
        info("bypass.index.start", index=idx)
        try:
            mount_wim(cfg.wim_path, cfg.mount_dir, idx, tool)
            if do_dll_patch:
                patched.extend(patch_dlls(cfg.mount_dir, cfg.bypass_dir))  # type: ignore[arg-type]
            unmount_wim(cfg.mount_dir, tool, commit=True)
        except Exception:
            try:
                unmount_wim(cfg.mount_dir, tool, commit=False)
            except Exception:
                pass
            raise
        info("bypass.index.done", index=idx)

    summary = {
        "tool": tool,
        "wim": str(cfg.wim_path),
        "indexes": indexes,
        "dll_patch": do_dll_patch,
        "dlls_patched": sorted(set(patched)),
    }
    info("bypass.done", **summary)
    return summary


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="Patch install.wim with Win11 bypass DLLs")
    p.add_argument("--wim", type=Path, required=True, help="Path to install.wim")
    p.add_argument("--mount", type=Path, required=True, help="Mount directory (will be created)")
    p.add_argument("--bypass-dir", type=Path, default=None,
                   help="Directory containing appraiserres.dll + appraiser.dll. "
                        "Skip to rely on autounattend registry tweak only.")
    args = p.parse_args()
    apply(BypassConfig(wim_path=args.wim, mount_dir=args.mount, bypass_dir=args.bypass_dir))
