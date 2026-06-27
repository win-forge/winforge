"""Bootability tests under a variety of conditions.

Existing test files cover:
- test_verify_iso_bootable.py: parser correctness on synthetic byte fixtures
- test_repack_uefi.py: per-builder output (xorriso→uefi, genisoimage→bios-only)

This file covers the GAPS — conditions not yet tested:

A. Round-trip content verification
   After repack, extract the ISO and confirm autounattend.xml + install.wim
   are actually embedded with the right content. A bootable ISO with the
   wrong files inside is worse than a non-bootable one — it boots but
   installs the wrong thing.

B. Corrupt / malformed input handling
   repack.sh and verify_iso_bootable should fail gracefully, not crash
   with a traceback, when fed garbage. A truncated download or a 7z
   extraction error should produce a clear exit code, not a Python
   exception that masks the real problem.

C. Parser edge cases beyond the existing byte fixtures
   - UEFI-only catalog (no BIOS default entry — the oscdimg 1-entry path)
   - Multiple EFI sections (entry_count > 1)
   - Catalog at a high LBA (stress the seek logic)
   - Empty file / zero-byte file

D. Real builder output structure verification
   Existing tests check uefi=True/False from the parser; these tests
   dump the actual catalog bytes from a real xorriso-built ISO and
   verify the structure matches the El Torito spec — not just that the
   parser says "ok". Catches parser-vs-reality mismatches where both
   are wrong in the same way (the exact bug class documented in the
   skill's SectionEntries pitfall).
"""
from __future__ import annotations

import os
import shutil
import struct
import subprocess
from pathlib import Path

import pytest

from scripts.build.verify_iso_bootable import (
    verify_iso_bootable,
    debug_dump_catalog,
)

REPO_ROOT = Path(__file__).parent.parent
REPACK_SH = REPO_ROOT / "scripts/build/repack.sh"
SECTOR = 2048


# ---------------------------------------------------------------------------
# Helpers (shared with test_repack_uefi.py but self-contained here so this
# file can be run independently)
# ---------------------------------------------------------------------------

def _have_7z() -> bool:
    return any(shutil.which(t) for t in ("7z", "7zr", "7za"))


def _make_source_fixture(root: Path) -> Path:
    """Minimal directory tree repack.sh requires."""
    (root / "efi/microsoft/boot").mkdir(parents=True)
    (root / "efi/microsoft/boot/efisys.bin").write_bytes(b"\x00" * 2048)
    (root / "boot").mkdir()
    (root / "boot/etfsboot.com").write_bytes(b"\x00" * 2048)
    (root / "sources").mkdir()
    (root / "sources/install.wim").write_bytes(b"\x00" * 4096)
    return root


def _make_source_iso(src_dir: Path, out_iso: Path) -> Path:
    """Build a source ISO using xorriso (preferred) or genisoimage.

    -R -J (Rock Ridge + Joliet) preserve lowercase directory names so
    repack.sh's ``cp "$WIM_IN" "$WORK/sources/install.wim"`` works.
    """
    builder = None
    for b in ("xorriso", "genisoimage"):
        if shutil.which(b):
            builder = b
            break
    if not builder:
        pytest.skip("no ISO builder available (need xorriso or genisoimage)")

    rr_joliet = ["-R", "-J"]
    cmd = [builder, "-o", str(out_iso), "-V", "SRC_FX", *rr_joliet]
    if builder == "xorriso":
        cmd = ["xorriso", "-as", "mkisofs", "-o", str(out_iso), "-V", "SRC_FX", *rr_joliet]
    subprocess.run(cmd + [str(src_dir)], check=True, capture_output=True, timeout=60)
    return out_iso


