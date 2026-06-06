"""Generate UUP-dump download+conversion inputs for a build+edition combination.

UUP-dump's modern API is a JSON-ish endpoint `get.php`; for stability we still
fetch the lang-selection HTML and parse the embedded file list and conversion
script URL. (The exact endpoint may change; keep this module thin so the
parse layer can be swapped.)
"""
from __future__ import annotations
from dataclasses import dataclass
import re
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
