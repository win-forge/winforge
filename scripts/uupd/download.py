"""Generate UUP-dump download+conversion inputs for a build+edition combination.

UUP-dump's modern API is a JSON-ish endpoint `get.php`; for stability we still
fetch the lang-selection HTML and parse the embedded file list and conversion
script URL. (The exact endpoint may change; keep this module thin so the
parse layer can be swapped.)
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
class ConversionInputs:
    files: list[str]
    converter_script_url: str
    raw_html: str  # for the script bundle download step


def build_request(uuid: str, edition: str, lang: str = "en-US") -> str:
    return (
        f"https://uupdump.net/get.php?id={uuid}"
        f"&lang={lang}&edition={edition}"
    )


_SCRIPT_RE = re.compile(r'href="([^"]+(?:convert|uup_[^"]+\.(?:cmd|sh))[^"]*)"', re.IGNORECASE)


def parse_response(html: str) -> ConversionInputs:
    files = re.findall(r'href="/?files/([^"]+)"', html)
    m_script = _SCRIPT_RE.search(html)
    script_url = m_script.group(1) if m_script else ""
    return ConversionInputs(files=files, converter_script_url=script_url, raw_html=html)


def fetch(uuid: str, edition: str, lang: str = "en-US") -> ConversionInputs:
    r = requests.get(build_request(uuid, edition, lang), timeout=60)
    r.raise_for_status()
    return parse_response(r.text)


def resolve_script_url(base_url: str, script_name: str) -> str:
    """Resolve a UUP-dump script URL to its platform-specific variant.

    Takes the parsed converter script URL and returns a URL for the
    requested script_name (e.g. 'uup_download_windows.cmd').
    """
    if not base_url:
        return ""
    base_dir = base_url.rsplit("/", 1)[0] + "/"
    if base_url.startswith("//"):
        return "https:" + base_dir + script_name
    if base_url.startswith("/"):
        return "https://uupdump.net" + base_dir + script_name
    return base_dir + script_name


def download_files(inputs: ConversionInputs, output_dir: Path) -> list[Path]:
    """Download all UUP files via aria2 from the 'files' URLs parsed from the page.

    Returns list of local file paths.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    base_url = "https://uupdump.net/files/"
    urls = [base_url + f for f in inputs.files]

    # Write aria2 input file
    arena_input = output_dir / "aria2.txt"
    with open(arena_input, "w") as fh:
        for url, local_name in zip(urls, inputs.files):
            fh.write(f"{url}\n")
            fh.write(f"  out={local_name}\n")

    # Run aria2
    result = subprocess.run(
        ["aria2c", "-c", "-x4", "-s4", "--dir", str(output_dir), "-i", str(arena_input)],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        raise RuntimeError(f"aria2 failed:\n{result.stderr}")

    # Download ALL converter scripts (both Linux and Windows)
    if inputs.converter_script_url:
        for script_name in ("uup_download_windows.cmd", "uup_download_linux.sh"):
            script_url = resolve_script_url(inputs.converter_script_url, script_name)
            if not script_url:
                continue
            try:
                r = requests.get(script_url, timeout=30)
                r.raise_for_status()
                script_path = output_dir / script_name
                script_path.write_text(r.text)
                script_path.chmod(0o755)
            except Exception:
                pass  # Not all scripts may exist for every build

    # Generate hashes manifest
    hashes = {}
    for path in output_dir.iterdir():
        if path.is_file() and path.suffix in (".cab", ".esd", ".psf", ".msu"):
            h = hashlib.sha256()
            with open(path, "rb") as f:
                for chunk in iter(lambda: f.read(1 << 20), b""):
                    h.update(chunk)
            hashes[path.name] = h.hexdigest()
    (output_dir / "hashes.json").write_text(json.dumps(hashes, indent=2))

    return [output_dir / f for f in inputs.files if (output_dir / f).exists()]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fetch UUP-dump files for a build+edition")
    parser.add_argument("uuid", help="UUP-dump build UUID")
    parser.add_argument("edition", help="Edition (professional, enterprise, etc.)")
    parser.add_argument("--output-dir", "-o", default="./uup-files", help="Output directory")
    parser.add_argument("--lang", default="en-US", help="Language code")
    args = parser.parse_args()

    inputs = fetch(args.uuid, args.edition, args.lang)
    files = download_files(inputs, Path(args.output_dir))

    print(f"Downloaded {len(files)} files to {args.output_dir}/")
    for f in files:
        print(f"  {f.name} ({f.stat().st_size / 1e6:.1f} MB)")