# ---------------------------------------------------------------------------
# A. Round-trip content verification
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _have_7z(), reason="7z not installed")
class TestRoundTripContent:
    """Verify that files embedded by repack.sh survive the ISO round-trip."""

    def test_autounattend_embedded_after_repack(self, tmp_path: Path):
        """repack.sh copies autounattend.xml into the ISO root.
        After building, extract and confirm it's there with the right content.
        """
        if not shutil.which("xorriso"):
            pytest.skip("xorriso not installed")

        src_dir = _make_source_fixture(tmp_path / "src")
        src_iso = _make_source_iso(src_dir, tmp_path / "src.iso")

        autou_content = '<?xml version="1.0"?><unattend><test>ROUNDTRIP</test></unattend>'
        autou = tmp_path / "autounattend.xml"
        autou.write_text(autou_content)

        fake_wim = tmp_path / "install.wim"
        fake_wim.write_bytes(b"WIM_MARKER" * 100)

        out_iso = tmp_path / "out.iso"
        subprocess.run(
            ["bash", str(REPACK_SH), str(src_iso), str(out_iso),
             str(fake_wim), str(autou)],
            check=True, capture_output=True, timeout=120,
        )

        # Extract and verify
        extract_dir = tmp_path / "extract"
        subprocess.run(
            ["7z", "x", str(out_iso), f"-o{extract_dir}", "-bd", "-y"],
            check=True, capture_output=True, timeout=60,
        )
        embedded_autou = extract_dir / "autounattend.xml"
        assert embedded_autou.exists(), "autounattend.xml not found in built ISO"
        assert embedded_autou.read_text() == autou_content, (
            "autounattend.xml content mismatch — repack.sh may have copied "
            "the wrong file or corrupted it during ISO build."
        )

    def test_install_wim_replaced_after_repack(self, tmp_path: Path):
        """repack.sh replaces sources/install.wim with the patched one.
        Confirm the WIM in the output ISO is the one we supplied, not the
        original from the source ISO.
        """
        if not shutil.which("xorriso"):
            pytest.skip("xorriso not installed")

        src_dir = _make_source_fixture(tmp_path / "src")
        src_iso = _make_source_iso(src_dir, tmp_path / "src.iso")

        # Use a distinctive marker so we can tell the patched WIM from the original
        wim_marker = b"PATCHED_WIM_MARKER_12345" + b"\x00" * 9000
        fake_wim = tmp_path / "install.wim"
        fake_wim.write_bytes(wim_marker)

        autou = tmp_path / "autounattend.xml"
        autou.write_text('<?xml version="1.0"?><unattend/>')

        out_iso = tmp_path / "out.iso"
        subprocess.run(
            ["bash", str(REPACK_SH), str(src_iso), str(out_iso),
             str(fake_wim), str(autou)],
            check=True, capture_output=True, timeout=120,
        )

        extract_dir = tmp_path / "extract"
        subprocess.run(
            ["7z", "x", str(out_iso), f"-o{extract_dir}", "-bd", "-y"],
            check=True, capture_output=True, timeout=60,
        )
        embedded_wim = extract_dir / "sources" / "install.wim"
        assert embedded_wim.exists(), "sources/install.wim not found in built ISO"
        assert embedded_wim.read_bytes() == wim_marker, (
            "install.wim in the output ISO doesn't match the patched WIM. "
            "repack.sh may have failed to replace it."
        )

    def test_efisys_bin_preserved_after_repack(self, tmp_path: Path):
        """The EFI boot image must survive the repack — it's what makes
        the ISO UEFI-bootable. If repack.sh drops it, UEFI boot fails.
        """
        if not shutil.which("xorriso"):
            pytest.skip("xorriso not installed")

        src_dir = _make_source_fixture(tmp_path / "src")
        # Write a distinctive efisys.bin so we can verify it survived
        efi_content = b"EFI_BOOT_MARKER" + b"\x00" * 2032
        (src_dir / "efi/microsoft/boot/efisys.bin").write_bytes(efi_content)
        src_iso = _make_source_iso(src_dir, tmp_path / "src.iso")

        fake_wim = tmp_path / "install.wim"
        fake_wim.write_bytes(b"\x00" * 1024)
        autou = tmp_path / "autounattend.xml"
        autou.write_text('<?xml version="1.0"?><unattend/>')

        out_iso = tmp_path / "out.iso"
        subprocess.run(
            ["bash", str(REPACK_SH), str(src_iso), str(out_iso),
             str(fake_wim), str(autou)],
            check=True, capture_output=True, timeout=120,
        )

        extract_dir = tmp_path / "extract"
        subprocess.run(
            ["7z", "x", str(out_iso), f"-o{extract_dir}", "-bd", "-y"],
            check=True, capture_output=True, timeout=60,
        )
        embedded_efi = extract_dir / "efi/microsoft/boot/efisys.bin"
        assert embedded_efi.exists(), "efisys.bin not found in built ISO"
        assert embedded_efi.read_bytes() == efi_content, (
            "efisys.bin content changed during repack — the EFI boot image "
            "may have been corrupted or replaced."
        )


