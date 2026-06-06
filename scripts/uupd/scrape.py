"""Parse UUP-dump's known.php index into a list of Build records."""
from __future__ import annotations
import re
from dataclasses import dataclass, asdict
from datetime import datetime
from bs4 import BeautifulSoup
from scripts.lib.log import info


@dataclass
class Build:
    title: str
    arch: str
    uuid: str
    added_at: str  # ISO 8601 UTC

    def to_dict(self) -> dict:
        return asdict(self)


# selectlang.php?id=<uuid>
_UUID_RE = re.compile(r"selectlang\.php\?id=([0-9a-f-]{36})")
_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) UTC")


def parse_known_page(html: str) -> list[Build]:
    soup = BeautifulSoup(html, "lxml")
    out: list[Build] = []
    for row in soup.select("table tr"):
        a = row.find("a", href=_UUID_RE)
        if not a:
            continue
        m_uuid = _UUID_RE.search(a["href"])
        if not m_uuid:
            continue
        title = a.get_text(strip=True)
        cells = row.find_all("td")
        arch = cells[1].get_text(strip=True) if len(cells) > 1 else ""
        date_text = cells[2].get_text(strip=True) if len(cells) > 2 else ""
        m_date = _DATE_RE.search(date_text)
        iso_date = ""
        if m_date:
            iso_date = datetime.strptime(m_date.group(1), "%Y-%m-%d %H:%M:%S").isoformat() + "Z"
        out.append(Build(title=title, arch=arch, uuid=m_uuid.group(1), added_at=iso_date))
    info("scrape.parsed", count=len(out))
    return out


def fetch_latest() -> list[Build]:
    import requests
    r = requests.get("https://uupdump.net/known.php", timeout=30)
    r.raise_for_status()
    return parse_known_page(r.text)


if __name__ == "__main__":
    import json
    import sys
    for b in fetch_latest():
        json.dump(b.to_dict(), sys.stdout)
        print()
