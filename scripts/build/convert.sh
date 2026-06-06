#!/usr/bin/env bash
# Fetch UUP files + convert to ISO using UUP-dump conversion script.
# Usage: convert.sh <uuid> <edition> <output-dir>
set -euo pipefail

UUID="$1"
EDITION="$2"
OUTDIR="$3"

mkdir -p "$OUTDIR"
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

echo "[convert] Fetching UUP files for $UUID / $EDITION..."
python -m scripts.uupd.download "$UUID" "$EDITION" --output-dir "$WORK/uup"

echo "[convert] Running UUP download script..."
bash "$WORK/uup/uup_download_linux.sh" 2>&1 | tee "$OUTDIR/convert.log"

# The converter should produce the ISO in the WORK dir
ISO=$(find "$WORK" -name "*.iso" | head -1)
if [ -n "$ISO" ]; then
    cp "$ISO" "$OUTDIR/iso-in.iso"
    echo "[convert] ISO created: $OUTDIR/iso-in.iso ($(du -h "$OUTDIR/iso-in.iso" | cut -f1))"
else
    echo "[convert] ERROR: No ISO produced. Check $OUTDIR/convert.log"
    ls -la "$WORK/"
    exit 1
fi
