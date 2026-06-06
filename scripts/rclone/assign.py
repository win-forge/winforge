"""Assign an ISO to a Google Drive account from the pool.

Strategy: round-robin among accounts that (a) list the product under
handles_products and (b) have enough free quota (used_gb + iso_gb <= quota_gb).
The caller persists used_gb back to the pool manifest after a successful upload.
"""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import yaml

from scripts.lib.log import info, error


@dataclass
class Account:
    name: str
    handles_products: list[str]
    quota_gb: float
    used_gb: float = 0.0


def load_accounts(path: Path) -> list[Account]:
    with path.open() as f:
        cfg = yaml.safe_load(f) or {}
    out: list[Account] = []
    manifest_path = path.with_suffix(".used.yaml")
    used = {}
    if manifest_path.exists():
        used = (yaml.safe_load(manifest_path.read_text()) or {})
    for a in cfg.get("accounts", []):
        out.append(Account(
            name=a["name"],
            handles_products=a.get("handles_products", []),
            quota_gb=float(a.get("quota_gb", 15)),
            used_gb=float(used.get(a["name"], 0.0)),
        ))
    return out


def save_used(path: Path, accounts: list[Account]) -> None:
    used = {a.name: a.used_gb for a in accounts}
    path.write_text(yaml.safe_dump(used))


def assign(product: str, iso_gb: float, accounts: list[Account], cursor: int = 0) -> str:
    """Return the account name to use. Raises RuntimeError if no account can host it."""
    candidates = [
        a for a in accounts
        if product in a.handles_products and (a.used_gb + iso_gb) <= a.quota_gb
    ]
    if not candidates:
        error("rclone.no_candidate", product=product, iso_gb=iso_gb,
              pool=[(a.name, a.used_gb, a.quota_gb) for a in accounts])
        raise RuntimeError(f"No account can host {product} ({iso_gb} GB)")
    pick = candidates[cursor % len(candidates)]
    info("rclone.assigned", product=product, account=pick.name, used_gb=pick.used_gb, iso_gb=iso_gb)
    return pick.name
