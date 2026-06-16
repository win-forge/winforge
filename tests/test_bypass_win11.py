"""Tests for scripts.build.bypass_win11_requirements — uses a stub tool, no real WIMs."""
from __future__ import annotations
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from scripts.build import bypass_win11_requirements as bypass


def _proc(returncode: int = 0, stdout: str = "", stderr: str = "") -> MagicMock:
    p = MagicMock()
    p.returncode = returncode
    p.stdout = stdout
    p.stderr = stderr
    return p


def test_detect_tool_picks_dism_on_windows():
    with patch("scripts.build.bypass_win11_requirements.sys") as s:
        s.platform = "win32"
        assert bypass.detect_tool() == "dism"


def test_detect_tool_picks_wimlib_elsewhere():
    with patch("scripts.build.bypass_win11_requirements.sys") as s:
        s.platform = "linux"
        assert bypass.detect_tool() == "wimlib-imagex"


def test_get_wim_indexes_parses_dism():
    out = """
Deployment Image Servicing and Management tool
Details for image : install.wim

Index : 1
Name : Windows 11 Pro
Index : 2
Name : Windows 11 Home
"""
    with patch("scripts.build.bypass_win11_requirements.subprocess.run",
               return_value=_proc(0, out)) as r:
        idxs = bypass.get_wim_indexes(Path("install.wim"), "dism")
    assert idxs == [1, 2]
    args = r.call_args.args[0]
    assert args[0] == "dism"


def test_get_wim_indexes_parses_wimlib():
    out = """
Image Information:
Index: 1
Name: Windows 11 Pro
Index: 2
Name: Windows 11 Home
"""
    with patch("scripts.build.bypass_win11_requirements.subprocess.run",
               return_value=_proc(0, out)):
        idxs = bypass.get_wim_indexes(Path("install.wim"), "wimlib-imagex")
    assert idxs == [1, 2]


def test_get_wim_indexes_raises_on_nonzero():
    with patch("scripts.build.bypass_win11_requirements.subprocess.run",
               return_value=_proc(1, "", "boom")):
        with pytest.raises(RuntimeError, match="boom"):
            bypass.get_wim_indexes(Path("install.wim"), "dism")


def test_mount_wim_dism_cmd(tmp_path: Path):
    with patch("scripts.build.bypass_win11_requirements._run") as r:
        bypass.mount_wim(Path("wim"), tmp_path / "m", 2, "dism")
    cmd = r.call_args.args[0]
    assert cmd[0] == "dism"
    assert "/Mount-Wim" in cmd
    assert "/Index:2" in cmd
    assert "/ReadOnly:NO" in cmd
    assert any(s.startswith("/MountDir:") for s in cmd)
    assert any(s.startswith("/WimFile:") for s in cmd)


def test_mount_wim_wimlib_cmd(tmp_path: Path):
    with patch("scripts.build.bypass_win11_requirements._run") as r:
        bypass.mount_wim(Path("wim"), tmp_path / "m", 1, "wimlib-imagex")
    cmd = r.call_args.args[0]
    assert cmd[:3] == ["wimlib-imagex", "mount", "wim"]
    assert "--readwrite" in cmd


def test_unmount_wim_commits_dism():
    with patch("scripts.build.bypass_win11_requirements._run") as r:
        bypass.unmount_wim(Path("/m"), "dism", commit=True)
    cmd = r.call_args.args[0]
    assert "/Commit" in cmd
    assert "/Discard" not in cmd


def test_unmount_wim_discards_wimlib():
    with patch("scripts.build.bypass_win11_requirements._run") as r:
        bypass.unmount_wim(Path("/m"), "wimlib-imagex", commit=False)
    cmd = r.call_args.args[0]
    assert "--discard" in cmd


