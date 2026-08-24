import hashlib
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from scripts.uupd.download import (
    _CDN_URL_RE,
    ConversionInputs,
    FileEntry,
    build_request,
    download_files,
    fetch,
    parse_response,
)

REPO_ROOT = Path(__file__).parent.parent
UUID = "00000000-0000-0000-0000-000000000001"


def test_build_request_shapes_query():
    url = build_request(UUID, edition="professional", lang="en-us")
    assert "uupdump.net" in url
    assert UUID in url
    assert "professional" in url.lower() or "edition=" in url.lower()
    # UUP-dump uses 'pack' (not 'lang') and lowercase lang codes
    assert "pack=" in url
    assert "pack=en-us" in url


def test_build_request_uses_pack_not_lang():
    """Regression: UUP-dump changed the param from lang= to pack= in their get.php."""
    url = build_request(UUID, "professional", "de-de")
    assert "pack=de-de" in url
    assert "lang=" not in url


# --- Real UUP-dump HTML parsing ---

REAL_HTML = """
<table>
<tr><td><a href="http://tlu.dl.delivery.mp.microsoft.com/filestreamingservice/files/750083a4-499c-4a6c-9b55-53fa6002b438?P1=123">DesktopDeployment.cab</a></td>
    <td>2026-06-10</td>
    <td><code>159a0502dda74856f91807ebde158bc10b3eaf10</code></td>
    <td>13 MiB</td></tr>
<tr><td><a href="http://tlu.dl.delivery.mp.microsoft.com/filestreamingservice/files/202135bc-c77c-4e5d-a6fd-d8ee203c36cd?P1=123">Edge.wim</a></td>
    <td>2026-06-10</td>
    <td><code>8cbf831243845318634fcd4121af397969a6bc39</code></td>
    <td>50 MiB</td></tr>
</table>
<textarea>@echo off
rename "750083a4-499c-4a6c-9b55-53fa6002b438" "DesktopDeployment.cab"
rename "202135bc-c77c-4e5d-a6fd-d8ee203c36cd" "Edge.wim"
</textarea>
<textarea>159a0502dda74856f91807ebde158bc10b3eaf10 *DesktopDeployment.cab
8cbf831243845318634fcd4121af397969a6bc39 *Edge.wim
</textarea>
"""


def test_parse_response_extracts_cdn_files():
    parsed = parse_response(REAL_HTML)
    assert len(parsed.files) == 2
    assert parsed.files[0].target_name == "DesktopDeployment.cab"
    assert parsed.files[0].guid_filename == "750083a4-499c-4a6c-9b55-53fa6002b438"
    assert "tlu.dl.delivery.mp.microsoft.com" in parsed.files[0].url
    assert "750083a4" in parsed.files[0].url


def test_parse_response_extracts_sha1_from_manifest():
    parsed = parse_response(REAL_HTML)
    by_name = {f.target_name: f.sha1 for f in parsed.files}
    assert by_name["DesktopDeployment.cab"] == "159a0502dda74856f91807ebde158bc10b3eaf10"
    assert by_name["Edge.wim"] == "8cbf831243845318634fcd4121af397969a6bc39"


def test_parse_response_extracts_rename_cmd():
    parsed = parse_response(REAL_HTML)
    assert "@echo off" in parsed.rename_cmd
    assert 'rename "750083a4-499c-4a6c-9b55-53fa6002b438" "DesktopDeployment.cab"' in parsed.rename_cmd
    assert 'rename "202135bc-c77c-4e5d-a6fd-d8ee203c36cd" "Edge.wim"' in parsed.rename_cmd


def test_parse_response_extracts_sha1_manifest_text():
    parsed = parse_response(REAL_HTML)
    assert "159a0502dda74856f91807ebde158bc10b3eaf10" in parsed.sha1_manifest
    assert "8cbf831243845318634fcd4121af397969a6bc39" in parsed.sha1_manifest


def test_parse_response_handles_empty_page():
    parsed = parse_response("<html><body>Nothing here</body></html>")
    assert parsed.files == []
    assert parsed.rename_cmd == ""


