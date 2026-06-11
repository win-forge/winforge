#!/usr/bin/env bash
# Download UUP files + run UUP-dump converter to produce ISO.
# Usage: convert.sh <uuid> <edition> <output-dir>
# Runs on Windows (Git Bash) — Windows runners have DISM, oscdimg, wimlib.
set -euo pipefail

UUID="$1"
EDITION="$2"
OUTDIR="$3"

mkdir -p "$OUTDIR"
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

echo "[convert] Fetching UUP files for $UUID / $EDITION..."
python -m scripts.uupd.download "$UUID" "$EDITION" --output-dir "$WORK/uup"

# Validate downloads: 0-byte files indicate WAF/expired-URL issues
ZERO_BYTE=$(find "$WORK/uup" -name "*-*-*-*-*-*" -size 0 2>/dev/null | wc -l)
if [ "$ZERO_BYTE" -gt 0 ]; then
    echo "[convert] WARNING: $ZERO_BYTE files downloaded as 0 bytes (likely expired URLs). Re-running with fresh URL params may help."
fi

echo "[convert] Applying rename script (GUID -> friendly names)..."
cd "$WORK/uup"
if [ -f "uup_rename_windows.cmd" ]; then
    if command -v cygpath >/dev/null 2>&1 && command -v cmd.exe >/dev/null 2>&1; then
        # Windows runner: invoke via cmd.exe
        cmd.exe //c "$(cygpath -w "$WORK/uup/uup_rename_windows.cmd")" 2>&1 | tail -5
    else
        # Linux: do the rename directly from the .cmd script's rename lines
        echo "  (no Windows tools available; emulating rename from .cmd)"
        grep -E '^rename ' uup_rename_windows.cmd | \
            sed -E 's/rename "([^"]+)" "([^"]+)"/mv -v "\1" "\2"/' | \
            bash 2>&1 | tail -10
    fi
elif [ -f "uup_rename_linux.sh" ]; then
    bash "$WORK/uup/uup_rename_linux.sh" 2>&1 | tail -5
else
    echo "[convert] ERROR: No rename script found"
    ls -la "$WORK/uup/" | head -20
    exit 1
fi

# Check we have the expected UUP set
EXP_CAB=$(find . -name "*.cab" | wc -l)
EXP_ESD=$(find . -name "*.esd" | wc -l)
EXP_PSF=$(find . -name "*.psf" 2>/dev/null | wc -l)
echo "[convert] Found $EXP_CAB .cab, $EXP_ESD .esd, $EXP_PSF .psf files"

if [ "$EXP_CAB" -lt 10 ]; then
    echo "[convert] ERROR: Too few CAB files; rename may have failed"
    ls -la | head -20
    exit 1
fi

cd - >/dev/null

# Now we need to actually build install.wim from the UUP files.
# Use the UUP-dump converter (Windows version) which wraps DISM.
echo "[convert] Cloning UUP-dump converter..."
if [ ! -d "$WORK/converter" ]; then
    # UUP-dump hosts their converter on their own Gitea, not GitHub
    GIT_TERMINAL_PROMPT=0 git clone --depth=1 https://git.uupdump.net/uup-dump/converter.git "$WORK/converter" 2>&1 | tail -3
fi

echo "[convert] Running UUP-dump converter (this takes 10-30 minutes)..."
# The Gitea repo only has convert.sh (Linux/macOS); it uses wimlib, cabextract, chntpw, genisoimage
if [ -f "$WORK/converter/convert.sh" ]; then
    # Copy UUP files into the converter's working dir (it expects a UUPs/ subdir)
    mkdir -p "$WORK/converter/UUPs"
    cp -r "$WORK/uup/"* "$WORK/converter/UUPs/" 2>/dev/null || true
    cd "$WORK/converter"
    # Default: wim compression, UUPs/ dir, no virtual editions
    bash convert.sh wim UUPs 0 2>&1 | tee "$OUTDIR/convert.log" | tail -30
    cd - >/dev/null
else
    echo "[convert] ERROR: UUP-dump converter missing convert.sh"
    ls "$WORK/converter/"
    exit 1
fi

# The converter produces the ISO in its working dir
ISO=$(find "$WORK/converter" -maxdepth 2 -name "*.iso" -type f | head -1)
if [ -n "$ISO" ]; then
    cp "$ISO" "$OUTDIR/iso-in.iso"
    echo "[convert] ISO created: $OUTDIR/iso-in.iso ($(du -h "$OUTDIR/iso-in.iso" | cut -f1))"
else
    echo "[convert] ERROR: No ISO produced. Check $OUTDIR/convert.log"
    find "$WORK/converter" -maxdepth 2 -name "*.iso" -o -name "*.wim" 2>/dev/null | head -5
    exit 1
fi
