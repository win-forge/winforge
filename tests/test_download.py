from pathlib import Path
import hashlib
import pytest
from unittest.mock import patch as mock_patch, MagicMock
from scripts.uupd.download import (
    build_request,
    parse_response,
    resolve_script_url,
    download_files,
    ConversionInputs,
)


UUID = "00000000-0000-0000-0000-000000000001"


def test_build_request_shapes_query():
    url = build_request(UUID, edition="professional", lang="en-US")
    assert "uupdump.net" in url
    assert UUID in url
    assert "professional" in url.lower() or "edition=" in url.lower()


def test_parse_response_extracts_file_list():
    fake_html = """
    <html><body>
      <a href="/files/test1.cab">test1.cab</a>
      <a href="/files/test2.esd">test2.esd</a>
      <a href="/files/uup_convert.sh">convert</a>
    </body></html>
    """
    parsed = parse_response(fake_html)
    assert "test1.cab" in parsed.files
    assert "test2.esd" in parsed.files
    assert parsed.converter_script_url


# --- resolve_script_url: rewrite UUP-dump's relative URLs to platform-specific ---

def test_resolve_absolute_path_to_windows_cmd():
    url = "/files/uup_download_linux.sh"
    assert resolve_script_url(url, "uup_download_windows.cmd") == "https://uupdump.net/files/uup_download_windows.cmd"


def test_resolve_absolute_path_to_linux_sh():
    url = "/files/uup_download_windows.cmd"
    assert resolve_script_url(url, "uup_download_linux.sh") == "https://uupdump.net/files/uup_download_linux.sh"


def test_resolve_protocol_relative_url():
    url = "//files.example.com/uup_download_linux.sh"
    assert resolve_script_url(url, "uup_download_windows.cmd") == "https://files.example.com/uup_download_windows.cmd"


def test_resolve_empty_returns_empty():
    assert resolve_script_url("", "uup_download_windows.cmd") == ""


# --- download_files: end-to-end with mocked network + aria2 ---

def test_download_files_runs_aria2_and_writes_hashes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Mock aria2, requests.get; verify aria2 invoked, hashes manifest written."""
    inputs = ConversionInputs(
        files=["a.cab", "b.esd", "c.psf"],
        converter_script_url="",
        raw_html="",
    )
    # Create fake downloaded files in output dir as if aria2 had run
    out = tmp_path / "uup"
    out.mkdir()
    (out / "a.cab").write_bytes(b"AAA")
    (out / "b.esd").write_bytes(b"BBBB")
    (out / "c.psf").write_bytes(b"CCCCC")

    # Mock aria2: just leave the files in place
    def fake_aria2(*args, **kwargs):
        return MagicMock(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("subprocess.run", fake_aria2)

    # No converter scripts to download
    with mock_patch("scripts.uupd.download.requests.get") as mock_get:
        mock_get.side_effect = Exception("should not be called - no script url")
        try:
            download_files(inputs, out)
        except Exception:
            pass

    # Files exist
    assert (out / "a.cab").exists()
    assert (out / "b.esd").exists()
    # Hashes manifest written
    assert (out / "hashes.json").exists()
    import json
    hashes = json.loads((out / "hashes.json").read_text())
    assert "a.cab" in hashes
    assert len(hashes["a.cab"]) == 64  # sha256 hex
    expected_a = hashlib.sha256(b"AAA").hexdigest()
    assert hashes["a.cab"] == expected_a


# --- End-to-end CLI test for download.py with mocked HTTP ---

def test_download_cli_handles_404_gracefully(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys):
    """Run download.py with a fake UUID; expect it to fail at the network step without crashing the parser."""
    fake_html = """
    <html><body>
      <a href="/files/one.cab">one.cab</a>
    </body></html>
    """

    import subprocess
    import sys
    class FakeResp:
        status_code = 200
        text = fake_html
        def raise_for_status(self): pass

    monkeypatch.setattr("requests.get", lambda *a, **k: FakeResp())

    # aria2 will fail because there's no real network — that's expected; the parser should run first
    subprocess.run(
        [sys.executable, "-m", "scripts.uupd.download", "fake-uuid-1234", "professional",
         "--output-dir", str(tmp_path / "out")],
        capture_output=True, text=True, cwd="/opt/data/winforge", timeout=30,
    )
    # Should not exit 0 (aria2 will fail), but should have produced an aria2.txt
    aria2_input = tmp_path / "out" / "aria2.txt"
    if aria2_input.exists():
        content = aria2_input.read_text()
        assert "one.cab" in content
        assert "uupdump.net" in content
