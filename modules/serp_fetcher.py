"""
serp_fetcher.py
Fetch top competitor URLs from Semrush phrase_organic endpoint.
Supports fetching for multiple keywords per URL group (primary + highest-SV).

Thread-safe rate-limiting via threading.Lock so concurrent workers
don't bypass the Semrush API interval.
"""

import logging
import threading
import time
from urllib.parse import quote_plus, urlparse

import requests

logger = logging.getLogger(__name__)

SEMRUSH_API_BASE = "https://api.semrush.com/"

# ── Thread-safe rate limiter ──────────────────────────────────────────────────

_rate_lock = threading.Lock()
_last_request_time: float = 0.0


def _rate_limit(min_interval: float = 1.0) -> None:
    """Enforce minimum interval between Semrush API calls (thread-safe)."""
    global _last_request_time
    with _rate_lock:
        elapsed = time.time() - _last_request_time
        if elapsed < min_interval:
            time.sleep(min_interval - elapsed)
        _last_request_time = time.time()


# ── Domain helpers ────────────────────────────────────────────────────────────

def _extract_domain(url: str) -> str:
    """Extract bare domain (without www.) from URL using urlparse."""
    try:
        netloc = urlparse(url).netloc.lower()
        if netloc.startswith("www."):
            netloc = netloc[4:]
        return netloc
    except Exception:
        return ""


def _is_own_domain(domain: str, own_domain: str) -> bool:
    """
    True if domain belongs to the site owner.
    Uses proper domain comparison — not substring match — to avoid
    filtering 'workshop.com' when own_domain is 'shop'.
    """
    if not own_domain:
        return False
    d = domain.lower().lstrip("www.")
    o = own_domain.lower().lstrip("www.")
    # Exact match or subdomain match (e.g. 'au.shop.com' ends with '.shop.com')
    return d == o or d.endswith("." + o)


# ── SERP fetch ────────────────────────────────────────────────────────────────

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
    if not api_key or not keyword:
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


def fetch_serp_multi_keyword(
    keywords: list[dict],
    api_key: str,
    database: str = "us",
    own_domain: str = "",
    top_n: int = 3,
) -> list[dict]:
    """
    Fetch SERP competitors for multiple keywords, de-duplicate by domain,
    and return up to top_n unique competitors.

    keywords: list of {"keyword": str, "search_volume": int} dicts,
              ordered by priority (highest-priority first).
    """
    seen_domains: set[str] = set()
    combined: list[dict] = []

    for kw_info in keywords:
        kw = kw_info.get("keyword", "")
        if not kw:
            continue
        comps = fetch_serp_competitors(
            keyword=kw, api_key=api_key, database=database,
            own_domain=own_domain, top_n=top_n * 2,  # over-fetch to allow dedup
        )
        for c in comps:
            d = c.get("domain", "").lower()
            if d not in seen_domains:
                seen_domains.add(d)
                c["source_keyword"] = kw
                combined.append(c)
            if len(combined) >= top_n:
                break
        if len(combined) >= top_n:
            break

    return combined[:top_n]


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
        domain = parts[2].strip()
        if domain.startswith("www."):
            domain = domain[4:]
        title = parts[3].strip() if len(parts) > 3 else ""

        # Exclude own domain (proper domain comparison)
        if _is_own_domain(domain, own_domain):
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