def test_cdn_url_regex_matches_actual_format():
    """Verify our regex matches what UUP-dump actually returns (regression)."""
    url = "http://tlu.dl.delivery.mp.microsoft.com/filestreamingservice/files/750083a4-499c-4a6c-9b55-53fa6002b438?P1=1781126831&P2=404&P3=2&P4=WfFScSQ08c5cdlrvl%2fzi9p5LiNpU1oNIiOB7ROGHxqsu3FXBh6Mh9PvHGqnwkGJxPdUZ0K7apbCqxMi4mukgRQ%3d%3d"
    html = f'<a href="{url}">DesktopDeployment.cab</a>'
    m = _CDN_URL_RE.search(html)
    assert m is not None
    assert m.group(1) == url
    assert m.group(2).strip() == "DesktopDeployment.cab"


# --- Live fetch test (skipped on network failure) ---

def test_fetch_real_uup_dump():
    """Hit the real UUP-dump get.php and verify the parser works on production HTML."""
    real_uuid = "ebfcd736-eb43-42c3-aff2-35445412d076"  # Win11 28000.2179 amd64
    try:
        inputs = fetch(real_uuid, "professional", "en-us")
    except Exception as e:
        pytest.skip(f"Cannot reach UUP-dump: {e}")
    assert len(inputs.files) > 50
    assert inputs.rename_cmd
    assert inputs.sha1_manifest
    # All files should have Microsoft CDN URLs
    for f in inputs.files:
        assert "tlu.dl.delivery.mp.microsoft.com" in f.url


# --- download_files with mocked network + aria2 ---