# ---------------------------------------------------------------------------
# B. Corrupt / malformed input handling
# ---------------------------------------------------------------------------

class TestCorruptInput:
    """repack.sh and verify_iso_bootable should fail gracefully on garbage."""

    def test_repack_fails_on_non_iso_input(self, tmp_path: Path):
        """Feed repack.sh a plain text file as the source ISO.
        7z should fail to extract, and repack.sh should exit non-zero.
        """
        fake_iso = tmp_path / "not-an-iso.txt"
        fake_iso.write_text("this is not an ISO file")

        out_iso = tmp_path / "out.iso"
        fake_wim = tmp_path / "install.wim"
        fake_wim.write_bytes(b"\x00" * 1024)
        autou = tmp_path / "autounattend.xml"
        autou.write_text('<?xml version="1.0"?><unattend/>')

        result = subprocess.run(
            ["bash", str(REPACK_SH), str(fake_iso), str(out_iso),
             str(fake_wim), str(autou)],
            capture_output=True, timeout=30,
        )
        assert result.returncode != 0, (
            "repack.sh should fail on non-ISO input, not silently produce garbage"
        )

    def test_repack_fails_on_missing_source_iso(self, tmp_path: Path):
        """Source ISO path doesn't exist — repack.sh should fail fast."""
        out_iso = tmp_path / "out.iso"
        fake_wim = tmp_path / "install.wim"
        fake_wim.write_bytes(b"\x00" * 1024)
        autou = tmp_path / "autounattend.xml"
        autou.write_text('<?xml version="1.0"?><unattend/>')

        result = subprocess.run(
            ["bash", str(REPACK_SH), str(tmp_path / "nonexistent.iso"),
             str(out_iso), str(fake_wim), str(autou)],
            capture_output=True, timeout=30,
        )
        assert result.returncode != 0

    def test_verify_empty_file_raises(self, tmp_path: Path):
        """Zero-byte file — parser should raise, not crash with IndexError."""
        empty = tmp_path / "empty.iso"
        empty.write_bytes(b"")
        with pytest.raises((ValueError, FileNotFoundError)):
            verify_iso_bootable(empty)

    def test_verify_truncated_iso_raises(self, tmp_path: Path):
        """File too small to contain sector 16 — parser should raise."""
        tiny = tmp_path / "tiny.iso"
        tiny.write_bytes(b"\x00" * 100)  # way less than one sector
        with pytest.raises((ValueError, FileNotFoundError)):
            verify_iso_bootable(tiny)

    def test_verify_random_bytes_raises(self, tmp_path: Path):
        """File big enough for sectors but no ISO 9660 structure."""
        junk = tmp_path / "junk.iso"
        junk.write_bytes(os.urandom(20 * SECTOR))  # 20 sectors of random data
        with pytest.raises(ValueError, match="not an ISO 9660 image"):
            verify_iso_bootable(junk)

    def test_verify_iso_with_no_boot_record(self, tmp_path: Path):
        """Valid PVD but sector 17 is not an El Torito Boot Record."""
        iso = tmp_path / "nobr.iso"
        with iso.open("wb") as f:
            f.write(b"\x00" * (16 * SECTOR))
            # PVD
            pvd = bytearray(SECTOR)
            pvd[0] = 0x01
            pvd[1:6] = b"CD001"
            f.write(pvd)
            # Sector 17: not a boot record (wrong magic)
            br = bytearray(SECTOR)
            br[0] = 0x02  # not 0x00 (Boot Record type)
            br[1:6] = b"CD001"
            f.write(br)
        result = verify_iso_bootable(iso)
        assert result.uefi is False
        assert result.bios is False
        assert result.errors  # should have error messages

    def test_verify_iso_with_boot_record_but_no_el_torito_signature(self, tmp_path: Path):
        """Sector 17 has CD001 magic but not the EL TORITO SPECIFICATION string."""
        iso = tmp_path / "noeltorito.iso"
        with iso.open("wb") as f:
            f.write(b"\x00" * (16 * SECTOR))
            pvd = bytearray(SECTOR)
            pvd[0] = 0x01
            pvd[1:6] = b"CD001"
            f.write(pvd)
            br = bytearray(SECTOR)
            br[0] = 0x00
            br[1:6] = b"CD001"
            # Don't write "EL TORITO SPECIFICATION" — leave zeros
            f.write(br)
        result = verify_iso_bootable(iso)
        assert result.uefi is False
        assert result.bios is False
        assert result.errors


