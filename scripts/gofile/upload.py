"""Gofile uploader for winforge.

Public API: https://gofile.io/api
  - GET  /servers                                    -> best server list
  - POST {server}/contents/uploadfile (multipart)    -> upload a file
  - GET  /accounts/getid  (auth)                     -> account id + rootFolder
  - POST /contents/createFolder (auth)               -> create a sub-folder

Guest uploads (no token) get a server-created default folder — we can't pick
the name, and content expires. With an account JWT, content persists, we get
a manager link, and we can pre-create /{product}/{edition}/ folders.

Auth: `Authorization: Bearer <jwt>` header. JWT lives in the `GOFILE_TOKEN`
secret.
"""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import os
import sys
from typing import Any
import requests

from scripts.lib.log import info, error, warn


API_BASE = "https://api.gofile.io"
HTTP_TIMEOUT = 30
UPLOAD_TIMEOUT = 60 * 60 * 2  # ISOs are big; 2h per file


@dataclass
class UploadResult:
    file_id: str
    folder_id: str
    folder_url: str           # public download page
    direct_url: str | None
    manager_url: str | None
    guest_token: str | None   # present for guest uploads; needed to manage later


def _headers(token: str | None) -> dict[str, str]:
    h: dict[str, str] = {}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def get_best_server(token: str | None = None) -> str:
    r = requests.get(f"{API_BASE}/servers", headers=_headers(token), timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    body = r.json()
    if body.get("status") != "ok":
        raise RuntimeError(f"gofile /servers error: {body}")
    servers = body.get("data", {}).get("servers", [])
    if not servers:
        raise RuntimeError("gofile /servers returned no servers")
    return servers[0]["name"]


def get_account_root(token: str) -> str:
    """Return the account's rootFolder id. Requires auth token."""
    r = requests.get(f"{API_BASE}/accounts/getid",
                     headers=_headers(token), timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    body = r.json()
    if body.get("status") != "ok":
        raise RuntimeError(f"gofile /accounts/getid error: {body}")
    return body["data"]["rootFolder"]


def create_folder(name: str, parent_id: str, token: str) -> str:
    """Create a folder under parent_id. Requires auth token.

    NOTE: endpoint is on api.gofile.io, not the upload server.
    """
    r = requests.post(
        f"{API_BASE}/contents/createFolder",
        json={"folderName": name, "parentFolderId": parent_id},
        headers=_headers(token), timeout=HTTP_TIMEOUT,
    )
    r.raise_for_status()
    body = r.json()
    if body.get("status") != "ok":
        raise RuntimeError(f"gofile createFolder error: {body}")
    return body["data"]["id"]


def upload_file(server: str, local_path: Path,
                folder_id: str | None = None,
                token: str | None = None) -> dict[str, Any]:
    """Multipart upload. Without `folder_id` the server creates a guest folder."""
    with local_path.open("rb") as fh:
        files = {"file": (local_path.name, fh)}
        data: dict[str, str] = {}
        if folder_id:
            data["folderId"] = folder_id
        r = requests.post(
            f"https://{server}.gofile.io/contents/uploadfile",
            files=files, data=data, headers=_headers(token),
            timeout=UPLOAD_TIMEOUT,
        )
    r.raise_for_status()
    body = r.json()
    if body.get("status") != "ok":
        raise RuntimeError(f"gofile upload error: {body}")
    return body["data"]


def upload_iso(local_path: Path, *, product: str, edition: str,
               token: str | None = None) -> UploadResult:
    """High-level: get server, optionally make /{product}/{edition}, upload.

    - With token:   creates /{rootFolder}/{product}/{edition}/, file goes in edition folder.
    - Without token: file is uploaded to a server-generated guest folder.
    """
    if not local_path.exists():
        raise FileNotFoundError(local_path)

    server = get_best_server(token)
    info("gofile.server", server=server)

    target_folder: str | None = None
    if token:
        root = get_account_root(token)
        product_id = create_folder(product, root, token)
        info("gofile.folder.product", product=product, folder_id=product_id)
        target_folder = create_folder(edition, product_id, token)
        info("gofile.folder.edition", edition=edition, folder_id=target_folder)
    else:
        warn("gofile.no_token",
             hint="uploads as guest — content expires. Set GOFILE_TOKEN for persistence "
                  "and custom folder names.")

    size_gb = local_path.stat().st_size / 1e9
    info("gofile.upload.start", path=str(local_path), size_gb=round(size_gb, 2),
         target_folder=target_folder or "guest")
    res = upload_file(server, local_path, folder_id=target_folder, token=token)

    file_id = res.get("id", "")
    folder_url = res.get("downloadPage", "")
    parent_folder = res.get("parentFolder", target_folder or "")
    info("gofile.upload.done", file_id=file_id, folder_url=folder_url,
         parent_folder=parent_folder)

    return UploadResult(
        file_id=file_id,
        folder_id=parent_folder,
        folder_url=folder_url,
        direct_url=res.get("directLink"),
        manager_url=res.get("managerAccess") or res.get("link"),
        guest_token=res.get("guestToken"),
    )


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="Upload a Windows ISO to gofile.io")
    p.add_argument("iso", type=Path, help="Path to ISO file")
    p.add_argument("--product", required=True, help="e.g. win11-24h2")
    p.add_argument("--edition", required=True, help="e.g. professional")
    p.add_argument("--token", default=os.environ.get("GOFILE_TOKEN"),
                   help="GoFile JWT (or set GOFILE_TOKEN env). Optional — guest uploads expire.")
    args = p.parse_args()
    try:
        result = upload_iso(
            args.iso, product=args.product, edition=args.edition, token=args.token,
        )
    except Exception as e:
        error("gofile.failed", error=str(e))
        sys.exit(1)
    print(f"folder_url={result.folder_url}")
    print(f"file_id={result.file_id}")
    print(f"folder_id={result.folder_id}")
    if result.direct_url:
        print(f"direct_url={result.direct_url}")
    if result.manager_url:
        print(f"manager_url={result.manager_url}")
    if result.guest_token:
        print(f"guest_token={result.guest_token}")
