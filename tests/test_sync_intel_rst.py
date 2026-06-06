from pathlib import Path
import pytest
from scripts.drivers.sync_intel_rst import extract_version_from_filename, find_driver_root

def test_version_extraction():
    assert extract_version_from_filename("SetupRST_19.5.2.1049.exe") == "19.5.2.1049"
    assert extract_version_from_filename("SetupRST_19.0.0.1064.exe") == "19.0.0.1064"

def test_find_driver_root_picks_deepest_iaStorAC(tmp_path: Path):
    (tmp_path / "drivers" / "iaStorAC" / "x64").mkdir(parents=True)
    (tmp_path / "drivers" / "iaStorAC" / "x64" / "iaStorAC.inf").write_text("; stub")
    (tmp_path / "drivers" / "iaAHCIC" / "x64").mkdir(parents=True)
    root = find_driver_root(tmp_path)
    assert (root / "iaStorAC" / "x64" / "iaStorAC.inf").exists()