# ---------------------------------------------------------------------------
# C. Parser edge cases — UEFI-only, multiple entries, high LBA
# ---------------------------------------------------------------------------

def _zero_sector() -> bytes:
    return b"\x00" * SECTOR


def _pvd() -> bytes:
    pvd = bytearray(_zero_sector())
    pvd[0] = 0x01
    pvd[1:6] = b"CD001"
    return bytes(pvd)


def _br(catalog_lba: int) -> bytes:
    br = bytearray(_zero_sector())
    br[0] = 0x00
    br[1:6] = b"CD001"
    for i, ch in enumerate(b"EL TORITO SPECIFICATION"):
        br[7 + i] = ch
    br[0x47:0x4B] = struct.pack("<I", catalog_lba)
    return bytes(br)


def _validation_entry() -> bytes:
    e = bytearray(32)
    e[0] = 0x01
    e[1] = 0x00
    return bytes(e)


def _default_entry(bootable: bool = True, rba: int = 100) -> bytes:
    e = bytearray(32)
    e[0] = 0x88 if bootable else 0x00
    e[8:12] = struct.pack("<I", rba)
    return bytes(e)


def _efi_section_header(entry_count: int, platform: int = 0xEF) -> bytes:
    e = bytearray(32)
    e[0] = 0x91
    e[1] = platform
    e[2:4] = struct.pack("<H", entry_count)
    return bytes(e)


def _build_iso_bytes(
    tmp_path: Path,
    *,
    catalog_lba: int = 18,
    catalog: bytes | None = None,
) -> Path:
    """Write a minimal ISO with a custom boot catalog."""
    iso = tmp_path / "test.iso"
    if catalog is None:
        catalog = (
            _validation_entry()
            + _default_entry()
            + _efi_section_header(1)
        ).ljust(SECTOR, b"\x00")

    with iso.open("wb") as f:
        f.write(b"\x00" * (16 * SECTOR))
        f.write(_pvd())
        f.write(_br(catalog_lba))
        written = 18
        while written < catalog_lba:
            f.write(_zero_sector())
            written += 1
        f.write(catalog.ljust(SECTOR, b"\x00"))
        while written + 1 < max(catalog_lba + 1, 19):
            f.write(_zero_sector())
            written += 1
    return iso


