"""
competitor_scraper.py
Scrape competitor pages for title, meta description, H1, H2s, word count.
Uses a ThreadPoolExecutor so all competitors for a URL are fetched in parallel.

Includes retry with exponential backoff for transient network failures.
"""

import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

REQUEST_TIMEOUT = 12  # seconds per page
MAX_RETRIES = 1       # 1 retry = 2 total attempts
RETRY_BACKOFF = 2.0   # seconds before retry


def scrape_page(url: str) -> dict[str, Any]:
    """
    Fetch and parse a single page with retry on transient failures.
    Returns dict:
      title, meta_description, h1, h2s (list, max 10), h3s (list, max 5),
      word_count, canonical, error (str or None)
    """
    result: dict[str, Any] = {
        "url": url,
        "title": "",
        "meta_description": "",
        "h1": "",
        "h2s": [],
        "h3s": [],
        "word_count": 0,
        "canonical": "",
        "error": None,
    }

    resp = None
    last_exc = None

    for attempt in range(1 + MAX_RETRIES):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT, allow_redirects=True)
            resp.raise_for_status()
            last_exc = None
            break
        except requests.RequestException as exc:
            last_exc = exc
            status = getattr(getattr(exc, "response", None), "status_code", None)
            # Don't retry client errors (403, 404, etc.) — only transient/server errors
            if status and 400 <= status < 500:
                break
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF * (attempt + 1))

    if last_exc or resp is None:
        result["error"] = str(last_exc or "No response")
        logger.warning("Scrape failed for %s: %s", url, last_exc)
        return result

    try:
        soup = BeautifulSoup(resp.text, "lxml")
    except Exception:
        soup = BeautifulSoup(resp.text, "html.parser")

    # Title
    title_tag = soup.find("title")
    result["title"] = title_tag.get_text(strip=True) if title_tag else ""

    # Meta description — check both name="description" and property="og:description"
    meta_tag = soup.find("meta", attrs={"name": re.compile(r"^description$", re.I)})
    if not meta_tag:
        meta_tag = soup.find("meta", attrs={"property": re.compile(r"^og:description$", re.I)})
    if meta_tag:
        result["meta_description"] = meta_tag.get("content", "").strip()

    # H1
    h1_tag = soup.find("h1")
    result["h1"] = h1_tag.get_text(strip=True) if h1_tag else ""

    # H2s (first 10)
    result["h2s"] = [h.get_text(strip=True) for h in soup.find_all("h2")][:10]

    # H3s (first 5)
    result["h3s"] = [h.get_text(strip=True) for h in soup.find_all("h3")][:5]

    # Canonical
    canonical_tag = soup.find("link", rel="canonical")
    if canonical_tag:
        result["canonical"] = canonical_tag.get("href", "").strip()

    # Word count (body text)
    body = soup.find("body")
    if body:
        # Remove script/style tags before counting
        for tag in body.find_all(["script", "style", "nav", "footer"]):
            tag.decompose()
        text = body.get_text(separator=" ")
        words = [w for w in text.split() if len(w) > 1]
        result["word_count"] = len(words)

    return result


def scrape_competitors(competitor_list: list[dict], max_workers: int = 5) -> list[dict]:
    """
    Scrape all competitor pages concurrently using a ThreadPoolExecutor.
    Preserves original order. Returns merged list with scrape results added.
    """
    if not competitor_list:
        return []

    results: dict[int, dict] = {}

    def _scrape(idx: int, comp: dict) -> tuple[int, dict]:
        url = comp.get("url", "")
        if not url:
            return idx, {**comp, "error": "No URL"}
        scrape_result = scrape_page(url)
        return idx, {**comp, **scrape_result}

    with ThreadPoolExecutor(max_workers=min(max_workers, len(competitor_list))) as pool:
        futures = {pool.submit(_scrape, i, comp): i for i, comp in enumerate(competitor_list)}
        for future in as_completed(futures):
            idx, merged = future.result()
            results[idx] = merged

    return [results[i] for i in range(len(competitor_list))]
