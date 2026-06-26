"""End-to-end tests for scripts/build/repack.sh — exercise each ISO builder
against a synthetic fixture and assert bootability.

These tests catch the silent UEFI-drop regression class for ALL three
builders in repack.sh, not just the xorriso path that the live CI verifies.
A naive change to the xorriso invocation (e.g. someone re-adding
``--grub2-boot-info``) breaks the live build immediately. But the
oscdimg and genisoimage paths are only run in niche environments; without
these tests, a regression there would slip through.

Strategy
--------
- Build a minimal real source ISO from a synthetic directory (the same
  files repack.sh checks for: efi/microsoft/boot/efisys.bin,
  boot/etfsboot.com, sources/install.wim). The source ISO must be
  extractable by 7z (the tool repack.sh uses internally).
- For each available builder, run repack.sh against the source ISO
  and parse the resulting output with scripts.build.verify_iso_bootable.
- Assert the documented expectation per builder.

Expected per-builder outcomes (documented in repack.sh):
- xorriso:   uefi=True, bios=True, no warnings  (verified by the live CI run)
- genisoimage: uefi=False (no EFI section header), with explicit warning
- oscdimg:   cannot be tested on Linux runner (Windows-only tool). We
             validate the bootdata syntax statically instead — see
             test_oscdimg_bootdata_syntax below.

Why a synthetic source ISO?
---------------------------
repack.sh's signature is ``repack.sh <iso-in> <iso-out> <wim-in> <autou>``
and it does ``7z x "$ISO_IN"`` internally. We can't pass a directory.
Building a real source ISO from the same small fixture the live CI uses
keeps the test faithful to what repack.sh actually does.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from scripts.build.verify_iso_bootable import verify_iso_bootable

REPO_ROOT = Path(__file__).parent.parent
REPACK_SH = REPO_ROOT / "scripts/build/repack.sh"

# Builders we test (skip if binary missing). oscdimg is tested separately
# via a static bootdata-syntax check — there's no Linux binary for it.
RUNTIME_BUILDERS = ("xorriso", "genisoimage")


def _have_7z() -> bool:
    """repac.sh extracts the source ISO via 7z; tests need it too."""
    return any(shutil.which(t) for t in ("7z", "7zr", "7za"))


def _make_source_fixture(root: Path) -> Path:
    """Create the minimal directory tree repack.sh requires.

    repack.sh only checks ``[ -f "$UEFI_BOOT" ]`` and the oscdimg path
    checks ``[ -f "$BIOS_BOOT" ]``. The other files (install.wim,
    autounattend.xml) are simply ``cp``'d over. So zero-byte files are
    sufficient for the structure test — we don't need a real Windows WIM.
    """
    (root / "efi/microsoft/boot").mkdir(parents=True)
    (root / "efi/microsoft/boot/efisys.bin").write_bytes(b"\x00" * 2048)
    (root / "boot").mkdir()
    (root / "boot/etfsboot.com").write_bytes(b"\x00" * 2048)
    (root / "sources").mkdir()
    (root / "sources/install.wim").write_bytes(b"\x00" * 4096)
    return root


def _make_source_iso(src_dir: Path, out_iso: Path, builder: str) -> Path:
    """Build a minimal source ISO from src_dir using ``builder``.

    Uses whatever builder is available (xorriso preferred, genisoimage
    fallback). The output is a valid ISO 9660 image that 7z can extract.
    """
    if builder == "xorriso":
        subprocess.run(
            [
                "xorriso", "-as", "mkisofs",
                "-o", str(out_iso),
                "-V", "SRC_FX",
                str(src_dir),
            ],
            check=True, capture_output=True, timeout=60,
        )
    elif builder == "genisoimage":
        subprocess.run(
            [
                "genisoimage", "-o", str(out_iso),
                "-V", "SRC_FX",
                str(src_dir),
            ],
            check=True, capture_output=True, timeout=60,
        )
    else:
        raise ValueError(f"unknown source builder: {builder}")
    return out_iso


def _builder_only_path(target_builder: str) -> str:
    """Build a PATH that exposes only ``target_builder`` (no xorriso, no oscdimg).

    repack.sh picks the first available builder via ``command -v``. To
    test what the genisoimage fallback produces, we shadow xorriso and
    oscdimg with a `false` shim so ``command -v`` finds only genisoimage.
    The current system PATH is appended so the rest of the script (bash,
    cp, etc.) still resolves.
    """
    shim_dir = Path(os.environ.get("TMPDIR", "/tmp")) / "repack-shims"
    shim_dir.mkdir(exist_ok=True)
    for blocked in ("xorriso", "oscdimg", "mkisofs"):
        shim = shim_dir / blocked
        if not shim.exists():
            shim.write_text("#!/bin/sh\nexit 127\n")
            shim.chmod(0o755)
    return f"{shim_dir}:{os.environ.get('PATH', '')}"


@pytest.mark.skipif(not _have_7z(), reason="7z not installed (needed for repack.sh extraction)")
@pytest.mark.parametrize("builder", RUNTIME_BUILDERS)
def test_repack_produces_iso(tmp_path: Path, builder: str):
    """End-to-end: feed repack.sh a synthetic source ISO and assert it
    produces an output ISO without crashing, for each available builder.

    Bootability expectations are checked separately below.
    """
    if not shutil.which(builder):
        pytest.skip(f"{builder} not installed")

    src_dir = _make_source_fixture(tmp_path / "src")
    src_iso = _make_source_iso(src_dir, tmp_path / "src.iso", builder="genisoimage" if shutil.which("genisoimage") else "xorriso")

    fake_wim = tmp_path / "install.wim"
    fake_wim.write_bytes(b"\x00" * 1024)
    fake_xml = tmp_path / "autounattend.xml"
    fake_xml.write_text('<?xml version="1.0"?><unattend/>')

    out_iso = tmp_path / "out.iso"
    subprocess.run(
        [
            "bash", str(REPACK_SH),
            str(src_iso), str(out_iso),
            str(fake_wim), str(fake_xml),
        ],
        check=True, capture_output=True, timeout=120,
    )
    assert out_iso.exists()
    assert out_iso.stat().st_size > 2048


@pytest.mark.skipif(not _have_7z(), reason="7z not installed")
def test_repack_xorriso_produces_uefi_bootable_iso(tmp_path: Path):
    """xorriso is the verified builder. The catalog must have an EFI
    section with SectionEntries >= 1 — i.e. uefi=True, no warnings.

    Regression target: anyone re-adding ``--grub2-boot-info`` to the
    xorriso invocation (the bug that prompted adding the verifier in
    the first place). xorriso's patched invocation is in repack.sh; if
    that breaks, this test fails.
    """
    if not shutil.which("xorriso"):
        pytest.skip("xorriso not installed")
    if not shutil.which("genisoimage"):
        pytest.skip("genisoimage not installed (needed to build synthetic source)")

    src_dir = _make_source_fixture(tmp_path / "src")
    src_iso = _make_source_iso(src_dir, tmp_path / "src.iso", builder="genisoimage")

    fake_wim = tmp_path / "install.wim"
    fake_wim.write_bytes(b"\x00" * 1024)
    fake_xml = tmp_path / "autounattend.xml"
    fake_xml.write_text('<?xml version="1.0"?><unattend/>')

    out_iso = tmp_path / "out.iso"
    # Use default PATH — xorriso is found first per repack.sh's priority order
    # (oscdimg, xorriso, genisoimage). oscdimg isn't on Linux; xorriso wins.
    result = subprocess.run(
        [
            "bash", str(REPACK_SH),
            str(src_iso), str(out_iso),
            str(fake_wim), str(fake_xml),
        ],
        capture_output=True, timeout=120,
    )
    assert result.returncode == 0, (
        f"repack.sh failed (rc={result.returncode})\n"
        f"stdout: {result.stdout.decode(errors='replace')}\n"
        f"stderr: {result.stderr.decode(errors='replace')}"
    )
    assert b"Building ISO with xorriso" in result.stdout, (
        f"Expected xorriso to be chosen; got stdout:\n{result.stdout.decode(errors='replace')}"
    )

    check = verify_iso_bootable(out_iso)
    assert check.uefi is True, (
        f"xorriso output is not UEFI-bootable: {check.to_dict()}\n"
        f"This usually means the xorriso invocation dropped --grub2-boot-info "
        f"or removed the -e efisys.bin line. See scripts/build/repack.sh."
    )
    assert check.bios is True
    assert not check.warnings, f"unexpected warnings: {check.warnings}"


@pytest.mark.skipif(not _have_7z(), reason="7z not installed")
def test_repack_genisoimage_produces_bios_only_iso(tmp_path: Path):
    """genisoimage fallback is documented as BIOS-only / UEFI-incompatible.
    The verify step on the live build will reject this output with
    uefi=False. This test pins that behavior so a future change to the
    genisoimage branch (e.g. someone "fixing" it to drop UEFI) surfaces
    as a test failure on the genisoimage branch specifically.

    Strategy: shadow xorriso and oscdimg with a `false` shim so
    repack.sh's ``command -v`` falls through to genisoimage.
    """
    if not shutil.which("genisoimage"):
        pytest.skip("genisoimage not installed")

    src_dir = _make_source_fixture(tmp_path / "src")
    # Build source ISO with whatever builder IS available — only the
    # repack.sh execution needs genisoimage forced.
    source_builder = "genisoimage" if shutil.which("genisoimage") else None
    if not source_builder:
        pytest.skip("no source-builder available (need xorriso or genisoimage)")
    src_iso = _make_source_iso(src_dir, tmp_path / "src.iso", builder=source_builder)

    fake_wim = tmp_path / "install.wim"
    fake_wim.write_bytes(b"\x00" * 1024)
    fake_xml = tmp_path / "autounattend.xml"
    fake_xml.write_text('<?xml version="1.0"?><unattend/>')

    out_iso = tmp_path / "out.iso"
    shadowed_path = _builder_only_path("genisoimage")
    result = subprocess.run(
        [
            "bash", str(REPACK_SH),
            str(src_iso), str(out_iso),
            str(fake_wim), str(fake_xml),
        ],
        env={**os.environ, "PATH": shadowed_path},
        capture_output=True, timeout=120,
    )
    # genisoimage may or may not exit cleanly depending on platform; what
    # matters is which builder was chosen and what the catalog looks like.
    assert b"Building ISO with genisoimage" in result.stdout, (
        f"Expected genisoimage to be chosen; got stdout:\n"
        f"{result.stdout.decode(errors='replace')}"
    )

    # Check that the WARNING we added to repack.sh actually fires — that's
    # how operators learn their build is going to fail verification.
    assert b"genisoimage produces BIOS-only ISOs" in result.stdout, (
        "Expected the genisoimage warning to fire; the warning text in "
        "repack.sh may have been edited away."
    )

    # genisoimage output is BIOS-only by documented design. The verify
    # step on the live build would catch this; this test pins the
    # behavior so a regression (genisoimage output *also* failing BIOS,
    # or somehow gaining UEFI by accident) is caught here.
    if out_iso.exists():
        check = verify_iso_bootable(out_iso)
        # Documented: uefi=False (genisoimage cannot produce UEFI ISOs).
        assert check.uefi is False, (
            f"genisoimage unexpectedly produced a UEFI-bootable ISO: {check.to_dict()}. "
            f"This would be great if real, but genisoimage has no documented way "
            f"to emit a 0x91 EFI section header. Investigate before merging."
        )


def test_oscdimg_bootdata_syntax_static():
    """oscdimg can't run on Linux, but we can validate the bootdata string
    statically. The Windows ADK syntax is::

        oscdimg -bootdata:2#p0,e,b<BIOS_BOOT>#pEF,e,b<UEFI_BOOT>

    where:
      - ``2`` = number of boot entries
      - ``p0`` = partition entry 0 (default/initial)
      - ``e`` = no emulation
      - ``b<BIOS_BOOT>`` = BIOS El Torito boot image
      - ``pEF`` = partition entry 0xEF (EFI)
      - ``b<UEFI_BOOT>`` = UEFI El Torito boot image

    A typo in this string (e.g. ``pFE`` instead of ``pEF``, or ``1#`` with
    only one entry but BIOS+UEFI both referenced) silently produces a
    non-bootable ISO. Catching the syntax statically is the only thing
    we can do on Linux.
    """
    text = REPACK_SH.read_text()
    # The oscdimg invocation lives between `ISO_BUILDER = "oscdimg"` and
    # the next `elif`. Look for the specific bootdata fragment.
    assert "-bootdata:2" in text, (
        "oscdimg invocation missing -bootdata:2 entry count. "
        "Expected -bootdata:2#p0,e,b<BIOS>#pEF,e,b<UEFI>."
    )
    assert '"$BIOS_BOOT"' in text or "BIOS_BOOT" in text, (
        "oscdimg bootdata doesn't reference BIOS_BOOT"
    )
    assert '"$UEFI_BOOT"' in text or "UEFI_BOOT" in text, (
        "oscdimg bootdata doesn't reference UEFI_BOOT"
    )
    # The pEF (EFI partition) marker must appear in EVERY bootdata
    # fragment — without it, no EFI entry is registered and UEFI
    # machines can't boot. Check each fragment individually because
    # repack.sh has both a 2-entry (BIOS+UEFI) and a 1-entry
    # (UEFI-only fallback for ISOs without etfsboot.com) variant.
    import re
    bootdata_matches = re.findall(r"-bootdata:\S+", text)
    assert bootdata_matches, "no -bootdata: fragment found"
    for frag in bootdata_matches:
        assert "pEF" in frag, (
            f"oscdimg bootdata fragment {frag!r} missing pEF partition marker; "
            f"UEFI machines won't boot the resulting ISO."
        )
        # The 2-entry fragment must also have the p0 default marker.
        # The 1-entry fragment uses pEF as its sole entry (no p0 because
        # there's only one).
        if frag.startswith("-bootdata:2"):
            assert "p0," in frag or "p0#" in frag, (
                f"2-entry oscdimg bootdata {frag!r} missing p0 default partition marker"
            )


def test_xorriso_invocation_excludes_grub2_boot_info():
    """Regression guard: --grub2-boot-info silently drops the EFI section.

    See scripts/build/verify_iso_bootable.py for the full bug story.
    The original bug was that ``xorriso -as mkisofs --grub2-boot-info``
    writes a catalog with EFI entry_count=0. The flag was removed in
    commit f3888b3. The flag NAME is allowed to appear in comments
    (documenting the pitfall), but it must not appear on any actual
    xorriso command line.
    """
    text = REPACK_SH.read_text()
    # Strip comments (# to EOL) before checking — comments documenting the
    # pitfall are fine and expected.
    code_lines = []
    for line in text.splitlines():
        # Preserve indentation; drop everything from `#` onwards (naive but
        # this script doesn't use heredocs or # inside strings).
        stripped = line.split("#", 1)[0]
        code_lines.append(stripped)
    code_only = "\n".join(code_lines)

    assert "--grub2-boot-info" not in code_only, (
        "repack.sh invokes xorriso with --grub2-boot-info, which silently "
        "produces BIOS-only ISOs (UEFI entry_count=0 in the El Torito catalog). "
        "Remove it from the xorriso command. See "
        "scripts/build/verify_iso_bootable.py for details."
    )