class TestParserEdgeCases:
    """Parser behavior on catalog shapes not covered by existing fixtures."""

    def test_uefi_only_no_bios_default_entry(self, tmp_path: Path):
        """Catalog with no BIOS default entry (0x00) but valid EFI section.
        This is the oscdimg 1-entry UEFI-only path. BIOS won't boot, but
        UEFI should. The parser should report bios=False, uefi=True.
        """
        catalog = (
            _validation_entry()
            + _default_entry(bootable=False)
            + _efi_section_header(1)
        ).ljust(SECTOR, b"\x00")
        iso = _build_iso_bytes(tmp_path, catalog=catalog)
        result = verify_iso_bootable(iso)
        assert result.uefi is True, "UEFI should boot — EFI section is present with entry_count=1"
        assert result.bios is False, "BIOS should not boot — default entry is 0x00"
        assert result.ok is True, "ok is True iff uefi=True AND no errors"

    def test_efi_entry_count_greater_than_one(self, tmp_path: Path):
        """entry_count=2 — multiple EFI boot entries. Some ISOs have
        multiple EFI boot images (e.g. fallback + primary). Parser should
        report uefi=True for any entry_count >= 1.
        """
        catalog = (
            _validation_entry()
            + _default_entry()
            + _efi_section_header(2)
        ).ljust(SECTOR, b"\x00")
        iso = _build_iso_bytes(tmp_path, catalog=catalog)
        result = verify_iso_bootable(iso)
        assert result.uefi is True
        assert result.bios is True

    def test_efi_entry_count_255(self, tmp_path: Path):
        """Maximum entry_count for UINT16 — stress test the parser."""
        catalog = (
            _validation_entry()
            + _default_entry()
            + _efi_section_header(255)
        ).ljust(SECTOR, b"\x00")
        iso = _build_iso_bytes(tmp_path, catalog=catalog)
        result = verify_iso_bootable(iso)
        assert result.uefi is True

    def test_catalog_at_high_lba(self, tmp_path: Path):
        """Catalog at LBA 1000 — stress the seek logic. Real ISOs
        sometimes place the catalog far from the volume descriptors.
        """
        catalog = (
            _validation_entry()
            + _default_entry()
            + _efi_section_header(1)
        ).ljust(SECTOR, b"\x00")
        iso = _build_iso_bytes(tmp_path, catalog_lba=1000, catalog=catalog)
        result = verify_iso_bootable(iso)
        assert result.uefi is True
        assert result.bios is True

    def test_no_section_header_at_all(self, tmp_path: Path):
        """Catalog with only validation + default entry, no section header.
        The bytes at 64..95 are zeros (not 0x91). This is a BIOS-only ISO.
        """
        catalog = (
            _validation_entry()
            + _default_entry()
            + b"\x00" * 32  # no section header
        ).ljust(SECTOR, b"\x00")
        iso = _build_iso_bytes(tmp_path, catalog=catalog)
        result = verify_iso_bootable(iso)
        assert result.bios is True
        assert result.uefi is False
        assert any("No EFI section header" in w for w in result.warnings)

    def test_validation_entry_wrong_id(self, tmp_path: Path):
        """Catalog byte 0 is not 0x01 (validation entry header).
        The catalog is malformed — parser should report errors.
        """
        bad_validation = bytearray(_validation_entry())
        bad_validation[0] = 0x00  # wrong — should be 0x01
        catalog = (
            bytes(bad_validation)
            + _default_entry()
            + _efi_section_header(1)
        ).ljust(SECTOR, b"\x00")
        iso = _build_iso_bytes(tmp_path, catalog=catalog)
        result = verify_iso_bootable(iso)
        assert result.errors, "Should have errors when validation entry is malformed"

    def test_debug_dump_catalog_returns_valid_dict(self, tmp_path: Path):
        """debug_dump_catalog should return a dict with the expected keys
        and correct values for a known-good ISO.
        """
        catalog = (
            _validation_entry()
            + _default_entry(bootable=True, rba=200)
            + _efi_section_header(1)
        ).ljust(SECTOR, b"\x00")
        iso = _build_iso_bytes(tmp_path, catalog_lba=18, catalog=catalog)
        dump = debug_dump_catalog(iso)
        assert dump["catalog_lba"] == 18
        assert dump["section_byte0"] == "0x91"
        assert dump["section_byte1"] == "0xef"
        assert dump["section_entries_int"] == 1


