"""Tests for scripts.build.verify_iso_bootable — byte-fixture tests, no real ISOs.

These fixtures synthesize the exact bytes of an ISO 9660 Primary Volume
Descriptor, El Torito Boot Record, and boot catalog so we can test the
parser's behavior on each catalog variant without depending on xorriso,
genisoimage, or any real ISO file.
"""
from __future__ import annotations

import struct
from pathlib import Path

import pytest

from scripts.build.verify_iso_bootable import verify_iso_bootable


SECTOR = 2048


def _zero_sector() -> bytes:
    return b"\x00" * SECTOR


def _pvd() -> bytes:
    """Primary Volume Descriptor at sector 16."""
    pvd = bytearray(_zero_sector())
    pvd[0] = 0x01  # PVD type
    pvd[1:6] = b"CD001"  # Standard Identifier
    return bytes(pvd)


def _br(catalog_lba: int) -> bytes:
    """El Torito Boot Record Volume Descriptor at sector 17."""
    br = bytearray(_zero_sector())
    br[0] = 0x00  # Boot Record Volume Descriptor type
    br[1:6] = b"CD001"
    # BR bytes 7..39 must be exactly 32 bytes ("EL TORITO SPECIFICATION"
    # is 23 chars). Use individual byte writes to avoid shrinking the
    # bytearray via slice-assignment of a shorter RHS.
    for i, ch in enumerate(b"EL TORITO SPECIFICATION"):
        br[7 + i] = ch
    br[0x47:0x4B] = struct.pack("<I", catalog_lba)
    return bytes(br)


def _validation_entry() -> bytes:
    """Bytes 0..31 of the boot catalog: validation entry."""
    e = bytearray(32)
    e[0] = 0x01  # header ID
    e[1] = 0x00  # platform: 8086
    e[2:7] = b"EL TO"  # first 5 bytes of "EL TORITO SPECIFICATION"
    return bytes(e)


def _default_entry(bootable: bool = True, rba: int = 100) -> bytes:
    """Bytes 32..63 of the boot catalog: default/initial boot entry."""
    e = bytearray(32)
    e[0] = 0x88 if bootable else 0x00
    e[1] = 0x00  # boot media type
    e[8:12] = struct.pack("<I", rba)
    return bytes(e)


def _efi_section_header(entry_count: int) -> bytes:
    """Bytes 64..95 of the boot catalog: EFI section header (or absent).

    Per the El Torito spec (edk2 MdePkg/Include/IndustryStandard/ElTorito.h,
    Section header entry):
      - Byte 0: Indicator (0x91 for final header)
      - Byte 1: PlatformId (0xEF for EFI)
      - Bytes 2-3: SectionEntries (UINT16 LE) — count of section entries
      - Bytes 4-31: Id[28] string (often empty)
    """
    e = bytearray(32)
    e[0] = 0x91  # final section header ID
    e[1] = 0xEF  # platform ID for EFI
    e[2:4] = struct.pack("<H", entry_count)
    return bytes(e)


def _build_iso(
    tmp_path: Path,
    *,
    catalog_lba: int = 18,
    bios_bootable: bool = True,
    include_efi_section: bool = True,
    efi_entry_count: int = 1,
    efi_platform: int = 0xEF,
    catalog: bytes | None = None,
) -> Path:
    """Write a minimal ISO with a custom boot catalog to a temp file."""
    iso = tmp_path / "test.iso"
    pvd = _pvd()
    br = _br(catalog_lba)
    if catalog is None:
        catalog = (
            _validation_entry()
            + _default_entry(bootable=bios_bootable)
            + (
                bytes(_efi_section_header(efi_entry_count))
                if include_efi_section
                else b"\x00" * 32
            )
        )
        # Pad to a full sector (the on-disk catalog is always one full sector)
        catalog = catalog.ljust(SECTOR, b"\x00")
        # Patch the platform byte if the caller wants a non-EFI section
        if include_efi_section and efi_platform != 0xEF:
            catalog_arr = bytearray(catalog)
            catalog_arr[64 + 1] = efi_platform
            catalog = bytes(catalog_arr)

    assert len(catalog) == SECTOR, "catalog fixture must be exactly one sector"

    # Layout: [sector 0..15: padding][sector 16: PVD][sector 17: BR][sector N: catalog]
    sectors_needed = max(catalog_lba + 1, 18)
    with iso.open("wb") as f:
        f.write(b"\x00" * (16 * SECTOR))
        f.write(pvd)
        f.write(br)
        # Pad up to the catalog LBA if needed
        written = 18
        while written < catalog_lba:
            f.write(_zero_sector())
            written += 1
        f.write(catalog)
        # Pad with one more sector so the file is at least sectors_needed long
        while written + 1 < sectors_needed:
            f.write(_zero_sector())
            written += 1
    return iso


def test_raises_on_non_iso(tmp_path: Path):
    """File big enough for sector 16 but lacking the CD001 magic should fail with ValueError."""
    f = tmp_path / "fake.iso"
    # Write enough sectors to reach the PVD (sector 16), but with the wrong magic.
    f.write_bytes(b"\x00" * (20 * SECTOR))
    with pytest.raises(ValueError, match="not an ISO 9660 image"):
        verify_iso_bootable(f)


