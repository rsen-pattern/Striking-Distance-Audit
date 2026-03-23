"""
serp_fetcher.py
Fetch top competitor URLs from Semrush phrase_organic endpoint.
One call per URL group (primary keyword only).
"""

import logging
import time
from urllib.parse import quote_plus, urlparse

import requests

logger = logging.getLogger(__name__)

SEMRUSH_API_BASE = "https://api.semrush.com/"
_last_request_time: float = 0.0


def _rate_limit(min_interval: float = 1.0) -> None:
    """Enforce minimum interval between Semrush API calls."""
    global _last_request_time
    elapsed = time.time() - _last_request_time
    if elapsed < min_interval:
        time.sleep(min_interval - elapsed)
    _last_request_time = time.time()


def _extract_domain(url: str) -> str:
    try:
        return urlparse(url).netloc.lower().lstrip("www.")
    except Exception:
        return ""


def fetch_serp_competitors(
    keyword: str,
    api_key: str,
    database: str = "us",
    limit: int = 10,
    own_domain: str = "",
    top_n: int = 3,
) -> list[dict]:
    """
    Call Semrush phrase_organic for keyword.
    Returns list of up to top_n competitor dicts:
      {position, url, domain, title}
    Excludes own_domain. Returns [] on any error.
    """
    if not api_key:
        logger.warning("No Semrush API key provided — skipping SERP fetch.")
        return []

    _rate_limit()

    params = {
        "type": "phrase_organic",
        "key": api_key,
        "phrase": keyword,
        "database": database,
        "display_limit": limit,
        "export_columns": "Po,Ur,Dn,Tt",
        "export_decode": "1",
        "export_format": "csv",
    }

    try:
        resp = requests.get(SEMRUSH_API_BASE, params=params, timeout=30)
        resp.raise_for_status()
    except requests.RequestException as exc:
        logger.warning("Semrush API request failed for '%s': %s", keyword, exc)
        return []

    text = resp.text.strip()
    if not text or text.startswith("ERROR"):
        logger.warning("Semrush API error for '%s': %s", keyword, text[:200])
        return []

    results = _parse_semrush_csv(text, own_domain, top_n)
    logger.info("SERP fetch '%s' → %d competitors", keyword, len(results))
    return results


def _parse_semrush_csv(text: str, own_domain: str, top_n: int) -> list[dict]:
    """Parse Semrush CSV response lines into competitor dicts."""
    lines = text.splitlines()
    if len(lines) < 2:
        return []

    # First line is header: Position;Url;Domain;Title (semicolon-separated)
    competitors = []
    for line in lines[1:]:
        parts = line.split(";")
        if len(parts) < 3:
            continue
        try:
            position = int(parts[0].strip())
        except ValueError:
            continue

        url = parts[1].strip()
        domain = parts[2].strip().lstrip("www.")
        title = parts[3].strip() if len(parts) > 3 else ""

        # Exclude own domain
        if own_domain and (own_domain in domain or domain in own_domain):
            continue

        competitors.append({
            "position": position,
            "url": url,
            "domain": domain,
            "title": title,
        })

        if len(competitors) >= top_n:
            break

    return competitors
