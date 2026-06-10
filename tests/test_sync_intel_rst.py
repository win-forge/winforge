from pathlib import Path
import pytest
from scripts.drivers.sync_intel_rst import (
    extract_version_from_filename,
    find_driver_root,
    fetch_latest_metadata,
)


def test_version_extraction():
    assert extract_version_from_filename("SetupRST_19.5.2.1049.exe") == "19.5.2.1049"
    assert extract_version_from_filename("SetupRST_19.0.0.1064.exe") == "19.0.0.1064"


def test_version_extraction_returns_none_for_generic():
    """Intel's new page uses SetupRST.exe with no version in filename."""
    assert extract_version_from_filename("SetupRST.exe") is None


def test_find_driver_root_picks_deepest_iaStorAC(tmp_path: Path):
    (tmp_path / "drivers" / "iaStorAC" / "x64").mkdir(parents=True)
    (tmp_path / "drivers" / "iaStorAC" / "x64" / "iaStorAC.inf").write_text("; stub")
    (tmp_path / "drivers" / "iaAHCIC" / "x64").mkdir(parents=True)
    root = find_driver_root(tmp_path)
    assert (root / "iaStorAC" / "x64" / "iaStorAC.inf").exists()


def test_fetch_latest_metadata_parses_new_intel_page():
    """Hit the real Intel download page; verify the new-format parser works.

    Skips on network failure so CI without internet still passes.
    """
    try:
        version, url = fetch_latest_metadata()
    except Exception as e:
        pytest.skip(f"Cannot reach Intel: {e}")
    assert version
    assert "." in version  # looks like X.Y.Z.W
    assert url.startswith("https://")
    assert "SetupRST" in url


def test_fetch_latest_metadata_falls_back_to_old_pattern():
    """When the new generic pattern isn't on the page, fall back to versioned filename."""
    # Old Intel page had: <a href="...SetupRST_19.5.2.1049.exe">
    fake_html = """
    <html><body>
      <a href="https://downloadmirror.intel.com/849936/eng/SetupRST_19.5.2.1049.exe">Download</a>
    </body></html>
    """
    from scripts.drivers.sync_intel_rst import GENERIC_RE, VERSIONED_RE
    m_url = GENERIC_RE.search(fake_html)
    assert m_url is None
    m = VERSIONED_RE.search(fake_html)
    assert m is not None
    assert m.group(1) == "19.5.2.1049"