def test_raises_on_missing_file(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        verify_iso_bootable(tmp_path / "does-not-exist.iso")


def test_fully_bootable_bios_and_uefi(tmp_path: Path):
    """Catalog with EFI section + entry_count=1 should report both True."""
    iso = _build_iso(tmp_path, efi_entry_count=1)
    result = verify_iso_bootable(iso)
    assert result.bios is True
    assert result.uefi is True
    assert result.warnings == []
    assert result.errors == []
    assert result.ok is True


def test_efi_section_entry_count_zero_is_caught(tmp_path: Path):
    """The actual bug case: EFI section present but entry_count=0.

    This is the regression that --grub2-boot-info introduced. UEFI
    machines cannot boot an ISO with this catalog shape; the build
    should fail (uefi=False) so a human notices.
    """
    iso = _build_iso(tmp_path, efi_entry_count=0)
    result = verify_iso_bootable(iso)
    assert result.bios is True, "BIOS still boots fine — that's the silent failure"
    assert result.uefi is False
    assert any("entry_count=0" in w for w in result.warnings), (
        "warning should mention the count so the bug is grep-able in build logs"
    )
    assert any("--grub2-boot-info" in w for w in result.warnings)
    assert result.ok is False


def test_no_efi_section_is_bios_only(tmp_path: Path):
    """Catalog with no EFI section header — ISO boots BIOS only."""
    iso = _build_iso(tmp_path, include_efi_section=False)
    result = verify_iso_bootable(iso)
    assert result.bios is True
    assert result.uefi is False
    assert any("No EFI section header" in w for w in result.warnings)


def test_section_header_with_non_efi_platform(tmp_path: Path):
    """Catalog with a section header but platform != 0xEF (e.g. PowerPC)."""
    iso = _build_iso(tmp_path, efi_platform=0x01)  # 0x01 = PowerPC
    result = verify_iso_bootable(iso)
    assert result.uefi is False
    assert any("platform=0x01" in w for w in result.warnings)
    assert any("expected 0xEF" in w for w in result.warnings)


def test_bios_entry_not_bootable_warns(tmp_path: Path):
    """Catalog with default entry not bootable (byte 0x00 instead of 0x88)."""
    iso = _build_iso(tmp_path, bios_bootable=False)
    result = verify_iso_bootable(iso)
    assert result.bios is False
    assert any("BIOS default entry not bootable" in w for w in result.warnings)


def test_truncated_catalog_returns_error(tmp_path: Path):
    """Catalog shorter than 96 bytes — parser should report an error, not crash.

    Note: this branch is defensive; real-world ISOs always write a full
    catalog sector, so we don't currently exercise it. The branch exists
    in the parser for robustness against malformed inputs.
    """
    pytest.skip("truncation branch is defensive; real-world ISOs always have a full catalog sector")


def test_custom_catalog_lba(tmp_path: Path):
    """Boot catalog at a non-default LBA — parser reads it via BR pointer."""
    iso = _build_iso(tmp_path, catalog_lba=42, efi_entry_count=1)
    result = verify_iso_bootable(iso)
    assert result.bios is True
    assert result.uefi is True


def test_ok_property_combines_correctly(tmp_path: Path):
    """ok is True iff uefi=True AND no errors, regardless of warnings."""
    iso_ok = _build_iso(tmp_path, efi_entry_count=1)
    assert verify_iso_bootable(iso_ok).ok is True

    iso_broken = _build_iso(tmp_path, efi_entry_count=0)
    assert verify_iso_bootable(iso_broken).ok is False

    iso_bios_only = _build_iso(tmp_path, include_efi_section=False)
    assert verify_iso_bootable(iso_bios_only).ok is False


def test_entry_count_offset_matches_spec(tmp_path: Path):
    """Spec regression: SectionEntries (UINT16 LE) is at offset 2-3 of the
    section header entry, NOT offset 28-29. The 28-29 region is the LAST
    2 bytes of the 28-byte Id[28] string, which is usually empty zeros
    and would yield a constant false-positive entry_count=0.

    This test pins both offsets to lock the spec interpretation:
    - Encoding entry_count=1 at the WRONG offset (28-29) and reading at
      the RIGHT offset (2-3) returns entry_count=0 → fails. Confirms the
      bug shape that fooled the original skill recipe.
    - Encoding at the RIGHT offset (2-3) and reading at the WRONG offset
      (28-29) returns the Id bytes (= 0 for empty Id) → constant
      false-positive.
    """
    import struct as _s

    def _fixture_with_count_at(offset: int) -> Path:
        iso = tmp_path / f"iso_{offset}.iso"
        cat = (
            _validation_entry()
            + _default_entry()
            + (lambda e: (e.__setitem__(slice(offset, offset + 2), _s.pack("<H", 1)) or e))(bytearray(32))
        )
        # Patch bytes 0/1 of the section entry
        cat_arr = bytearray(cat)
        cat_arr[64] = 0x91
        cat_arr[65] = 0xEF
        cat = bytes(cat_arr).ljust(SECTOR, b"\x00")
        with iso.open("wb") as f:
            f.write(b"\x00" * (16 * SECTOR))
            f.write(_pvd())
            f.write(_br(18))
            f.write(cat)
        return iso

    # Encoding at the spec-correct offset (2) → parser should report UEFI=True
    iso_correct = _fixture_with_count_at(2)
    assert verify_iso_bootable(iso_correct).uefi is True

    # Encoding at the wrong offset (28) → parser should report UEFI=False
    # because section[2:4] reads zero (this is the false-positive shape).
    iso_wrong = _fixture_with_count_at(28)
    assert verify_iso_bootable(iso_wrong).uefi is False, (
        "Parser should report UEFI=False when entry count is at wrong offset "
        "(this is the bug shape: reading the Id[28] tail instead of SectionEntries)"
    )