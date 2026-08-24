"""Shared HTTP helper for UUP-dump fetches.

uupdump.net sits behind a WAF that 403s default python-requests User-Agents,
and transient 5xx/429s are common during build hours. Every fetch goes
through get_with_retry() so we present a real UA and back off on failure.
"""
from __future__ import annotations

import time

import requests

USER_AGENT = "winforge/1.0 (+https://github.com/win-forge/winforge)"

# Statuses worth retrying: WAF blips, rate limits, upstream hiccups.
_RETRY_STATUSES = frozenset({403, 429, 500, 502, 503, 504})


def get_with_retry(
    url: str,
    *,
    timeout: int = 30,
    attempts: int = 3,
    backoff: float = 5.0,
) -> requests.Response:
    """GET with a proper User-Agent and linear backoff on transient failures.

    Raises RuntimeError after the final attempt with a summary of what failed.
    """
    last_err: str | None = None
    for attempt in range(1, attempts + 1):
        try:
            r = requests.get(url, timeout=timeout, headers={"User-Agent": USER_AGENT})
            if r.status_code not in _RETRY_STATUSES:
                return r
            last_err = f"HTTP {r.status_code}"
        except requests.RequestException as e:
            last_err = f"{type(e).__name__}: {e}"
        if attempt < attempts:
            wait = backoff * attempt
            print(f"[http] {last_err} on {url} — retry {attempt}/{attempts - 1} in {wait:.0f}s")
            time.sleep(wait)
    raise RuntimeError(f"GET {url} failed after {attempts} attempts (last: {last_err})")
