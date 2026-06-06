"""Track which UUP-dump builds we've already seen, emit only new ones."""
from __future__ import annotations
import json
from pathlib import Path


def load_known(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return json.loads(path.read_text())


def save_known(path: Path, builds: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(builds, indent=2, sort_keys=True))


def diff_new(state_path: Path, scraped: list[dict]) -> list[dict]:
    known = {b["uuid"] for b in load_known(state_path)}
    return [b for b in scraped if b.get("uuid") not in known]
