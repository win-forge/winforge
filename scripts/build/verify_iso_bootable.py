"""Verify a built ISO is bootable on BIOS and UEFI.

Parses the El Torito boot catalog directly from the ISO bytes. This is the
only reliable check that UEFI boot will work — ``file win11.iso`` only
reports the BIOS/MBR boot indicator and silently passes when the EFI
section of the catalog is empty.

Background
----------
xorriso's ``--grub2-boot-info`` flag is for GRUB-based boot images.
With it present, xorriso writes an El Torito catalog whose EFI section
has ``entry_count=0``. The ISO boots on legacy BIOS but not on UEFI
systems, with no visible error. This module exists to catch that
regression on every build, not just when someone remembers to manually
parse the catalog.

Usage
-----
    python -m scripts.build.verify_iso_bootable path/to.iso
    python -m scripts.build.verify_iso_bootable --strict path/to.iso

Exits 0 on success (UEFI bootable), 1 on failure. ``--strict`` also
fails the build on any warning, even if BIOS+UEFI both report OK.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path


SECTOR = 2048


@dataclass
class BootCheck:
    bios: bool
    uefi: bool
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def ok(self) -> bool:
        return self.uefi and not self.errors


def _read_sector(path: Path, lba: int) -> bytes:
    with path.open("rb") as f:
        f.seek(lba * SECTOR)
        data = f.read(SECTOR)
    if len(data) != SECTOR:
        raise ValueError(f"short read at sector {lba}: got {len(data)} bytes")
    return data


def verify_iso_bootable(iso_path: str | Path) -> BootCheck:
    """Return a ``BootCheck`` describing whether the ISO is BIOS+UEFI bootable.

    Raises ``ValueError`` if the file is not a parseable ISO 9660 image.
    """
    path = Path(iso_path)
    if not path.is_file():
        raise FileNotFoundError(path)

    # Volume Recognition Sequence: PVD at sector 16, BR at sector 17.
    pvd = _read_sector(path, 16)
    if pvd[1:6] != b"CD001":
        raise ValueError(f"{path}: not an ISO 9660 image (PVD type byte = {pvd[0]})")

    br = _read_sector(path, 17)
    if br[1:6] != b"CD001":
        return BootCheck(
            bios=False, uefi=False,
            errors=[f"{path}: no El Torito Boot Record Volume Descriptor at sector 17"],
        )
    if not br[7:39].startswith(b"EL TORITO SPECIFICATION"):
        return BootCheck(
            bios=False, uefi=False,
            errors=[f"{path}: sector 17 is CD001 but not an El Torito BR"],
        )

    # Boot Catalog pointer (LBA) at BR offset 0x47..0x4B.
    catalog_lba = int.from_bytes(br[0x47:0x4B], "little")
    catalog = _read_sector(path, catalog_lba)

    # Validation entry: bytes 0..31. Header byte must be 0x01, platform 0x00,
    # ID string "EL TORITO SPECIFICATION".
    if catalog[0] != 0x01:
        return BootCheck(
            bios=False, uefi=False,
            errors=[f"{path}: boot catalog missing validation entry (byte 0 = {catalog[0]:#x})"],
        )

    # Default/initial entry: bytes 32..63.
    default = catalog[32:64]
    bios_ok = default[0] == 0x88  # 0x88 = bootable, 0x00 = not bootable
    bios_rba = int.from_bytes(default[8:12], "little")
    if not bios_ok:
        # No BIOS entry is unusual but not fatal — some images are UEFI-only.
        # Treat as a warning, not an error.
        pass

    # Section header entry: bytes 64..95.
    warnings: list[str] = []
    errors: list[str] = []
    uefi_ok = False

    if len(catalog) < 96:
        errors.append(
            f"{path}: boot catalog truncated at sector {catalog_lba} "
            f"(got {len(catalog)} bytes, need >= 96)"
        )
        return BootCheck(bios=bios_ok, uefi=False, warnings=warnings, errors=errors)

    section = catalog[64:96]
    if section[0] == 0x91:
        # Section header present. Platform ID at byte 65 (section[1]).
        if section[1] == 0xEF:
            entry_count = int.from_bytes(section[28:30], "little")
            uefi_ok = entry_count >= 1
            if not uefi_ok:
                warnings.append(
                    f"EFI section present but entry_count={entry_count} "
                    "(likely caused by --grub2-boot-info or missing efisys.bin)"
                )
        else:
            warnings.append(
                f"Section header present but platform=0x{section[1]:02x} "
                "(expected 0xEF for EFI)"
            )
    else:
        warnings.append(
            f"No EFI section header (byte 0x91) in boot catalog "
            f"(got 0x{section[0]:02x}); ISO is BIOS-only"
        )

    if not bios_ok:
        warnings.append(
            f"BIOS default entry not bootable (byte = 0x{default[0]:02x}, "
            f"RBA = {bios_rba}); ISO may not boot on legacy BIOS either"
        )

    return BootCheck(bios=bios_ok, uefi=uefi_ok, warnings=warnings, errors=errors)


def _format_text(check: BootCheck, iso_path: Path) -> str:
    lines = [
        f"ISO: {iso_path}",
        f"  BIOS bootable: {check.bios}",
        f"  UEFI bootable: {check.uefi}",
    ]
    for w in check.warnings:
        lines.append(f"  warning: {w}")
    for e in check.errors:
        lines.append(f"  error:   {e}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify a built ISO is BIOS+UEFI bootable by parsing the El Torito catalog."
    )
    parser.add_argument("iso", help="path to the ISO file to verify")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="fail on warnings, not just errors",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="emit machine-readable JSON instead of text",
    )
    args = parser.parse_args(argv)

    iso_path = Path(args.iso)
    try:
        check = verify_iso_bootable(iso_path)
    except (FileNotFoundError, ValueError) as e:
        if args.json:
            print(json.dumps({"ok": False, "error": str(e)}))
        else:
            print(f"error: {e}", file=sys.stderr)
        return 2

    if args.json:
        payload = check.to_dict()
        payload["ok"] = check.ok and not (args.strict and check.warnings)
        print(json.dumps(payload, indent=2))
    else:
        print(_format_text(check, iso_path))

    if check.errors:
        return 1
    if not check.uefi:
        return 1
    if args.strict and check.warnings:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())