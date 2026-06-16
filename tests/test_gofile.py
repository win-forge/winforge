"""Tests for scripts.gofile.upload. All network calls are mocked — never hit gofile in CI."""
from __future__ import annotations
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from scripts.gofile import upload


def _mock_response(status: str = "ok", data: dict | None = None) -> MagicMock:
    r = MagicMock()
    r.json.return_value = {"status": status, "data": data or {}}
    r.raise_for_status = MagicMock()
    return r


def test_get_best_server_picks_first():
    fake = _mock_response("ok", {
        "servers": [{"name": "store-eu-par-5", "zone": "eu"},
                    {"name": "store-na-phx-1", "zone": "na"}],
    })
    with patch("scripts.gofile.upload.requests.get", return_value=fake) as g:
        server = upload.get_best_server()
    assert server == "store-eu-par-5"
    g.assert_called_once()
    assert g.call_args.args[0] == "https://api.gofile.io/servers"


def test_get_best_server_propagates_token():
    fake = _mock_response("ok", {"servers": [{"name": "store1", "zone": "na"}]})
    with patch("scripts.gofile.upload.requests.get", return_value=fake) as g:
        upload.get_best_server(token="jwt-xyz")
    headers = g.call_args.kwargs["headers"]
    assert headers["Authorization"] == "Bearer jwt-xyz"


def test_get_best_server_propagates_api_error():
    fake = _mock_response("error-notFound", {})
    with patch("scripts.gofile.upload.requests.get", return_value=fake):
        with pytest.raises(RuntimeError, match="error"):
            upload.get_best_server()


def test_get_best_server_empty_list():
    fake = _mock_response("ok", {"servers": []})
    with patch("scripts.gofile.upload.requests.get", return_value=fake):
        with pytest.raises(RuntimeError, match="no servers"):
            upload.get_best_server()


def test_get_account_root():
    fake = _mock_response("ok", {"rootFolder": "root-abc", "id": "acct-1"})
    with patch("scripts.gofile.upload.requests.get", return_value=fake) as g:
        root = upload.get_account_root("jwt-xyz")
    assert root == "root-abc"
    assert g.call_args.args[0] == "https://api.gofile.io/accounts/getid"
    assert g.call_args.kwargs["headers"]["Authorization"] == "Bearer jwt-xyz"


def test_get_account_root_propagates_error():
    fake = _mock_response("error-token", {})
    with patch("scripts.gofile.upload.requests.get", return_value=fake):
        with pytest.raises(RuntimeError, match="getid"):
            upload.get_account_root("bad-jwt")


def test_create_folder_uses_api_base():
    fake = _mock_response("ok", {"id": "folder-xyz"})
    with patch("scripts.gofile.upload.requests.post", return_value=fake) as p:
        fid = upload.create_folder("professional", "root-1", "jwt")
    assert fid == "folder-xyz"
    # Must hit api.gofile.io, NOT the upload server
    assert p.call_args.args[0] == "https://api.gofile.io/contents/createFolder"
    body = p.call_args.kwargs["json"]
    assert body == {"folderName": "professional", "parentFolderId": "root-1"}
    assert p.call_args.kwargs["headers"]["Authorization"] == "Bearer jwt"


def test_create_folder_propagates_error():
    fake = _mock_response("error", {"x": 1})
    with patch("scripts.gofile.upload.requests.post", return_value=fake):
        with pytest.raises(RuntimeError, match="createFolder"):
            upload.create_folder("x", "p", "jwt")


def test_upload_file_sends_multipart(tmp_path: Path):
    iso = tmp_path / "win11-24h2-professional.iso"
    iso.write_bytes(b"FAKEISO" * 1024)
    fake = _mock_response("ok", {
        "id": "file-123",
        "downloadPage": "https://gofile.io/d/abc",
        "parentFolder": "folder-xyz",
        "guestToken": "guest-tok",
    })
    with patch("scripts.gofile.upload.requests.post", return_value=fake) as p:
        res = upload.upload_file("store1", iso, folder_id="leaf-1")
    assert res["id"] == "file-123"
    files_kw = p.call_args.kwargs["files"]
    assert "file" in files_kw
    assert p.call_args.kwargs["data"] == {"folderId": "leaf-1"}
    # Must hit upload server
    assert p.call_args.args[0] == "https://store1.gofile.io/contents/uploadfile"


