#!/usr/bin/env bash
# Upload a single ISO to a named rclone remote. Usage:
#   upload.sh <local-iso> <remote-name> <remote-path> <rclone-conf>
set -euo pipefail

LOCAL="$1"
REMOTE_NAME="$2"
REMOTE_PATH="$3"
RCLONE_CONF="$4"

rclone --config="$RCLONE_CONF" copyto \
  "$LOCAL" \
  "${REMOTE_NAME}:${REMOTE_PATH}" \
  --progress \
  --drive-chunk-size=64M \
  --transfers=4 \
  --checkers=8 \
  --log-level=INFO \
  --log-file=/tmp/rclone-upload.log
