from pathlib import Path
import os
import subprocess
import sys
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


@pytest.fixture(autouse=True)
def _reset_config_root():
    """Reset the module-level CONFIG_ROOT before each test.

    Some tests monkeypatch WINFORGE_CONFIG_ROOT. Without this fixture,
    the constants stay stale from the previous test.
    """
    # Make sure the env var doesn't leak from another test
    os.environ.pop("WINFORGE_CONFIG_ROOT", None)
    from scripts.profiles import load
    load.reset_config_root()
    yield
    os.environ.pop("WINFORGE_CONFIG_ROOT", None)
    load.reset_config_root()


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


def test_find_config_root_resolves_to_config_subdir(monkeypatch, tmp_path: Path):
    """When WINFORGE_CONFIG_ROOT points at a repo root, return its config/ subdir.

    This is the caller-mode case: the consumer sets WINFORGE_CONFIG_ROOT to
    their repo root, and we auto-append /config to find profiles.
    """
    # Set up a fake config dir
    (tmp_path / "config" / "profiles").mkdir(parents=True)
    (tmp_path / "config" / "products.yaml").write_text("products: []")
    (tmp_path / "config" / "editions.yaml").write_text("editions: {}")

    monkeypatch.setenv("WINFORGE_CONFIG_ROOT", str(tmp_path))
    from scripts.profiles import load
    load.reset_config_root()
    assert load.CONFIG_ROOT == tmp_path / "config"
    assert load.PROFILES_DIR == tmp_path / "config" / "profiles"


def test_find_config_root_resolves_to_pointed_path_when_no_config_subdir(
    monkeypatch, tmp_path: Path
):
    """If WINFORGE_CONFIG_ROOT points at the config dir itself (no config/ inside), use it directly."""
    (tmp_path / "profiles").mkdir()
    monkeypatch.setenv("WINFORGE_CONFIG_ROOT", str(tmp_path))
    from scripts.profiles import load
    load.reset_config_root()
    assert load.CONFIG_ROOT == tmp_path
    assert load.PROFILES_DIR == tmp_path / "profiles"


def test_find_config_root_relative_to_workspace(monkeypatch, tmp_path: Path):
    """Relative WINFORGE_CONFIG_ROOT is resolved against $GITHUB_WORKSPACE."""
    (tmp_path / "config" / "profiles").mkdir(parents=True)
    (tmp_path / "config" / "products.yaml").write_text("products: []")
    (tmp_path / "config" / "editions.yaml").write_text("editions: {}")

    monkeypatch.setenv("GITHUB_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("WINFORGE_CONFIG_ROOT", ".")
    from scripts.profiles import load
    load.reset_config_root()
    assert load.CONFIG_ROOT == tmp_path / "config"


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
    # Values are quoted to handle titles with () or spaces
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


def test_cli_output_is_eval_safe():
    """The __main__ output must be eval-safe (values quoted)."""
    import os
    env = {**os.environ, "PYTHONPATH": str(REPO_ROOT)}
    result = subprocess.run(
        [sys.executable, "-m", "scripts.profiles.load", "win11-prod"],
        capture_output=True, text=True, cwd=REPO_ROOT, env=env,
    )
    assert result.returncode == 0
    # Each line must be KEY="value" so parens/spaces in titles are safe
    for line in result.stdout.strip().splitlines():
        assert line.startswith(('PROFILE=', 'PRODUCT=', 'EDITION=', 'LANGUAGE=',
                                'COMPRESSION=', 'LABEL=', 'UUP_UUID=', 'UUP_TITLE='))
        # Values should be wrapped in double quotes
        _, _, val = line.partition("=")
        assert val.startswith('"') and val.endswith('"'), f"unquoted: {line!r}"


def test_resolve_dispatch_returns_env_shape():
    payload = resolve_dispatch("win11-prod", profiles_dir=PROFILES_DIR)
    assert payload["PRODUCT"] == "win11-24h2"
    assert payload["EDITION"] == "professional"
    assert payload["COMPRESSION"] == "wim"
    assert payload["LANGUAGE"] == "en-us"
    assert payload["UUP_UUID"] == "ebfcd736-eb43-42c3-aff2-35445412d076"


def test_resolve_dispatch_errors_when_no_uuid():
    """If neither profile nor products.yaml has a uup_uuid, raise."""
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
