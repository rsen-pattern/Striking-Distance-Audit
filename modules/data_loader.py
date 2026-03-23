"""
data_loader.py
Load Semrush ranking data, Screaming Frog crawl data, and BrandRules
from either a Google Sheets CSV URL or an uploaded Excel file.
"""

import io
import logging
import re
from urllib.parse import urlparse

import pandas as pd
import requests

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Column name normalisers
# ---------------------------------------------------------------------------

SEMRUSH_COL_MAP = {
    "keyword": "keyword",
    "position": "position",
    "previous position": "prev_position",
    "prev position": "prev_position",
    "search volume": "search_volume",
    "volume": "search_volume",
    "keyword difficulty": "kd",
    "difficulty": "kd",
    "url": "url",
    "traffic": "traffic",
    "traffic (%)": "traffic_pct",
    "cpc": "cpc",
    "keyword intents": "keyword_intents",
    "intent": "keyword_intents",
    "position type": "position_type",
}

CRAWL_COL_MAP = {
    # Core
    "address": "url",
    "status code": "status_code",
    "indexability": "indexability",
    # Title / meta / H1
    "title 1": "title",
    "meta description 1": "meta_description",
    "h1-1": "h1",
    "h1 1": "h1",
    "title 1 length": "title_length",
    "meta description 1 length": "meta_length",
    # Subheadings
    "h2-1": "h2_1",
    "h2 1": "h2_1",
    "h2-2": "h2_2",
    "h2 2": "h2_2",
    "h2-3": "h2_3",
    "h2 3": "h2_3",
    "h3-1": "h3_1",
    "h3 1": "h3_1",
    # Body copy (Screaming Frog "Copy" tab export)
    "copy 1": "page_copy",
    "copy": "page_copy",
    "body copy": "page_copy",
    # Content metrics
    "word count": "word_count",
    "readability": "readability",
    "flesch reading ease score": "readability",
    "sentence count": "sentence_count",
}

BRAND_COL_MAP = {
    "brand name (exact)": "brand_name",
    "brand name": "brand_name",
    "tone & style": "tone_style",
    "tone and style": "tone_style",
    "title suffix": "title_suffix",
    "capitalisation": "capitalisation",
    "capitalization": "capitalisation",
    "forbidden terms": "forbidden_terms",
    "preferred vocabulary": "preferred_vocabulary",
    "audience": "audience",
    "offer phrases allowed": "offer_phrases_allowed",
    "meta length cap": "meta_length_cap",
}


def _normalise_columns(df: pd.DataFrame, col_map: dict) -> pd.DataFrame:
    """Lower-case and strip column names, then rename using col_map."""
    df.columns = [c.strip().lower() for c in df.columns]
    rename = {k: v for k, v in col_map.items() if k in df.columns}
    return df.rename(columns=rename)


def _fetch_csv_url(url: str) -> pd.DataFrame:
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    return pd.read_csv(io.StringIO(resp.text))


def _read_sheet_from_excel(excel_file, sheet_name: str) -> pd.DataFrame:
    """Read a named sheet from an Excel file object or path."""
    return pd.read_excel(excel_file, sheet_name=sheet_name, engine="openpyxl")


# ---------------------------------------------------------------------------
# Public loaders
# ---------------------------------------------------------------------------


