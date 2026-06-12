"""Profile loader.

A profile is a saved config bundle that tells the build pipeline:
  - which product + edition to build
  - which UUP build (UUID) to fetch
  - which compression format to use
  - which language pack
  - what label to use for the output ISO

Profiles live in config/profiles/<name>.yaml.

Schema:
    product: win11-24h2         # required: must exist in products.yaml
    edition: professional       # required: must exist in editions.yaml for that product
    language: en-us             # optional, default: en-us
    compression: wim            # optional: wim (default) | esd
    label: my-build-2026        # optional: used in ISO filename + artifact name
    uup_uuid: <override>        # optional: pin to a specific UUP-dump build UUID
    uup_title: <override>       # optional: human-readable title for the pinned build
"""
from __future__ import annotations
import os
from dataclasses import dataclass
from pathlib import Path
import argparse
import re
import sys
import yaml

from scripts.lib.log import info, error


def _find_config_root() -> Path:
    """Locate the config root for the active build.

    Resolution order:
    1. $WINFORGE_CONFIG_ROOT env var (caller-supplied). May be:
       - An absolute path to a directory that *contains* config/ (e.g.
         $GITHUB_WORKSPACE). PROFILES_DIR becomes $WINFORGE_CONFIG_ROOT/config/profiles.
       - Or an absolute path to the config/ directory itself. PROFILES_DIR
         becomes $WINFORGE_CONFIG_ROOT/profiles.
       The function detects which case by checking whether the path
       contains a 'config' subdirectory.
    2. ./config (self-build on winforge repo)
    3. ./.winforge/config (caller mode with default checkout layout)

    Returns the path to the config/ directory itself.
    """
    explicit = os.environ.get("WINFORGE_CONFIG_ROOT", "").strip()
    if explicit:
        p = Path(explicit)
        if not p.is_absolute():
            workspace = os.environ.get("GITHUB_WORKSPACE") or os.getcwd()
            p = Path(workspace) / p
        p = p.resolve()
        # If the explicit path is the repo root (contains a config/ subdir),
        # use that. Otherwise assume it points AT the config dir.
        if (p / "config").is_dir():
            return p / "config"
        return p

    cwd = Path(os.getcwd()).resolve()
    for candidate in (cwd / "config", cwd / ".winforge" / "config"):
        if candidate.is_dir():
            return candidate
    # Fallback: behave as if ./config existed (load() will surface a clear error)
    return cwd / "config"


def _products_file(root: Path) -> Path:
    # Caller's config may not have products.yaml — fall back to the winforge
    # vendored one so self-build still validates against a known product list.
    if (root / "products.yaml").is_file():
        return root / "products.yaml"
    if (root.parent / ".winforge" / "config" / "products.yaml").is_file():
        return root.parent / ".winforge" / "config" / "products.yaml"
    # Last-resort: same as root (will error cleanly if missing)
    return root / "products.yaml"


def _editions_file(root: Path) -> Path:
    if (root / "editions.yaml").is_file():
        return root / "editions.yaml"
    if (root.parent / ".winforge" / "config" / "editions.yaml").is_file():
        return root.parent / ".winforge" / "config" / "editions.yaml"
    return root / "editions.yaml"


# Module-level constants. Set at module load via the helpers above.
# Tests that need to change the config root should call reset_config_root() first.
CONFIG_ROOT: Path = _find_config_root()
PROFILES_DIR: Path = CONFIG_ROOT / "profiles"
PRODUCTS_FILE: Path = _products_file(CONFIG_ROOT)
EDITIONS_FILE: Path = _editions_file(CONFIG_ROOT)


def reset_config_root() -> None:
    """Re-resolve CONFIG_ROOT / PROFILES_DIR / etc. from current env + cwd.

    Useful in tests that monkeypatch WINFORGE_CONFIG_ROOT. Call before
    asserting on the module-level constants.
    """
    global CONFIG_ROOT, PROFILES_DIR, PRODUCTS_FILE, EDITIONS_FILE
    CONFIG_ROOT = _find_config_root()
    PROFILES_DIR = CONFIG_ROOT / "profiles"
    PRODUCTS_FILE = _products_file(CONFIG_ROOT)
    EDITIONS_FILE = _editions_file(CONFIG_ROOT)

# Field constraints
_VALID_COMPRESSION = ("wim", "esd")
_PROFILE_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


@dataclass
class Profile:
    name: str
    product: str
    edition: str
    language: str = "en-us"
    compression: str = "wim"
    label: str = ""
    uup_uuid: str = ""
    uup_title: str = ""

    def __post_init__(self) -> None:
        if not self.label:
            self.label = self.name

    def to_dispatch_payload(self) -> dict[str, str]:
        """Shape this profile as GitHub Actions env vars (UPPERCASE keys)."""
        return {
            "PROFILE": self.name,
            "PRODUCT": self.product,
            "EDITION": self.edition,
            "LANGUAGE": self.language,
            "COMPRESSION": self.compression,
            "LABEL": self.label,
            "UUP_UUID": self.uup_uuid,
            "UUP_TITLE": self.uup_title,
        }


class ProfileError(ValueError):
    """Raised when a profile is missing, malformed, or invalid."""


