"""Tests for scripts.build.bypass_policy."""
from __future__ import annotations
from pathlib import Path

import pytest

from scripts.build.bypass_policy import needs_dll_bypass, dlls_available, check


# --- needs_dll_bypass ---

def test_enterprise_24h2_needs_dll(tmp_path: Path):
    editions = tmp_path / "editions.yaml"
    editions.write_text(
        "editions:\n"
        "  win11-24h2:\n"
        "    - id: professional\n"
        "      label: Pro\n"
        "    - id: enterprise\n"
        "      label: Enterprise\n"
        "      needs_dll_bypass: true\n"
    )
    assert needs_dll_bypass("win11-24h2", "enterprise", editions) is True


def test_pro_24h2_does_not_need_dll(tmp_path: Path):
    editions = tmp_path / "editions.yaml"
    editions.write_text(
        "editions:\n"
        "  win11-24h2:\n"
        "    - id: professional\n"
        "      label: Pro\n"
        "    - id: enterprise\n"
        "      label: Enterprise\n"
        "      needs_dll_bypass: true\n"
    )
    assert needs_dll_bypass("win11-24h2", "professional", editions) is False


def test_missing_flag_defaults_false(tmp_path: Path):
    editions = tmp_path / "editions.yaml"
    editions.write_text(
        "editions:\n"
        "  win11-24h2:\n"
        "    - id: professional\n"
        "      label: Pro\n"
    )
    assert needs_dll_bypass("win11-24h2", "professional", editions) is False


def test_win10_never_needs_dll(tmp_path: Path):
    editions = tmp_path / "editions.yaml"
    editions.write_text(
        "editions:\n"
        "  win10-22h2:\n"
        "    - id: enterprise\n"
        "      label: Enterprise\n"
        "      needs_dll_bypass: true\n"  # even if set, win10 ignores
    )
    assert needs_dll_bypass("win10-22h2", "enterprise", editions) is False


def test_iot_ltsc_needs_dll(tmp_path: Path):
    editions = tmp_path / "editions.yaml"
    editions.write_text(
        "editions:\n"
        "  win11-25h2:\n"
        "    - id: iotenterprise\n"
        "      label: IoT Enterprise LTSC\n"
        "      needs_dll_bypass: true\n"
    )
    assert needs_dll_bypass("win11-25h2", "iotenterprise", editions) is True


def test_unknown_edition_returns_false(tmp_path: Path):
    editions = tmp_path / "editions.yaml"
    editions.write_text(
        "editions:\n"
        "  win11-24h2:\n"
        "    - id: professional\n"
        "      label: Pro\n"
    )
    assert needs_dll_bypass("win11-24h2", "nonexistent", editions) is False


def test_missing_editions_file_returns_false(tmp_path: Path):
    assert needs_dll_bypass("win11-24h2", "enterprise", tmp_path / "nope.yaml") is False


# --- dlls_available ---

def test_dlls_available_both_present(tmp_path: Path):
    bypass_root = tmp_path / "bypass"
    product_dir = bypass_root / "win11-24h2"
    product_dir.mkdir(parents=True)
    (product_dir / "appraiserres.dll").write_bytes(b"X")
    (product_dir / "appraiser.dll").write_bytes(b"Y")
    assert dlls_available("win11-24h2", bypass_root) is True


def test_dlls_available_missing_one(tmp_path: Path):
    bypass_root = tmp_path / "bypass"
    product_dir = bypass_root / "win11-24h2"
    product_dir.mkdir(parents=True)
    (product_dir / "appraiserres.dll").write_bytes(b"X")
    # appraiser.dll missing
    assert dlls_available("win11-24h2", bypass_root) is False


def test_dlls_available_missing_dir(tmp_path: Path):
    assert dlls_available("win11-24h2", tmp_path / "bypass") is False


# --- check (integration) ---

def test_check_action_skip_when_not_needed(tmp_path: Path):
    editions = tmp_path / "editions.yaml"
    editions.write_text(
        "editions:\n"
        "  win11-24h2:\n"
        "    - id: professional\n"
        "      label: Pro\n"
    )
    bypass_root = tmp_path / "bypass"
    result = check("win11-24h2", "professional", bypass_root, editions)
    assert result["action"] == "skip"
    assert result["needs_dll_bypass"] is False


def test_check_action_patch_when_needed_and_available(tmp_path: Path):
    editions = tmp_path / "editions.yaml"
    editions.write_text(
        "editions:\n"
        "  win11-24h2:\n"
        "    - id: enterprise\n"
        "      label: Enterprise\n"
        "      needs_dll_bypass: true\n"
    )
    bypass_root = tmp_path / "bypass"
    product_dir = bypass_root / "win11-24h2"
    product_dir.mkdir(parents=True)
    (product_dir / "appraiserres.dll").write_bytes(b"X")
    (product_dir / "appraiser.dll").write_bytes(b"Y")
    result = check("win11-24h2", "enterprise", bypass_root, editions)
    assert result["action"] == "patch"
    assert result["needs_dll_bypass"] is True
    assert result["dlls_available"] is True


def test_check_action_fail_when_needed_but_missing(tmp_path: Path):
    editions = tmp_path / "editions.yaml"
    editions.write_text(
        "editions:\n"
        "  win11-24h2:\n"
        "    - id: enterprise\n"
        "      label: Enterprise\n"
        "      needs_dll_bypass: true\n"
    )
    bypass_root = tmp_path / "bypass"
    # No DLLs staged
    result = check("win11-24h2", "enterprise", bypass_root, editions)
    assert result["action"] == "fail"
    assert result["needs_dll_bypass"] is True
    assert result["dlls_available"] is False


def test_check_action_skip_for_win10_even_if_dlls_present(tmp_path: Path):
    editions = tmp_path / "editions.yaml"
    editions.write_text(
        "editions:\n"
        "  win10-22h2:\n"
        "    - id: enterprise\n"
        "      label: Enterprise\n"
        "      needs_dll_bypass: true\n"
    )
    bypass_root = tmp_path / "bypass"
    product_dir = bypass_root / "win10-22h2"
    product_dir.mkdir(parents=True)
    (product_dir / "appraiserres.dll").write_bytes(b"X")
    (product_dir / "appraiser.dll").write_bytes(b"Y")
    result = check("win10-22h2", "enterprise", bypass_root, editions)
    assert result["action"] == "skip"
    assert result["needs_dll_bypass"] is False


# --- CLI ---

def test_cli_returns_0_for_skip(tmp_path: Path, capsys):
    editions = tmp_path / "editions.yaml"
    editions.write_text(
        "editions:\n"
        "  win11-24h2:\n"
        "    - id: professional\n"
        "      label: Pro\n"
    )
    from scripts.build.bypass_policy import main
    rc = main([
        "--product", "win11-24h2",
        "--edition", "professional",
        "--bypass-root", str(tmp_path / "bypass"),
        "--editions-file", str(editions),
    ])
    assert rc == 0
    out = capsys.readouterr().out
    assert "action=skip" in out


def test_cli_returns_1_for_fail(tmp_path: Path, capsys):
    editions = tmp_path / "editions.yaml"
    editions.write_text(
        "editions:\n"
        "  win11-24h2:\n"
        "    - id: enterprise\n"
        "      label: Enterprise\n"
        "      needs_dll_bypass: true\n"
    )
    from scripts.build.bypass_policy import main
    rc = main([
        "--product", "win11-24h2",
        "--edition", "enterprise",
        "--bypass-root", str(tmp_path / "bypass"),
        "--editions-file", str(editions),
    ])
    assert rc == 1
    out = capsys.readouterr().out
    assert "action=fail" in out
