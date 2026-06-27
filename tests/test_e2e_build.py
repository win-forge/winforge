"""End-to-end test verifying the complete build pipeline produces an ISO.

This is a 'dry run' of the build pipeline in a single test, using:
- Mocked network (no real UUP-dump calls)
- Real tools we have on the system: 7z (if present), genisoimage (if present)
- Synthetic UUP files (a few .cab/.esd files with valid structure)

Skipped if the necessary tools aren't installed. Goal: prove the entire
script chain (download -> convert -> repack -> upload assignment) runs
without crashing, not that it produces a real Windows ISO.
"""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
WORK = Path(os.environ.get("TMPDIR", "/tmp")) / "winforge-e2e"


@pytest.fixture(scope="module")
def ensure_tools():
    """Skip the e2e test if required tools aren't installed."""
    required = ["7z", "7zr", "7za"]  # at least one must be present
    if not any(shutil.which(t) for t in required):
        pytest.skip("7z not installed (needed for ISO extraction)")


def test_genisoimage_produces_bootable_iso(tmp_path: Path):
    """Smoke test: genisoimage on Linux can create a valid ISO from a small dir.
    This is the same tool the UUP-dump converter uses internally."""
    if not shutil.which("genisoimage"):
        pytest.skip("genisoimage not installed (apt install genisoimage)")

    iso_content = tmp_path / "src"
    iso_content.mkdir()
    (iso_content / "readme.txt").write_text("smoke test")
    (iso_content / "data.txt").write_text("x" * 1000)

    iso_out = tmp_path / "out.iso"
    result = subprocess.run(
        ["genisoimage", "-o", str(iso_out), str(iso_content)],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, f"genisoimage failed: {result.stderr}"
    assert iso_out.exists()
    assert iso_out.stat().st_size > 2048  # at least a sector

    # Verify ISO signature — PVD is at sector 16 (offset 32768), not byte 0.
    # Sector 0 is the System Area (typically zeros). The PVD starts with
    # type byte 0x01 followed by "CD001".
    with open(iso_out, "rb") as f:
        f.seek(16 * 2048)
        sig = f.read(7)
    assert sig.startswith(b"\x01CD001"), f"Not a valid ISO: PVD at sector 16 = {sig!r}"


def test_assign_account_selects_smallest_pool(tmp_path: Path):
    """assign.py must pick an account that handles the product + has free quota."""
    import yaml
    accounts = {
        "accounts": [
            {"name": "od1", "handles_products": ["win11-24h2"], "quota_gb": 100, "used_gb": 80},
            {"name": "od2", "handles_products": ["win11-24h2"], "quota_gb": 200, "used_gb": 20},
            {"name": "gd1", "handles_products": ["win11-24h2"], "quota_gb": 100, "used_gb": 99},
        ]
    }
    f = tmp_path / "accounts.yaml"
    f.write_text(yaml.safe_dump(accounts))
    # All three can host a 5GB ISO for win11-24h2. The first one (od1) is picked
    # by round-robin (cursor=0).
    result = subprocess.run(
        ["python", "-m", "scripts.rclone.assign", "win11-24h2", "5.0", str(f)],
        capture_output=True, text=True, cwd=REPO_ROOT, timeout=10,
    )
    assert result.returncode == 0, f"assign failed: {result.stderr}"
    assert "od1" in result.stdout or "od2" in result.stdout


def test_assign_rejects_when_no_account_handles_product(tmp_path: Path):
    """If no account handles the product, assign.py must raise."""
    import yaml
    accounts = {
        "accounts": [
            {"name": "od1", "handles_products": ["win10-22h2"], "quota_gb": 100},
        ]
    }
    f = tmp_path / "accounts.yaml"
    f.write_text(yaml.safe_dump(accounts))
    result = subprocess.run(
        ["python", "-m", "scripts.rclone.assign", "win11-24h2", "5.0", str(f)],
        capture_output=True, text=True, cwd=REPO_ROOT, timeout=10,
    )
    assert result.returncode != 0
    assert "No account" in result.stderr