def list_profiles(profiles_dir: Path = PROFILES_DIR) -> list[str]:
    """List all profile names in config/profiles/."""
    if not profiles_dir.exists():
        return []
    out: list[str] = []
    for f in sorted(profiles_dir.glob("*.yaml")):
        n = f.stem
        if _PROFILE_NAME_RE.match(n):
            out.append(n)
    return out


def _load_yaml(path: Path) -> dict:
    with path.open() as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ProfileError(f"{path}: top-level must be a mapping, got {type(data).__name__}")
    return data


def _validate_compression(value: str) -> None:
    if value not in _VALID_COMPRESSION:
        raise ProfileError(
            f"compression must be one of {list(_VALID_COMPRESSION)}, got {value!r}"
        )


def _validate_edition(product: str, edition: str, editions_data: dict) -> None:
    """Verify the (product, edition) pair is in editions.yaml."""
    product_editions = editions_data.get("editions", {}).get(product, [])
    valid_ids = {e["id"] for e in product_editions if "id" in e}
    if edition not in valid_ids:
        raise ProfileError(
            f"edition {edition!r} not in editions.yaml for product {product!r}. "
            f"Valid: {sorted(valid_ids)}"
        )


def _validate_product(product: str, products_data: dict) -> None:
    """Verify the product is in products.yaml."""
    valid_names = {p["name"] for p in products_data.get("products", []) if "name" in p}
    if product not in valid_names:
        raise ProfileError(
            f"product {product!r} not in products.yaml. Valid: {sorted(valid_names)}"
        )


def load(name: str, *, profiles_dir: Path = PROFILES_DIR) -> Profile:
    """Load a profile by name. Raises ProfileError if missing/invalid."""
    if not _PROFILE_NAME_RE.match(name):
        raise ProfileError(
            f"invalid profile name {name!r} (must match {_PROFILE_NAME_RE.pattern})"
        )
    path = profiles_dir / f"{name}.yaml"
    if not path.exists():
        raise ProfileError(
            f"profile {name!r} not found at {path}. "
            f"Available: {list_profiles(profiles_dir)}"
        )
    data = _load_yaml(path)
    return _from_dict(name, data)


def _from_dict(name: str, data: dict) -> Profile:
    """Build a Profile from a dict (with validation)."""
    required = ("product", "edition")
    missing = [f for f in required if f not in data]
    if missing:
        raise ProfileError(f"profile {name!r}: missing required field(s): {missing}")

    # Load the reference data for validation
    products_data = _load_yaml(PRODUCTS_FILE) if PRODUCTS_FILE.exists() else {"products": []}
    editions_data = _load_yaml(EDITIONS_FILE) if EDITIONS_FILE.exists() else {"editions": {}}

    product = str(data["product"])
    edition = str(data["edition"])
    _validate_product(product, products_data)
    _validate_edition(product, edition, editions_data)

    compression = str(data.get("compression", "wim"))
    _validate_compression(compression)

    # uup_uuid: pin from profile or inherit from products.yaml
    uup_uuid = str(data.get("uup_uuid", ""))
    uup_title = str(data.get("uup_title", ""))
    if not uup_uuid:
        for p in products_data.get("products", []):
            if p.get("name") == product:
                uup_uuid = str(p.get("latest_uup_uuid", ""))
                uup_title = str(p.get("latest_uup_title", ""))
                break

    return Profile(
        name=name,
        product=product,
        edition=edition,
        language=str(data.get("language", "en-us")),
        compression=compression,
        label=str(data.get("label", name)),
        uup_uuid=uup_uuid,
        uup_title=uup_title,
    )


def resolve_dispatch(name: str, *, profiles_dir: Path = PROFILES_DIR) -> dict[str, str]:
    """Load a profile and return the env-var shape the build workflow expects.

    Returns a dict suitable for setting as GitHub Actions env vars:
        PROFILE, PRODUCT, EDITION, LANGUAGE, COMPRESSION, LABEL, UUP_UUID, UUP_TITLE
    """
    profile = load(name, profiles_dir=profiles_dir)
    if not profile.uup_uuid:
        raise ProfileError(
            f"profile {name!r}: no uup_uuid set. Either set uup_uuid in the profile "
            f"or run check-updates to populate products.yaml: latest_uup_uuid."
        )
    info("profile.loaded", name=profile.name, product=profile.product,
         edition=profile.edition, compression=profile.compression,
         uup_uuid=profile.uup_uuid[:8])
    return profile.to_dispatch_payload()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Resolve a build profile")
    parser.add_argument("name", help="Profile name (e.g. win11-prod)", nargs="?")
    parser.add_argument("--list", action="store_true", help="List all available profiles")
    args = parser.parse_args()

    if args.list:
        for n in list_profiles():
            print(n)
        sys.exit(0)

    if not args.name:
        parser.error("profile name required (or use --list)")

    try:
        payload = resolve_dispatch(args.name)
    except ProfileError as e:
        error("profile.error", name=args.name, error=str(e))
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
    for k, v in payload.items():
        # Quote values so titles with () or spaces don't break shell eval
        v_escaped = v.replace('"', '\\"')
        print(f'{k}="{v_escaped}"')
