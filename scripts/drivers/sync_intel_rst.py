"""Sync the latest Intel RST/VMD driver pack into drivers/pack/intel-rst/<version>/.

Source priority:
  1. GitHub release asset on win-forge/winforge (fast, reliable, version-pinned)
  2. Intel's download CDN (fallback if the release doesn't exist for the current
     version — handles the transition between Intel publishing a new version
     and us cutting a new release)

Intel's CDN frequently WAF-blocks bot traffic (returns 0-byte files), which
is why the release-asset path is preferred. Both paths handle both old format
(SetupRST_X.Y.Z.W.exe) and new format (SetupRST.exe on a page that lists the
version).
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
# GitHub release asset: pinned SetupRST.exe for the version this script
# expects. The version literal is appended to the URL so a single script
# works for any pinned Intel version. Update VENDORED_VERSION when bumping.
VENDORED_VERSION = "20.2.6.1025"
VENDORED_RELEASE_URL = (
    f"https://github.com/win-forge/winforge/releases/download/"
    f"intel-rst-v{VENDORED_VERSION}/SetupRST.exe"
)
PACK_ROOT = Path(__file__).parent / "pack" / "intel-rst"

# Filename with version: SetupRST_19.5.2.1049.exe
VERSIONED_RE = re.compile(r"SetupRST[_A-Za-z]*(\d+(?:\.\d+)+)\.exe", re.IGNORECASE)
# Generic filename (no version): SetupRST.exe
GENERIC_RE = re.compile(r'href="(https?://downloadmirror\.intel\.com/\d+/SetupRST\.exe)"', re.IGNORECASE)
# Version text somewhere on the page
PAGE_VERSION_RE = re.compile(r"\b(\d+\.\d+\.\d+\.\d+)\b")


def extract_version_from_filename(name: str) -> str | None:
    m = VERSIONED_RE.search(name)
    return m.group(1) if m else None


def find_driver_root(extract_dir: Path) -> Path:
    """Heuristic: pick the directory containing the deepest iaStorAC/x64/iaStorAC.inf."""
    candidates = list(extract_dir.rglob("iaStorAC.inf"))
    if not candidates:
        raise FileNotFoundError("iaStorAC.inf not found in extracted tree")
    return candidates[0].parent.parent.parent  # iaStorAC.inf/x64/iaStorAC -> drivers


def _try_vendored_release() -> tuple[str, str] | None:
    """Download from the win-forge/winforge GitHub release.

    Returns (version, local_path) on success, or None if the release is missing
    / unreachable. The version is hard-coded (VENDORED_VERSION) since the
    release tag encodes it.
    """
    try:
        with requests.get(VENDORED_RELEASE_URL, stream=True, timeout=30) as r:
            r.raise_for_status()
            tmp = Path("/tmp") / f"SetupRST_v{VENDORED_VERSION}.exe"
            with tmp.open("wb") as f:
                for chunk in r.iter_content(chunk_size=1 << 20):
                    f.write(chunk)
            # Sanity: reject 0-byte (Intel WAF returns these; release shouldn't,
            # but defensive)
            if tmp.stat().st_size < 1024:
                tmp.unlink(missing_ok=True)
                return None
            return VENDORED_VERSION, str(tmp)
    except Exception as e:
        error("drivers.vendored_download_failed", error=str(e),
              url=VENDORED_RELEASE_URL)
        return None


def fetch_latest_metadata() -> tuple[str, str]:
    """Return (version, direct_download_url).

    Tries the new generic filename + page-version pattern first, then falls back
    to the old versioned filename pattern.
    """
    r = requests.get(DOWNLOAD_PAGE, timeout=30, allow_redirects=True)
    r.raise_for_status()
    html = r.text

    # New pattern: SetupRST.exe + version listed elsewhere on the page
    m_url = GENERIC_RE.search(html)
    if m_url:
        m_ver = PAGE_VERSION_RE.search(html)
        if m_ver:
            return m_ver.group(1), m_url.group(1)

    # Old pattern: filename contains the version
    m = VERSIONED_RE.search(html)
    if m:
        version = m.group(1)
        download_url = f"https://downloadmirror.intel.com/849936/eng/SetupRST_{version}.exe"
        return version, download_url

    raise RuntimeError(
        f"Could not parse RST version/URL from Intel download page "
        f"(page returned {len(html)} bytes, no SetupRST.exe link found)"
    )


def sync(target_root: Path = PACK_ROOT) -> Path | None:
    """Sync the Intel RST/VMD driver pack.

    Order of operations:
      1. Download the pinned version from the win-forge/winforge GitHub release
      2. Extract drivers from the installer
      3. Move drivers to the pack directory

    Falls back to Intel's CDN if the release is missing/unreachable (e.g. when
    Intel publishes a new version and we haven't cut a new release yet).

    Returns the driver root Path on success, or None if every source failed.
    Callers should treat None as "skip driver injection, continue build".
    """
    # Try the vendored release first
    out_dir = target_root / VENDORED_VERSION
    if (out_dir / ".synced").exists():
        info("drivers.already_synced", version=VENDORED_VERSION, path=str(out_dir))
        return out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    installer = out_dir / "SetupRST.exe"

    vendored = _try_vendored_release()
    if vendored is not None:
        version, local_path = vendored
        shutil.move(local_path, installer)
        info("drivers.vendored_ok", version=version, dst=str(installer))
    else:
        # Fall back to Intel CDN
        try:
            version, url = fetch_latest_metadata()
        except Exception as e:
            error("drivers.metadata_failed", error=str(e))
            return None
        info("drivers.downloading", version=version, url=url)
        try:
            with requests.get(url, stream=True, timeout=120) as r:
                r.raise_for_status()
                with installer.open("wb") as f:
                    for chunk in r.iter_content(chunk_size=1 << 20):
                        f.write(chunk)
        except Exception as e:
            error("drivers.download_failed", error=str(e), url=url)
            if installer.exists():
                installer.unlink()
            return None
        if installer.stat().st_size < 1024:
            error("drivers.download_failed", reason="empty_or_blocked", url=url,
                  size=installer.stat().st_size)
            installer.unlink()
            return None

    extract_dir = out_dir / "extracted"
    extract_dir.mkdir(exist_ok=True)
    try:
        subprocess.run(["7z", "x", "-y", f"-o{extract_dir}", str(installer)], check=True)
    except subprocess.CalledProcessError as e:
        error("drivers.extract_failed", error=str(e))
        return None
    try:
        driver_root = find_driver_root(extract_dir)
    except FileNotFoundError as e:
        error("drivers.layout_changed", error=str(e))
        return None
    final = out_dir / "drivers"
    if final.exists():
        shutil.rmtree(final)
    shutil.move(str(driver_root), str(final))
    (out_dir / ".synced").write_text(file_sha256(installer) + "\n")
    info("drivers.synced", version=VENDORED_VERSION, path=str(final))
    return final


if __name__ == "__main__":
    result = sync()
    if result is None:
        # Graceful skip — log already emitted. Don't fail the build.
        print("Driver sync skipped (download blocked or layout changed). Continuing without RST driver injection.")
        sys.exit(0)

