"""Thin wrapper over PyYAML with schema-error context."""
from pathlib import Path
import yaml


def load(path: Path) -> dict:
    try:
        with path.open() as f:
            return yaml.safe_load(f) or {}
    except yaml.YAMLError as e:
        raise SystemExit(f"yaml error in {path}: {e}")