def load_semrush_data(
    sheet_url: str | None = None,
    excel_file=None,
    sheet_name: str = "SEMRUSH_Ranking KWs",
) -> pd.DataFrame:
    """
    Returns normalised Semrush DataFrame.
    Columns: keyword, position, prev_position, search_volume, kd, url,
             traffic (optional), keyword_intents (optional).
    """
    if sheet_url:
        df = _fetch_csv_url(sheet_url)
    elif excel_file is not None:
        df = _read_sheet_from_excel(excel_file, sheet_name)
    else:
        raise ValueError("Provide either sheet_url or excel_file for Semrush data.")

    df = _normalise_columns(df, SEMRUSH_COL_MAP)

    required = ["keyword", "position", "search_volume", "url"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"Semrush data missing required columns: {missing}")

    # Coerce numerics
    for col in ["position", "prev_position", "search_volume", "kd", "traffic"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    # Fill prev_position with position if absent (treat as stable)
    if "prev_position" not in df.columns:
        df["prev_position"] = df["position"]
    else:
        df["prev_position"] = df["prev_position"].fillna(df["position"])

    if "kd" not in df.columns:
        df["kd"] = 50.0

    # Drop rows with no keyword or no URL
    df = df.dropna(subset=["keyword", "url"])
    df = df[df["keyword"].str.strip().astype(bool)]
    df = df[df["url"].str.strip().astype(bool)]

    # Normalise URLs (strip trailing slash)
    df["url"] = df["url"].str.strip().str.rstrip("/")

    # ── Derived columns ──────────────────────────────────────────────────────

    def _bucket(pos):
        if pos <= 3:    return "PROTECTED"
        elif pos <= 5:  return "PRIME_STRIKING"
        elif pos <= 10: return "PAGE1_STRIKING"
        elif pos <= 20: return "PAGE2_STRIKING"
        else:           return "DEEPER"

    df["keyword_bucket"] = df["position"].apply(_bucket)

    def _trend(row):
        pp = row.get("prev_position")
        if pd.isna(pp) or pp == 0:
            return "new"
        delta = pp - row["position"]   # positive = improved
        if delta > 1:   return "rising"
        if delta < -1:  return "declining"
        return "stable"

    df["trend"] = df.apply(_trend, axis=1)
    df["delta"] = df.apply(
        lambda r: round(r["prev_position"] - r["position"], 1)
                  if pd.notna(r.get("prev_position")) else None,
        axis=1,
    )
    df["is_protected"] = df["keyword_bucket"] == "PROTECTED"

    logger.info("Semrush data loaded: %d rows", len(df))
    return df.reset_index(drop=True)


def load_crawl_data(
    sheet_url: str | None = None,
    excel_file=None,
    sheet_name: str = "Internal_All",
) -> pd.DataFrame:
    """
    Returns normalised Screaming Frog DataFrame filtered to 200/Indexable.
    Columns: url, title, meta_description, h1, status_code, indexability.
    """
    if sheet_url:
        df = _fetch_csv_url(sheet_url)
    elif excel_file is not None:
        df = _read_sheet_from_excel(excel_file, sheet_name)
    else:
        raise ValueError("Provide either sheet_url or excel_file for crawl data.")

    df = _normalise_columns(df, CRAWL_COL_MAP)

    if "url" not in df.columns:
        raise ValueError("Crawl data missing 'Address' column.")

    # Filter to 200 + Indexable
    if "status_code" in df.columns:
        df["status_code"] = pd.to_numeric(df["status_code"], errors="coerce")
        df = df[df["status_code"] == 200]

    if "indexability" in df.columns:
        df = df[df["indexability"].str.strip().str.lower() == "indexable"]

    # Fill missing on-page columns
    text_cols = [
        "title", "meta_description", "h1",
        "h2_1", "h2_2", "h2_3", "h3_1",
        "page_copy",
    ]
    for col in text_cols:
        if col not in df.columns:
            df[col] = ""
        else:
            df[col] = df[col].fillna("").astype(str)

    numeric_cols = ["word_count", "readability", "sentence_count"]
    for col in numeric_cols:
        if col not in df.columns:
            df[col] = None
        else:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    df["url"] = df["url"].str.strip().str.rstrip("/")
    df = df.dropna(subset=["url"])
    df = df[df["url"].str.strip().astype(bool)]

    logger.info("Crawl data loaded: %d indexable rows", len(df))
    return df.reset_index(drop=True)


def load_brand_rules(
    sheet_url: str | None = None,
    excel_file=None,
    sheet_name: str = "BrandRules",
    domain: str | None = None,
) -> dict:
    """
    Returns brand rules dict for matching domain row (or first row).
    Keys: brand_name, tone_style, title_suffix, capitalisation, forbidden_terms,
          preferred_vocabulary, audience, offer_phrases_allowed, meta_length_cap.
    """
    if sheet_url:
        df = _fetch_csv_url(sheet_url)
    elif excel_file is not None:
        df = _read_sheet_from_excel(excel_file, sheet_name)
    else:
        # Return safe defaults if no brand rules provided
        return _default_brand_rules()

    df = _normalise_columns(df, BRAND_COL_MAP)

    if df.empty:
        return _default_brand_rules()

    # Try to match domain
    row = None
    if domain and "domain" in df.columns:
        matches = df[df["domain"].str.contains(domain, case=False, na=False)]
        if not matches.empty:
            row = matches.iloc[0]

    if row is None:
        row = df.iloc[0]

    rules = _default_brand_rules()
    for key in rules:
        if key in row.index and pd.notna(row[key]) and str(row[key]).strip():
            rules[key] = str(row[key]).strip()

    # meta_length_cap should be int
    try:
        rules["meta_length_cap"] = int(rules["meta_length_cap"])
    except (ValueError, TypeError):
        rules["meta_length_cap"] = 160

    logger.info("Brand rules loaded for: %s", rules.get("brand_name", "unknown"))
    return rules


def _default_brand_rules() -> dict:
    return {
        "brand_name": "Your Brand",
        "tone_style": "Professional, helpful, and clear.",
        "title_suffix": "",
        "capitalisation": "Title Case for titles, Sentence case for meta descriptions.",
        "forbidden_terms": "",
        "preferred_vocabulary": "",
        "audience": "General audience",
        "offer_phrases_allowed": "Yes",
        "meta_length_cap": 160,
    }


def extract_domain(url: str) -> str:
    """Extract bare domain (netloc) from a URL string."""
    try:
        parsed = urlparse(url)
        return parsed.netloc.lower().lstrip("www.")
    except Exception:
        return ""