def test_download_files_runs_aria2_and_writes_hashes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Mock aria2; verify aria2 invoked, sha1 + hashes manifest written."""
    inputs = ConversionInputs(
        files=[
            FileEntry(
                url="http://tlu.dl.delivery.mp.microsoft.com/filestreamingservice/files/aaaa?P1=1",
                guid_filename="aaaa",
                target_name="A.cab",
                sha1="606ec6e9bd8a8ff2ad14e5fade3f264471e82251",  # actual sha1 of the fake payload b"AAA"
            ),
            FileEntry(
                url="http://tlu.dl.delivery.mp.microsoft.com/filestreamingservice/files/bbbb?P1=1",
                guid_filename="bbbb",
                target_name="B.esd",
                sha1="",
            ),
        ],
        rename_cmd='@echo off\nrename "aaaa" "A.cab"\n',
        sha1_manifest="159a0502dda74856f91807ebde158bc10b3eaf10 *A.cab\n",
        raw_html="",
    )
    out = tmp_path / "uup"
    out.mkdir()
    (out / "aaaa").write_bytes(b"AAA")
    (out / "bbbb").write_bytes(b"BBBB")

    def fake_aria2(*args, **kwargs):
        return MagicMock(returncode=0, stdout="", stderr="")
    monkeypatch.setattr("subprocess.run", fake_aria2)

    download_files(inputs, out)

    # Files exist
    assert (out / "aaaa").exists()
    assert (out / "bbbb").exists()
    # Rename script written
    assert (out / "uup_rename_windows.cmd").exists()
    assert "rename" in (out / "uup_rename_windows.cmd").read_text()
    # SHA-1 manifest written
    assert (out / "SHA1").exists()
    # sha256 hashes
    assert (out / "hashes.json").exists()
    import json
    hashes = json.loads((out / "hashes.json").read_text())
    assert "aaaa" in hashes
    assert hashes["aaaa"] == hashlib.sha256(b"AAA").hexdigest()
    # aria2 input file references Microsoft CDN
    aria2 = (out / "aria2.txt").read_text()
    assert "tlu.dl.delivery.mp.microsoft.com" in aria2


def test_download_files_sha1_verification_passes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """When content matches the manifest sha1, download_files succeeds."""
    payload = b"REAL-CONTENT"
    inputs = ConversionInputs(
        files=[FileEntry(
            url="http://tlu.dl.delivery.mp.microsoft.com/files/cccc?P1=1",
            guid_filename="cccc",
            target_name="C.cab",
            sha1=hashlib.sha1(payload).hexdigest(),
        )],
        rename_cmd="",
        sha1_manifest="",
        raw_html="",
    )
    out = tmp_path / "uup"
    out.mkdir()
    (out / "cccc").write_bytes(payload)
    monkeypatch.setattr("subprocess.run", lambda *a, **k: MagicMock(returncode=0, stdout="", stderr=""))
    result = download_files(inputs, out)
    assert result == [out / "cccc"]


def test_get_with_retry_uses_ua_and_backs_off(monkeypatch: pytest.MonkeyPatch):
    """403/5xx get retried with backoff; success carries the winforge UA."""
    from scripts.lib.http import USER_AGENT, get_with_retry

    seen_headers: list[dict] = []
    calls = {"n": 0}

    class FakeResp:
        def __init__(self, status: int):
            self.status_code = status

    def fake_get(url, timeout=None, headers=None):
        seen_headers.append(headers or {})
        calls["n"] += 1
        return FakeResp(403 if calls["n"] < 3 else 200)

    monkeypatch.setattr("requests.get", fake_get)
    monkeypatch.setattr("scripts.lib.http.time.sleep", lambda s: None)
    resp = get_with_retry("https://uupdump.net/known.php", attempts=3, backoff=0.1)
    assert resp.status_code == 200
    assert calls["n"] == 3
    assert all(h.get("User-Agent") == USER_AGENT for h in seen_headers)


def test_get_with_retry_exhausts_and_raises(monkeypatch: pytest.MonkeyPatch):
    from scripts.lib.http import get_with_retry

    monkeypatch.setattr("requests.get", lambda *a, **k: type("R", (), {"status_code": 503})())
    monkeypatch.setattr("scripts.lib.http.time.sleep", lambda s: None)
    with pytest.raises(RuntimeError, match="failed after 2 attempts"):
        get_with_retry("https://uupdump.net/known.php", attempts=2, backoff=0.1)


def test_fetch_latest_goes_through_helper(monkeypatch: pytest.MonkeyPatch):
    """fetch_latest must route via scripts.lib.http (UA + retry), not bare requests."""
    from scripts.uupd import scrape

    called = {"helper": False}

    class FakeResp:
        status_code = 200
        text = "<html><body>no table</body></html>"

        def raise_for_status(self):
            pass

    def fake_helper(url, **kwargs):
        called["helper"] = True
        return FakeResp()

    monkeypatch.setattr("scripts.lib.http.get_with_retry", fake_helper)
    scrape.fetch_latest()
    assert called["helper"]


def test_fetch_files_goes_through_helper(monkeypatch: pytest.MonkeyPatch):
    import scripts.uupd.download as dl

    class FakeResp:
        status_code = 200
        text = "<html></html>"

        def raise_for_status(self):
            pass

    monkeypatch.setattr(
        "scripts.lib.http.get_with_retry",
        lambda url, **k: FakeResp(),
    )
    inputs = dl.fetch(UUID, "professional")
    assert inputs.files == []


def test_download_files_sha1_verification_fails_on_corrupt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Corrupted download (sha1 mismatch) must raise, not pass silently."""
    inputs = ConversionInputs(
        files=[FileEntry(
            url="http://tlu.dl.delivery.mp.microsoft.com/files/dddd?P1=1",
            guid_filename="dddd",
            target_name="D.cab",
            sha1="0" * 40,
        )],
        rename_cmd="",
        sha1_manifest="",
        raw_html="",
    )
    out = tmp_path / "uup"
    out.mkdir()
    (out / "dddd").write_bytes(b"corrupted-or-truncated-data")
    monkeypatch.setattr("subprocess.run", lambda *a, **k: MagicMock(returncode=0, stdout="", stderr=""))
    with pytest.raises(RuntimeError, match="integrity check FAILED"):
        download_files(inputs, out)
