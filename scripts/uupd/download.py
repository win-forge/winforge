"""Generate UUP-dump download+conversion inputs for a build+edition combination.

UUP-dump's modern API (as of mid-2026):
- GET /get.php?id=<uuid>&pack=<lang>&edition=<id>
- Returns HTML with a table of <a href="tlu.dl.delivery.mp.microsoft.com/...">filename</a>
- An inline <textarea> with a .cmd rename script (Windows) and SHA-1 manifest
- No separate converter scripts to download — we extract everything from the page
"""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import argparse
import hashlib
import json
import re
import subprocess
import requests


@dataclass
class FileEntry:
    """One UUP file from the page."""
    url: str           # Microsoft download URL (CDN)
    guid_filename: str # The GUID-named file as it comes from the CDN
    target_name: str   # The friendly name after the rename script runs
    sha1: str          # Expected SHA-1


@dataclass
class ConversionInputs:
    files: list[FileEntry]
    rename_cmd: str    # Windows .cmd script to rename GUIDs → friendly names
    sha1_manifest: str # sha1sum-compatible text
    raw_html: str


def build_request(uuid: str, edition: str, lang: str = "en-us") -> str:
    """UUP-dump get.php requires id + pack (lang, lowercase) + edition."""
    return (
        f"https://uupdump.net/get.php?id={uuid}"
        f"&pack={lang}&edition={edition}"
    )


# Match the new Microsoft CDN URLs in the file table
_CDN_URL_RE = re.compile(
    r'<a\s+href="(https?://tlu\.dl\.delivery\.mp\.microsoft\.com/filestreamingservice/files/[^"]+)"\s*>\s*'
    r'([^<]+?)\s*</a>',
    re.DOTALL,
)
# Match the SHA-1 manifest textarea content
_SHA1_RE = re.compile(r"^([0-9a-f]{40})\s+\*?(.+?)\s*$", re.MULTILINE)


def parse_response(html: str) -> ConversionInputs:
    files: list[FileEntry] = []
    # The page table uses Microsoft CDN URLs. The friendly name is the link text
    # (which is the target name after the .cmd script renames the GUID file).
    for m in _CDN_URL_RE.finditer(html):
        url = m.group(1)
        target_name = m.group(2).strip()
        # Extract GUID from URL: .../files/<guid>?...
        guid_match = re.search(r"/files/([0-9a-f-]{36})", url)
        if not guid_match:
            continue
        guid_filename = guid_match.group(1)
        files.append(FileEntry(
            url=url,
            guid_filename=guid_filename,
            target_name=target_name,
            sha1="",  # Filled in by hash lookup below
        ))

    # Extract the SHA-1 manifest textarea and map sha1 → target_name
    sha1_by_name: dict[str, str] = {}
    textareas = re.findall(r"<textarea[^>]*>(.*?)</textarea>", html, re.DOTALL)
    for ta in textareas:
        # Skip the .cmd rename script (has @echo + rename) and HTML fragments
        if "@echo" in ta or "rename" in ta or "<" in ta:
            continue
        # Looks like a sha1sum manifest
        for m in _SHA1_RE.finditer(ta):
            sha1_by_name[m.group(2).strip()] = m.group(1)

    # Fill in sha1 on each FileEntry
    for f in files:
        if f.target_name in sha1_by_name:
            f.sha1 = sha1_by_name[f.target_name]

    # Extract the .cmd rename script
    rename_cmd = ""
    for ta in textareas:
        if "@echo off" in ta and "rename" in ta:
            rename_cmd = ta.strip()
            break

    # Extract the SHA-1 manifest as a separate string
    sha1_manifest = ""
    for ta in textareas:
        if ta.strip() and "@echo" not in ta and ta.strip().split("\n", 1)[0].split()[0:1]:
            first_line = ta.strip().split("\n", 1)[0]
            if re.match(r"^[0-9a-f]{40}\s+", first_line):
                sha1_manifest = ta.strip()
                break

    return ConversionInputs(
        files=files,
        rename_cmd=rename_cmd,
        sha1_manifest=sha1_manifest,
        raw_html=html,
    )


def fetch(uuid: str, edition: str, lang: str = "en-us") -> ConversionInputs:
    r = requests.get(build_request(uuid, edition, lang), timeout=60)
    r.raise_for_status()
    return parse_response(r.text)


def download_files(inputs: ConversionInputs, output_dir: Path) -> list[Path]:
    """Download all UUP files via aria2 from the Microsoft CDN URLs.

    Returns list of local file paths (using GUID filenames, as downloaded).
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    # Write aria2 input file
    arena_input = output_dir / "aria2.txt"
    with open(arena_input, "w") as fh:
        for f in inputs.files:
            fh.write(f"{f.url}\n")
            fh.write(f"  out={f.guid_filename}\n")

    # Run aria2
    result = subprocess.run(
        ["aria2c", "-c", "-x4", "-s4", "--dir", str(output_dir), "-i", str(arena_input)],
        capture_output=True, text=True, timeout=600,  # 10 min cap on the download
    )
    if result.returncode != 0:
        # aria2 sometimes writes error details to stdout, not stderr
        err = (result.stderr or "").strip() or (result.stdout or "").strip() or "(no output)"
        raise RuntimeError(f"aria2 failed (rc={result.returncode}):\n{err}")

    # Write the rename script
    if inputs.rename_cmd:
        rename_path = output_dir / "uup_rename_windows.cmd"
        rename_path.write_text(inputs.rename_cmd)
        rename_path.chmod(0o755)

    # Write the SHA-1 manifest
    if inputs.sha1_manifest:
        (output_dir / "SHA1").write_text(inputs.sha1_manifest + "\n")

    # Generate sha256 hashes of what we actually downloaded
    hashes = {}
    for f in inputs.files:
        path = output_dir / f.guid_filename
        if path.exists() and path.stat().st_size > 0:
            h = hashlib.sha256()
            with open(path, "rb") as fh:
                for chunk in iter(lambda: fh.read(1 << 20), b""):
                    h.update(chunk)
            hashes[f.guid_filename] = h.hexdigest()
    (output_dir / "hashes.json").write_text(json.dumps(hashes, indent=2))

    return [output_dir / f.guid_filename for f in inputs.files if (output_dir / f.guid_filename).exists()]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fetch UUP-dump files for a build+edition")
    parser.add_argument("uuid", help="UUP-dump build UUID")
    parser.add_argument("edition", help="Edition (professional, enterprise, etc.)")
    parser.add_argument("--output-dir", "-o", default="./uup-files", help="Output directory")
    parser.add_argument("--lang", default="en-us", help="Language code (lowercase, e.g. en-us)")
    args = parser.parse_args()

    inputs = fetch(args.uuid, args.edition, args.lang)
    files = download_files(inputs, Path(args.output_dir))

    print(f"Downloaded {len(files)} files to {args.output_dir}/")
    for f in files:
        print(f"  {f.name} ({f.stat().st_size / 1e6:.1f} MB)")
