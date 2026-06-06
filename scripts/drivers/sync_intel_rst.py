"""Sync the latest Intel RST/VMD driver pack into drivers/pack/intel-rst/<version>/.

Source: https://www.intel.com/content/www/us/en/download/849936/...
"""
from __future__ import annotations
import re
import shutil
import subprocess
import sys
from pathlib import Path
import requests
from scripts.lib.log import info, error
from scripts.lib.sha import file_sha256

DOWNLOAD_PAGE = (
    "https://www.intel.com/content/www/us/en/download/849936/"
    "intel-rapid-storage-technology-driver-installation-software-with-intel-optane-memory.html"
)
PACK_ROOT = Path(__file__).parent / "pack" / "intel-rst"
VERSION_RE = re.compile(r"SetupRST[_A-Za-z]*(\d+(?:\.\d+)+)\.exe", re.IGNORECASE)


def extract_version_from_filename(name: str) -> str | None:
    m = VERSION_RE.search(name)
    return m.group(1) if m else None


def find_driver_root(extract_dir: Path) -> Path:
    """Heuristic: pick the directory containing the deepest iaStorAC/x64/iaStorAC.inf."""
    candidates = list(extract_dir.rglob("iaStorAC.inf"))
    if not candidates:
        raise FileNotFoundError("iaStorAC.inf not found in extracted tree")
    return candidates[0].parent.parent.parent  # iaStorAC.inf/x64/iaStorAC -> drivers


def fetch_latest_metadata() -> tuple[str, str]:
    """Return (version, direct_download_url)."""
    r = requests.get(DOWNLOAD_PAGE, timeout=30)
    r.raise_for_status()
    m = VERSION_RE.search(r.text)
    if not m:
        raise RuntimeError("Could not parse RST version from Intel download page")
    version = m.group(1)
    download_url = (
        f"https://downloadmirror.intel.com/849936/eng/SetupRST_{version}.exe"
    )
    return version, download_url


def sync(target_root: Path = PACK_ROOT) -> Path:
    version, url = fetch_latest_metadata()
    out_dir = target_root / version
    if (out_dir / ".synced").exists():
        info("drivers.already_synced", version=version, path=str(out_dir))
        return out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    installer = out_dir / f"SetupRST_{version}.exe"
    info("drivers.downloading", version=version, url=url)
    with requests.get(url, stream=True, timeout=120) as r:
        r.raise_for_status()
        with installer.open("wb") as f:
            for chunk in r.iter_content(chunk_size=1 << 20):
                f.write(chunk)
    extract_dir = out_dir / "extracted"
    extract_dir.mkdir(exist_ok=True)
    subprocess.run(["7z", "x", "-y", f"-o{extract_dir}", str(installer)], check=True)
    driver_root = find_driver_root(extract_dir)
    final = out_dir / "drivers"
    if final.exists():
        shutil.rmtree(final)
    shutil.move(str(driver_root), str(final))
    (out_dir / ".synced").write_text(file_sha256(installer) + "\n")
    info("drivers.synced", version=version, path=str(final))
    return final


if __name__ == "__main__":
    try:
        sync()
    except Exception as e:
        error("drivers.sync_failed", error=str(e))
        sys.exit(1)
