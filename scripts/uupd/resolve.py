"""Match a UUP-dump Build against our product config; return a normalized record or None."""
from __future__ import annotations
from pathlib import Path
from scripts.lib.yaml import load
from scripts.uupd.scrape import Build

CONFIG_PATH = Path(__file__).parent.parent.parent / "config" / "products.yaml"


def _detect_arch(title: str, build_arch: str) -> str | None:
    if build_arch == "x64":
        return "x64"
    t = title.lower()
    if "amd64" in t and "x64" in t:
        return "x64"
    return None


def resolve(build: Build, config_path: Path = CONFIG_PATH) -> dict | None:
    cfg = load(config_path)
    arch = _detect_arch(build.title, build.arch)
    if arch != "x64":
        return None
    for product in cfg.get("products", []):
        if product["match"] in build.title:
            return {
                "name": product["name"],
                "family": product["family"],
                "track": product["track"],
                "arch": arch,
                "uuid": build.uuid,
                "title": build.title,
                "added_at": build.added_at,
            }
    return None
