"""Render `{{KEY}}` placeholders in a file/string."""
from __future__ import annotations
import re
from pathlib import Path

_PLACEHOLDER_RE = re.compile(r"\{\{([A-Z0-9_]+)\}\}")


def render(text: str, values: dict[str, str]) -> str:
    return _PLACEHOLDER_RE.sub(lambda m: values.get(m.group(1), m.group(0)), text)


def render_file(src: Path, dst: Path, values: dict[str, str]) -> None:
    dst.write_text(render(src.read_text(), values))
