"""Structured logging for winforge. Writes to stderr, JSON to /tmp/winforge.log if WINFORGE_LOG_JSON set."""
import json
import os
import sys
import time
from typing import Any


def emit(level: str, event: str, **fields: Any) -> None:
    msg = {"ts": time.time(), "level": level, "event": event, **fields}
    line = json.dumps(msg, separators=(",", ":"))
    print(line, file=sys.stderr)
    if os.environ.get("WINFORGE_LOG_JSON"):
        with open("/tmp/winforge.log", "a") as f:
            f.write(line + "\n")


def info(event: str, **fields: Any) -> None:
    emit("info", event, **fields)


def warn(event: str, **fields: Any) -> None:
    emit("warn", event, **fields)


def error(event: str, **fields: Any) -> None:
    emit("error", event, **fields)
