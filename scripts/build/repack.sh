#!/usr/bin/env bash
set -euo pipefail

WORK=$(mktemp -d)
trap "rm -rf $WORK" EXIT

7z x "$ISO_IN" -o"$WORK" -bd -y >/dev/null
cp "$WIM_IN"   "$WORK/sources/install.wim"
cp "$AUTOU"    "$WORK/autounattend.xml"

cp "$WORK/boot/etfsboot.com" "$WORK/etfsboot.com" 2>/dev/null || true

oscdimg -m -o -u2 -udfver102 \
  -bootdata:2#p0,e,b"$WORK/boot/etfsboot.com"#pEF,e,b"$WORK/efi/microsoft/boot/efisys.bin" \
  "$WORK" "$ISO_OUT"

echo "[repack] wrote $ISO_OUT"
