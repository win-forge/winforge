from pathlib import Path
import pytest
from scripts.uupd.scrape import parse_known_page, Build

FIX = Path(__file__).parent / "fixtures" / "known_uup.html"

@pytest.fixture
def builds() -> list[Build]:
    return parse_known_page(FIX.read_text())

def test_returns_nonempty_list(builds):
    assert len(builds) > 50

def test_build_has_required_fields(builds):
    b = builds[0]
    assert b.title
    assert b.arch in ("x64", "arm64", "x86")
    assert b.uuid
    assert b.added_at

def test_contains_win11_x64(builds):
    matches = [b for b in builds if "Windows 11" in b.title and b.arch == "x64"]
    assert matches, "fixture should contain at least one Windows 11 x64 build"

def test_to_dict_roundtrip(builds):
    d = builds[0].to_dict()
    assert "title" in d and "uuid" in d and "arch" in d