def test_patch_dlls_copies_present_files(tmp_path: Path):
    bypass_dir = tmp_path / "bypass"
    bypass_dir.mkdir()
    (bypass_dir / "appraiserres.dll").write_bytes(b"X" * 100)
    (bypass_dir / "appraiser.dll").write_bytes(b"Y" * 200)
    mount = tmp_path / "m"
    sys32 = mount / "Windows" / "System32"
    sys32.mkdir(parents=True)
    # Pre-existing (unpatched) stubs
    (sys32 / "appraiserres.dll").write_bytes(b"orig1")
    (sys32 / "appraiser.dll").write_bytes(b"orig2")
    patched = bypass.patch_dlls(mount, bypass_dir)
    assert sorted(patched) == ["appraiser.dll", "appraiserres.dll"]
    assert (sys32 / "appraiserres.dll").read_bytes() == b"X" * 100
    assert (sys32 / "appraiser.dll").read_bytes() == b"Y" * 200


def test_patch_dlls_skips_missing(tmp_path: Path):
    bypass_dir = tmp_path / "bypass"
    bypass_dir.mkdir()
    # Only one DLL provided
    (bypass_dir / "appraiser.dll").write_bytes(b"X" * 10)
    mount = tmp_path / "m"
    sys32 = mount / "Windows" / "System32"
    sys32.mkdir(parents=True)
    (sys32 / "appraiser.dll").write_bytes(b"orig")
    patched = bypass.patch_dlls(mount, bypass_dir)
    assert patched == ["appraiser.dll"]


def test_apply_no_dlls_skips_patch(tmp_path: Path):
    wim = tmp_path / "install.wim"
    wim.write_bytes(b"FAKE")
    mount = tmp_path / "m"
    with patch("scripts.build.bypass_win11_requirements.get_wim_indexes",
               return_value=[1, 2]), \
         patch("scripts.build.bypass_win11_requirements.mount_wim") as m, \
         patch("scripts.build.bypass_win11_requirements.unmount_wim") as u, \
         patch("scripts.build.bypass_win11_requirements.detect_tool",
               return_value="dism"):
        summary = bypass.apply(bypass.BypassConfig(wim_path=wim, mount_dir=mount, bypass_dir=None))
    assert summary["dll_patch"] is False
    assert summary["dlls_patched"] == []
    assert summary["indexes"] == [1, 2]
    assert m.call_count == 2
    assert u.call_count == 2


def test_apply_with_dlls_patches_each_index(tmp_path: Path):
    wim = tmp_path / "install.wim"
    wim.write_bytes(b"FAKE")
    mount = tmp_path / "m"
    bypass_dir = tmp_path / "bypass"
    bypass_dir.mkdir()
    (bypass_dir / "appraiserres.dll").write_bytes(b"PATCHED_RES")
    with patch("scripts.build.bypass_win11_requirements.get_wim_indexes",
               return_value=[1]), \
         patch("scripts.build.bypass_win11_requirements.mount_wim") as m, \
         patch("scripts.build.bypass_win11_requirements.unmount_wim") as u, \
         patch("scripts.build.bypass_win11_requirements.detect_tool",
               return_value="dism"):
        summary = bypass.apply(bypass.BypassConfig(
            wim_path=wim, mount_dir=mount, bypass_dir=bypass_dir,
        ))
    assert summary["dll_patch"] is True
    assert summary["dlls_patched"] == ["appraiserres.dll"]
    m.assert_called_once()
    u.assert_called_once()

def test_apply_unmounts_with_discard_on_mount_failure(tmp_path: Path):
    wim = tmp_path / "install.wim"
    wim.write_bytes(b"FAKE")
    mount = tmp_path / "m"
    with patch("scripts.build.bypass_win11_requirements.get_wim_indexes",
               return_value=[1]), \
         patch("scripts.build.bypass_win11_requirements.mount_wim",
               side_effect=RuntimeError("boom")), \
         patch("scripts.build.bypass_win11_requirements.unmount_wim") as u, \
         patch("scripts.build.bypass_win11_requirements.detect_tool",
               return_value="dism"):
        with pytest.raises(RuntimeError, match="boom"):
            bypass.apply(bypass.BypassConfig(wim_path=wim, mount_dir=mount, bypass_dir=None))
    u.assert_called_once()
    assert "commit=False" in str(u.call_args) or "--discard" in str(u.call_args) or "/Discard" in str(u.call_args)
