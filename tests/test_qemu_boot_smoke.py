"""Unit tests for the QEMU boot smoke harness.

These cover the pure-logic parts that don't need QEMU installed:
PPM frame classification, the setup-splash heuristic, graceful
degradation without qemu, and report assembly. The actual boot legs
run in the boot-smoke workflow (and locally with qemu + ovmf).
"""

from __future__ import annotations

import json
import random
from pathlib import Path

import pytest

from scripts.test.qemu_boot_smoke import (
    classify_frame,
    looks_like_setup,
    main,
    run_leg,
)


def _ppm(w: int, h: int, pixfn) -> bytes:
    header = f"P6\n{w} {h}\n255\n".encode()
    body = bytearray()
    for y in range(h):
        for x in range(w):
            body += pixfn(x, y)
    return header + bytes(body)


def _flat_frame() -> bytes:
    """Firmware-style splash: solid background + small white logo box."""
    return _ppm(64, 48, lambda x, y: b"\x10\x30\x90" if not (20 <= x < 44 and 18 <= y < 26) else b"\xff\xff\xff")


def _busy_frame() -> bytes:
    """Setup-style screen: high-entropy graphical content."""
    rng = random.Random(7)
    return _ppm(64, 48, lambda x, y: bytes([rng.randrange(256) for _ in range(3)]))


# ---------------------------------------------------------------------------
# PPM parsing + classification
# ---------------------------------------------------------------------------

def test_flat_firmware_frame_is_not_setup(tmp_path: Path):
    f = tmp_path / "flat.ppm"
    f.write_bytes(_flat_frame())
    m = classify_frame(f)
    assert "error" not in m
    assert int(m["distinct_colors"]) <= 4  # bg + white box (+ antialias-free edges)
    assert not looks_like_setup(m)


def test_busy_setup_frame_detected(tmp_path: Path):
    f = tmp_path / "busy.ppm"
    f.write_bytes(_busy_frame())
    m = classify_frame(f)
    assert int(m["distinct_colors"]) >= 256
    assert float(m["color_spread"]) >= 25.0
    assert looks_like_setup(m)


def test_qemu_comment_header_parses(tmp_path: Path):
    """QMP screendumps may carry a '# comment' line inside the header."""
    f = tmp_path / "qemu.ppm"
    f.write_bytes(b"P6\n# Created by QMP screendump\n64 48\n255\n" + _busy_frame().split(b"255\n", 1)[1])
    m = classify_frame(f)
    assert "error" not in m
    assert int(m["w"]) == 64 and int(m["h"]) == 48
    assert looks_like_setup(m)


def test_maxval_on_dimension_line_parses(tmp_path: Path):
    """Legal PPM variant where '255' shares the dimensions line."""
    f = tmp_path / "alt.ppm"
    f.write_bytes(b"P6\n64 48 255\n" + _busy_frame().split(b"255\n", 1)[1])
    m = classify_frame(f)
    assert "error" not in m
    assert looks_like_setup(m)


def test_garbage_file_returns_error_not_crash(tmp_path: Path):
    f = tmp_path / "bad.ppm"
    f.write_bytes(b"not a ppm at all")
    m = classify_frame(f)
    assert "error" in m
    assert not looks_like_setup(m)  # errors must classify as NOT-setup


def test_truncated_pixel_data_doesnt_crash(tmp_path: Path):
    f = tmp_path / "trunc.ppm"
    blob = _busy_frame()
    f.write_bytes(blob[: len(blob) // 2])
    m = classify_frame(f)  # must not raise; short reads are tolerated
    assert isinstance(m, dict)


# ---------------------------------------------------------------------------
# run_leg without qemu (graceful degradation)
# ---------------------------------------------------------------------------

def test_run_leg_fails_gracefully_without_qemu(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import shutil

    monkeypatch.setattr(shutil, "which", lambda name: None)
    r = run_leg(Path("/nonexistent.iso"), "uefi", tmp_path)
    assert r.booted is False
    assert "not installed" in r.reason


def test_run_leg_uefi_requires_ovmf_vars(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """UEFI leg needs OVMF_CODE.fd present; missing firmware = clean failure."""
    import shutil

    monkeypatch.setattr(shutil, "which", lambda name: "qemu-system-x86_64")
    monkeypatch.setattr("scripts.test.qemu_boot_smoke.Path.exists", lambda self: False)
    r = run_leg(Path("/nonexistent.iso"), "uefi", tmp_path)
    assert r.booted is False
    assert "OVMF" in r.reason or "missing" in r.reason


# ---------------------------------------------------------------------------
# CLI/report assembly (no qemu needed — both legs fail gracefully)
# ---------------------------------------------------------------------------

def test_main_writes_report_and_exits_1_without_qemu(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
):
    import shutil

    iso = tmp_path / "fake.iso"
    iso.write_bytes(b"")
    outdir = tmp_path / "smoke"

    monkeypatch.setattr(shutil, "which", lambda name: None)
    rc = main([str(iso), "--outdir", str(outdir)])
    assert rc == 1
    report = json.loads((outdir / "smoke-report.json").read_text())
    assert report["all_ok"] is False
    assert [leg["leg"] for leg in report["legs"]] == ["uefi", "bios"]
    captured = capsys.readouterr()
    assert "BOOTED" not in captured.err


def test_main_single_leg_selection(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import shutil

    iso = tmp_path / "fake.iso"
    iso.write_bytes(b"")
    outdir = tmp_path / "smoke"

    monkeypatch.setattr(shutil, "which", lambda name: None)
    rc = main([str(iso), "--legs", "bios", "--outdir", str(outdir)])
    assert rc == 1
    report = json.loads((outdir / "smoke-report.json").read_text())
    assert [leg["leg"] for leg in report["legs"]] == ["bios"]
