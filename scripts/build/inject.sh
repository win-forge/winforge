#!/usr/bin/env bash
set -euo pipefail

ISO_IN="$1"
WORK="$2"
DRIVER_ROOT="$3"
CAP_SRC="$4"

mkdir -p "$WORK/mount"
mkdir -p "$WORK/drivers/intel-rst"
mkdir -p "$WORK/caps"

cp -r "$DRIVER_ROOT/." "$WORK/drivers/intel-rst/"
cp "$CAP_SRC"/* "$WORK/caps/" 2>/dev/null || true

echo "[stage] ready: $WORK"
