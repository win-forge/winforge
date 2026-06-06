"""Expand editions.yaml + a product name into the full build matrix rows."""
from __future__ import annotations
from pathlib import Path
from scripts.lib.yaml import load


def expand(config_path: Path, product: str | None = None) -> list[dict]:
    cfg = load(config_path)
    out: list[dict] = []
    for prod, editions in cfg.get("editions", {}).items():
        if product and prod != product:
            continue
        for ed in editions:
            out.append({
                "product": prod,
                "edition": ed["id"],
                "label": ed["label"],
            })
    return out