# ---------------------------------------------------------------------------
# D. Real builder output structure verification
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _have_7z(), reason="7z not installed")
class TestRealBuilderOutput:
    """Verify the parser against actual xorriso output — not just synthetic
    fixtures. This catches the class of bug where both parser and fixture
    are wrong in the same way (the SectionEntries offset bug documented in
    the skill). The real ISO's catalog bytes are ground truth.
    """

    @pytest.fixture
    def real_xorriso_iso(self, tmp_path: Path) -> Path:
        """Build a real ISO with xorriso that has both BIOS and EFI boot entries."""
        if not shutil.which("xorriso"):
            pytest.skip("xorriso not installed")
        if not shutil.which("genisoimage"):
            pytest.skip("genisoimage not installed (needed for source ISO)")

        src_dir = _make_source_fixture(tmp_path / "src")
        src_iso = _make_source_iso(src_dir, tmp_path / "src.iso")

        fake_wim = tmp_path / "install.wim"
        fake_wim.write_bytes(b"\x00" * 1024)
        autou = tmp_path / "autounattend.xml"
        autou.write_text('<?xml version="1.0"?><unattend/>')

        out_iso = tmp_path / "real.iso"
        result = subprocess.run(
            ["bash", str(REPACK_SH), str(src_iso), str(out_iso),
             str(fake_wim), str(autou)],
            capture_output=True, timeout=120,
        )
        assert result.returncode == 0, (
            f"repack.sh failed:\n{result.stderr.decode(errors='replace')}"
        )
        assert b"Building ISO with xorriso" in result.stdout, (
            f"Expected xorriso to be chosen:\n{result.stdout.decode(errors='replace')}"
        )
        return out_iso

    def test_xorriso_catalog_has_efi_section_header(self, real_xorriso_iso: Path):
        """The real xorriso output must have a 0x91 0xEF section header
        at catalog offset 64. Not just "parser says uefi=True" — verify
        the actual bytes.
        """
        dump = debug_dump_catalog(real_xorriso_iso)
        assert dump["section_byte0"] == "0x91", (
            f"Expected 0x91 (final section header) at catalog offset 64, "
            f"got {dump['section_byte0']}. xorriso may have changed its "
            f"catalog format."
        )
        assert dump["section_byte1"] == "0xef", (
            f"Expected 0xEF (EFI platform) at catalog offset 65, "
            f"got {dump['section_byte1']}."
        )

    def test_xorriso_catalog_entry_count_at_least_one(self, real_xorriso_iso: Path):
        """The EFI section must have entry_count >= 1. entry_count=0 is
        the --grub2-boot-info bug shape.
        """
        dump = debug_dump_catalog(real_xorriso_iso)
        assert dump["section_entries_int"] >= 1, (
            f"EFI section entry_count={dump['section_entries_int']}. "
            f"This is the --grub2-boot-info regression — entry_count=0 "
            f"means the EFI section is empty and UEFI machines can't boot."
        )

    def test_xorriso_catalog_bios_entry_is_bootable(self, real_xorriso_iso: Path):
        """The BIOS default entry (bytes 32-63) must have byte 0 = 0x88
        (bootable). If it's 0x00, BIOS won't boot.
        """
        with real_xorriso_iso.open("rb") as f:
            f.read(18 * SECTOR)  # seek past headers
            # Read catalog via the parser's helper
            from scripts.build.verify_iso_bootable import _read_sector
            br_sector = _read_sector(real_xorriso_iso, 17)
            catalog_lba = int.from_bytes(br_sector[0x47:0x4B], "little")
            catalog = _read_sector(real_xorriso_iso, catalog_lba)

        default_entry = catalog[32:64]
        assert default_entry[0] == 0x88, (
            f"BIOS default entry byte 0 = 0x{default_entry[0]:02x}, "
            f"expected 0x88 (bootable). BIOS won't boot this ISO."
        )

    def test_parser_agrees_with_real_catalog_bytes(self, real_xorriso_iso: Path):
        """The parser's verdict (uefi=True, bios=True) must agree with
        the actual catalog bytes when inspected manually. This is the
        meta-test against the SectionEntries offset bug class: if the
        parser reads the wrong offset, it might say uefi=True while the
        actual entry_count at the spec-correct offset is 0.
        """
        check = verify_iso_bootable(real_xorriso_iso)
        dump = debug_dump_catalog(real_xorriso_iso)

        # Parser says UEFI bootable
        assert check.uefi is True

        # Manual inspection of the actual bytes agrees
        assert dump["section_byte0"] == "0x91"
        assert dump["section_byte1"] == "0xef"
        assert dump["section_entries_int"] >= 1

        # If the parser were reading the wrong offset, these two would
        # disagree: parser says True but entry_count at the spec offset
        # is 0. This test catches that exact class of bug.
        assert check.uefi == (dump["section_entries_int"] >= 1), (
            "Parser verdict doesn't match manual catalog inspection. "
            "This is the SectionEntries offset bug class — the parser "
            "may be reading the wrong byte offset for entry_count."
        )

    def test_xorriso_output_passes_strict_mode(self, real_xorriso_iso: Path):
        """In strict mode, warnings also cause failure. A clean xorriso
        build should have no warnings at all.
        """
        check = verify_iso_bootable(real_xorriso_iso)
        assert not check.warnings, (
            f"xorriso output has warnings: {check.warnings}. "
            f"A clean build should have none."
        )
        assert check.ok is True
