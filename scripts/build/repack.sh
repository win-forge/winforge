#!/usr/bin/env bash
# Repack an ISO with a patched install.wim and autounattend.xml.
# Usage: repack.sh <iso-in> <iso-out> <wim-in> <autounattend-xml>
# Runs on Linux (uses xorriso/genisoimage) or Windows (oscdimg).
set -euo pipefail

ISO_IN="$1"
ISO_OUT="$2"
WIM_IN="$3"
AUTOU="$4"

# Put temp work in $WORKDIR (defaults to $GITHUB_WORKSPACE if set, else /tmp).
# $GITHUB_WORKSPACE is the LVM-mounted volume from maximize-build-space
# (~100GB usable). /tmp lives on /dev/root which is cramped after the LVM
# image is allocated. Extracting a 4.5GB ISO and rebuilding a 4.5GB ISO
# needs ~10GB of temp space.
WORKDIR="${WORKDIR:-${GITHUB_WORKSPACE:-/tmp}}"
WORK=$(mktemp -d -p "$WORKDIR")
trap 'rm -rf "$WORK"' EXIT

echo "[repack] Extracting ISO: $ISO_IN"
7z x "$ISO_IN" -o"$WORK" -bd -y >/dev/null

echo "[repack] Replacing install.wim"
cp "$WIM_IN" "$WORK/sources/install.wim"

echo "[repack] Injecting autounattend.xml"
cp "$AUTOU" "$WORK/autounattend.xml"

# Find boot files — handle both UEFI and BIOS
BIOS_BOOT="$WORK/boot/etfsboot.com"
UEFI_BOOT="$WORK/efi/microsoft/boot/efisys.bin"

if [ ! -f "$UEFI_BOOT" ]; then
    echo "[repack] ERROR: efisys.bin not found in ISO"
    exit 1
fi

# Pick the ISO builder. Priority order matches the Windows ADK preference
# (oscdimg), then Linux's preferred xorriso. genisoimage is kept as a last
# resort for environments where neither is available — but it produces
# BIOS-only ISOs that fail the UEFI verify step, so the resulting build
# will be flagged (and an actual install would fail to boot on modern UEFI
# hardware). The right answer in a genisoimage-only environment is to
# install xorriso (`apt install xorriso`), not to rely on this fallback.
ISO_BUILDER=""
if command -v oscdimg >/dev/null 2>&1; then
    ISO_BUILDER="oscdimg"
elif command -v xorriso >/dev/null 2>&1; then
    ISO_BUILDER="xorriso"
elif command -v genisoimage >/dev/null 2>&1; then
    ISO_BUILDER="genisoimage"
fi

if [ -z "$ISO_BUILDER" ]; then
    echo "[repack] ERROR: no ISO builder found (need oscdimg, xorriso, or genisoimage)"
    exit 1
fi

echo "[repack] Building ISO with $ISO_BUILDER: $ISO_OUT"

if [ "$ISO_BUILDER" = "oscdimg" ]; then
    if [ -f "$BIOS_BOOT" ]; then
        oscdimg -m -o -u2 -udfver102 \
            -bootdata:2#p0,e,b"$BIOS_BOOT"#pEF,e,b"$UEFI_BOOT" \
            "$WORK" "$ISO_OUT"
    else
        oscdimg -m -o -u2 -udfver102 \
            -bootdata:1#pEF,e,b"$UEFI_BOOT" \
            "$WORK" "$ISO_OUT"
    fi
elif [ "$ISO_BUILDER" = "xorriso" ]; then
    # xorriso: UEFI via -e (EFI system partition image), BIOS via -b.
    # -isohybrid-mbr gives a hybrid MBR for BIOS+UEFI on USB sticks;
    # -isohybrid-gpt-basdat adds a protective GPT for UEFI systems.
    # Note: --grub2-boot-info was removed — it's for GRUB-based images and
    # was producing an El Torito catalog with EFI entry_count=0 (the EFI
    # entry was silently dropped). Verified bootable via catalog parse.
    xorriso -as mkisofs \
        -o "$ISO_OUT" \
        -isohybrid-mbr /usr/lib/ISOLINUX/isohdpfx.bin \
        -b boot/etfsboot.com \
        -no-emul-boot \
        -boot-load-size 8 \
        -boot-info-table \
        -eltorito-alt-boot \
        -e efi/microsoft/boot/efisys.bin \
        -no-emul-boot \
        -isohybrid-gpt-basdat \
        -V "CCCOMA_X64FRE_EN-US_DV9" \
        "$WORK" 2>&1 | tail -3 || {
        # Fallback: simpler invocation if isohdpfx.bin isn't available
        xorriso -as mkisofs \
            -o "$ISO_OUT" \
            -e efi/microsoft/boot/efisys.bin \
            -no-emul-boot \
            -isohybrid-gpt-basdat \
            "$WORK" 2>&1 | tail -3
    }
elif [ "$ISO_BUILDER" = "genisoimage" ]; then
    # genisoimage fallback. DOES NOT produce UEFI-bootable ISOs — it has
    # no equivalent of xorriso's -eltorito-alt-boot / -e for an EFI
    # system partition image, and no documented way to emit a 0x91
    # section header with platform=0xEF. The ISO produced here boots
    # on neither legacy BIOS (efisys.bin isn't a BIOS bootloader) nor
    # UEFI (no EFI section in the catalog). The build's verify step
    # will fail it. We emit a clear warning so the operator sees why.
    echo "[repack] WARNING: genisoimage produces BIOS-only ISOs that won't boot on UEFI."
    echo "[repack] WARNING: Install xorriso for UEFI support: apt install xorriso"
    genisoimage -o "$ISO_OUT" \
        -V "CCCOMA_X64FRE_EN-US_DV9" \
        -b efi/microsoft/boot/efisys.bin \
        -no-emul-boot \
        "$WORK" 2>&1 | tail -3
fi

echo "[repack] ISO created: $ISO_OUT ($(du -h "$ISO_OUT" | cut -f1))"
