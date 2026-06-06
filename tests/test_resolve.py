from scripts.uupd.resolve import resolve
from scripts.uupd.scrape import Build

def make(title: str, arch: str = "x64") -> Build:
    return Build(title=title, arch=arch, uuid="x" * 36, added_at="2026-01-01T00:00:00Z")

def test_resolves_24h2():
    r = resolve(make("Windows 11, version 24H2 (26100.1234) amd64"))
    assert r is not None
    assert r["name"] == "win11-24h2"
    assert r["family"] == "win11"
    assert r["arch"] == "x64"

def test_skips_arm64():
    r = resolve(make("Windows 11, version 24H2 (26100.1234) arm64", arch="arm64"))
    assert r is None

def test_resolves_ltsc():
    r = resolve(make("Windows 11 IoT Enterprise LTSC (26200.5000) amd64"))
    assert r is not None
    assert r["name"] == "win11-ltsc"
    assert r["track"] == "ltsc"

def test_unknown_returns_none():
    r = resolve(make("Totally Made-Up Windows 99"))
    assert r is None