def test_upload_file_no_folder_id():
    iso = Path("/tmp/fake.iso")
    iso.write_bytes(b"x" * 10)
    fake = _mock_response("ok", {"id": "f1", "downloadPage": "u", "parentFolder": "g-1"})
    with patch("scripts.gofile.upload.requests.post", return_value=fake) as p:
        upload.upload_file("store1", iso)
    assert p.call_args.kwargs["data"] == {}
    iso.unlink()


def test_upload_iso_with_token_creates_two_folders(tmp_path: Path):
    """With a token, we hit getid, createFolder x2, then uploadfile."""
    iso = tmp_path / "win11.iso"
    iso.write_bytes(b"X" * 100)
    server_resp = _mock_response("ok", {"servers": [{"name": "store1", "zone": "na"}]})
    getid_resp = _mock_response("ok", {"rootFolder": "root-r", "id": "acct-1"})
    prod_resp = _mock_response("ok", {"id": "p-1"})
    ed_resp = _mock_response("ok", {"id": "e-1"})
    file_resp = _mock_response("ok", {
        "id": "f1",
        "downloadPage": "https://gofile.io/d/abc",
        "parentFolder": "e-1",
        "directLink": "https://store1.gofile.io/dl/abc",
    })
    with patch("scripts.gofile.upload.requests.get",
               side_effect=[server_resp, getid_resp]) as g, \
         patch("scripts.gofile.upload.requests.post",
               side_effect=[prod_resp, ed_resp, file_resp]) as p:
        result = upload.upload_iso(iso, product="win11-24h2",
                                   edition="professional", token="jwt")

    assert result.file_id == "f1"
    assert result.folder_id == "e-1"
    assert result.folder_url == "https://gofile.io/d/abc"
    assert result.direct_url == "https://store1.gofile.io/dl/abc"
    assert result.guest_token is None
    # GET: servers, getid
    assert g.call_count == 2
    # POST: createFolder(product), createFolder(edition), uploadfile
    assert p.call_count == 3
    assert p.call_args_list[0].args[0] == "https://api.gofile.io/contents/createFolder"
    assert p.call_args_list[1].args[0] == "https://api.gofile.io/contents/createFolder"
    assert p.call_args_list[2].args[0] == "https://store1.gofile.io/contents/uploadfile"


def test_upload_iso_without_token_skips_folder_creation(tmp_path: Path):
    """No token -> no getid, no createFolder calls. File goes to guest folder."""
    iso = tmp_path / "tiny.iso"
    iso.write_bytes(b"X" * 100)
    server_resp = _mock_response("ok", {"servers": [{"name": "store1", "zone": "na"}]})
    file_resp = _mock_response("ok", {
        "id": "f1",
        "downloadPage": "https://gofile.io/d/abc",
        "parentFolder": "guest-xyz",
        "guestToken": "gtok",
    })
    with patch("scripts.gofile.upload.requests.get", return_value=server_resp) as g, \
         patch("scripts.gofile.upload.requests.post", return_value=file_resp) as p:
        result = upload.upload_iso(iso, product="p", edition="e", token=None)
    # GET: only servers (no getid without token)
    assert g.call_count == 1
    # POST: only uploadfile (no createFolder without token)
    assert p.call_count == 1
    assert p.call_args.args[0] == "https://store1.gofile.io/contents/uploadfile"
    assert result.folder_id == "guest-xyz"
    assert result.guest_token == "gtok"


def test_upload_iso_propagates_api_error(tmp_path: Path):
    iso = tmp_path / "tiny.iso"
    iso.write_bytes(b"X" * 100)
    with patch("scripts.gofile.upload.requests.get",
               return_value=_mock_response("ok", {"servers": [{"name": "s1", "zone": "na"}]})), \
         patch("scripts.gofile.upload.requests.post",
               return_value=_mock_response("error", {"x": 1})):
        with pytest.raises(RuntimeError, match="upload"):
            upload.upload_iso(iso, product="p", edition="e")


def test_upload_iso_missing_file():
    with pytest.raises(FileNotFoundError):
        upload.upload_iso(Path("/nonexistent.iso"), product="p", edition="e")
