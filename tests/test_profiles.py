from pathlib import Path
import textwrap
import pytest
from scripts.profiles.load import (
    Profile,
    ProfileError,
    _PROFILE_NAME_RE,
    _from_dict,
    list_profiles,
    load,
    resolve_dispatch,
)

REPO_ROOT = Path(__file__).parent.parent
PROFILES_DIR = REPO_ROOT / "config" / "profiles"


def test_profile_name_regex_validates_form():
    assert _PROFILE_NAME_RE.match("win11-prod")
    assert _PROFILE_NAME_RE.match("a")
    assert _PROFILE_NAME_RE.match("test-123")
    # Disallowed: uppercase, leading dash, special chars
    assert not _PROFILE_NAME_RE.match("Win11-prod")
    assert not _PROFILE_NAME_RE.match("-win11")
    assert not _PROFILE_NAME_RE.match("win11_prod")
    # Too long
    assert not _PROFILE_NAME_RE.match("a" * 65)


def test_list_profiles_finds_yaml_files(tmp_path: Path):
    (tmp_path / "alpha.yaml").write_text("product: win11-24h2\nedition: professional\n")
    (tmp_path / "beta.yaml").write_text("product: win11-24h2\nedition: enterprise\n")
    (tmp_path / "NotAllowed.yaml").write_text("product: win11-24h2\nedition: professional\n")
    (tmp_path / "ignored.txt").write_text("text")
    names = list_profiles(profiles_dir=tmp_path)
    assert names == ["alpha", "beta"]


def test_load_returns_profile_with_defaults():
    p = load("win11-prod", profiles_dir=PROFILES_DIR)
    assert p.name == "win11-prod"
    assert p.product == "win11-24h2"
    assert p.edition == "professional"
    assert p.language == "en-us"
    assert p.compression == "wim"
    # label defaults to name
    assert p.label == "win11-prod"


def test_load_ltsc_profile_has_esd_compression():
    p = load("win11-ltsc", profiles_dir=PROFILES_DIR)
    assert p.product == "win11-ltsc"
    assert p.edition == "iotenterprise"
    assert p.compression == "esd"


def test_load_inherits_uup_uuid_from_products_yaml():
    p = load("win11-prod", profiles_dir=PROFILES_DIR)
    # products.yaml has latest_uup_uuid for win11-24h2
    assert p.uup_uuid == "ebfcd736-eb43-42c3-aff2-35445412d076"
    assert "28000" in p.uup_title


def test_load_missing_profile_raises():
    with pytest.raises(ProfileError, match="not found"):
        load("nonexistent", profiles_dir=PROFILES_DIR)


def test_load_invalid_name_raises():
    with pytest.raises(ProfileError, match="invalid profile name"):
        load("../etc/passwd", profiles_dir=PROFILES_DIR)


def test_load_unknown_product_raises(tmp_path: Path):
    (tmp_path / "bad.yaml").write_text("product: win99-future\nedition: pro\n")
    with pytest.raises(ProfileError, match="not in products.yaml"):
        load("bad", profiles_dir=tmp_path)


def test_load_unknown_edition_raises(tmp_path: Path):
    (tmp_path / "bad.yaml").write_text("product: win11-24h2\nedition: super-pro\n")
    with pytest.raises(ProfileError, match="not in editions.yaml"):
        load("bad", profiles_dir=tmp_path)


def test_load_invalid_compression_raises(tmp_path: Path):
    (tmp_path / "bad.yaml").write_text(
        "product: win11-24h2\nedition: professional\ncompression: bzip\n"
    )
    with pytest.raises(ProfileError, match="compression"):
        load("bad", profiles_dir=tmp_path)


def test_profile_to_dispatch_payload_shape():
    p = Profile(
        name="my-build",
        product="win11-24h2",
        edition="professional",
        compression="wim",
        label="my-build",
        uup_uuid="abc-123",
    )
    payload = p.to_dispatch_payload()
    # Keys are UPPERCASE — these go directly into GitHub Actions env vars
    assert payload == {
        "PROFILE": "my-build",
        "PRODUCT": "win11-24h2",
        "EDITION": "professional",
        "LANGUAGE": "en-us",
        "COMPRESSION": "wim",
        "LABEL": "my-build",
        "UUP_UUID": "abc-123",
        "UUP_TITLE": "",
    }


def test_resolve_dispatch_returns_env_shape():
    payload = resolve_dispatch("win11-prod", profiles_dir=PROFILES_DIR)
    assert payload["PRODUCT"] == "win11-24h2"
    assert payload["EDITION"] == "professional"
    assert payload["COMPRESSION"] == "wim"
    assert payload["LANGUAGE"] == "en-us"
    assert payload["UUP_UUID"] == "ebfcd736-eb43-42c3-aff2-35445412d076"


def test_resolve_dispatch_errors_when_no_uuid():
    """If neither profile nor products.yaml has a uup_uuid, raise."""
    import yaml
    (PROFILES_DIR / "win11-prod.yaml").write_text(
        textwrap.dedent("""
            product: win11-24h2
            edition: professional
        """)
    )
    # Temporarily blank out the uuid
    products_path = REPO_ROOT / "config" / "products.yaml"
    orig = products_path.read_text()
    products_path.write_text(textwrap.dedent("""
        products:
          - name: win11-24h2
            latest_uup_uuid: ""
    """))
    try:
        with pytest.raises(ProfileError, match="no uup_uuid"):
            resolve_dispatch("win11-prod", profiles_dir=PROFILES_DIR)
    finally:
        products_path.write_text(orig)
        (PROFILES_DIR / "win11-prod.yaml").write_text(textwrap.dedent("""
            product: win11-24h2
            edition: professional
            language: en-us
            compression: wim
            label: win11-prod
        """))


def test_from_dict_minimal_required_fields():
    """A profile with just product + edition gets sensible defaults."""
    p = _from_dict("minimal", {
        "product": "win11-24h2",
        "edition": "professional",
    })
    assert p.name == "minimal"
    assert p.label == "minimal"  # defaults to name
    assert p.compression == "wim"
    assert p.language == "en-us"


def test_from_dict_missing_required_field():
    with pytest.raises(ProfileError, match="missing required field"):
        _from_dict("incomplete", {"product": "win11-24h2"})


def test_custom_label_persists():
    p = load("win10-legacy", profiles_dir=PROFILES_DIR)
    assert p.label == "win10-legacy"
    assert p.compression == "esd"
    assert p.edition == "professional"
