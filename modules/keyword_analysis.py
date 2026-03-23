"""
keyword_analysis.py
Filter, score, and make decisions on striking-distance keywords.
Build per-URL groups ready for SERP fetch + AI processing.
"""

import logging
import re
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)

# Keywords / URL path fragments that indicate navigational / branded intent
NAVIGATIONAL_PATTERNS = re.compile(
    r"\b(login|sign in|sign up|register|careers|jobs|contact|about us|sitemap|"
    r"privacy policy|terms|cookie|faq|help center|support)\b",
    re.IGNORECASE,
)

NAVIGATIONAL_URL_PATTERNS = re.compile(
    r"/(login|sign-?in|sign-?up|register|careers|jobs|contact|about|sitemap|"
    r"privacy|terms|cookie|faq|help|support)(/|$)",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Core filter
# ---------------------------------------------------------------------------


def filter_striking_distance(
    df: pd.DataFrame,
    min_pos: int = 4,
    max_pos: int = 20,
    min_sv: int = 0,
    brand_name: str = "",
) -> pd.DataFrame:
    """
    Filter to striking distance range and remove branded / navigational terms.
    Returns filtered DataFrame.
    """
    # Position range
    mask = (df["position"] >= min_pos) & (df["position"] <= max_pos)
    df = df[mask].copy()

    # Minimum search volume
    if min_sv > 0:
        df = df[df["search_volume"] >= min_sv]

    # Exclude branded keywords
    if brand_name:
        brand_words = [w.strip().lower() for w in brand_name.split() if len(w) > 2]
        for bw in brand_words:
            df = df[~df["keyword"].str.lower().str.contains(re.escape(bw), na=False)]

    # Exclude navigational keyword terms
    df = df[~df["keyword"].str.contains(NAVIGATIONAL_PATTERNS, na=False)]

    # Exclude navigational URL paths
    df = df[~df["url"].str.contains(NAVIGATIONAL_URL_PATTERNS, na=False)]

    logger.info("After filtering: %d keywords in striking distance", len(df))
    return df.reset_index(drop=True)


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


def score_keyword_health(row: pd.Series) -> tuple[str, float]:
    """
    Returns (health_status, opportunity_score).
    health_status: 'rising' | 'declining' | 'stable' | 'new'
    opportunity_score: 0–100 float
    """
    position = float(row.get("position", 10))
    prev_position = float(row.get("prev_position", position))
    sv = float(row.get("search_volume", 0))
    kd = float(row.get("kd", 50))

    # Health status
    delta = prev_position - position  # positive = moved UP (improving)
    if prev_position == 0 or pd.isna(prev_position):
        health = "new"
    elif delta > 1:
        health = "rising"
    elif delta < -1:
        health = "declining"
    else:
        health = "stable"

    # Opportunity score components
    proximity_to_p1 = ((21 - position) / 17) * 40  # closer to p1 = higher
    sv_score = min(sv / 10_000, 1.0) * 30
    kd_inverse = ((100 - kd) / 100) * 20
    momentum = min(max(delta / 5, -1), 1) * 10  # clamp to [-10, +10]

    opportunity_score = max(0.0, min(100.0, proximity_to_p1 + sv_score + kd_inverse + momentum))

    return health, round(opportunity_score, 2)


# ---------------------------------------------------------------------------
# Decision
# ---------------------------------------------------------------------------

REPLACE_SV_THRESHOLD = 200


def assign_keyword_decision(row: pd.Series, sv_threshold: int = REPLACE_SV_THRESHOLD) -> str:
    """
    Returns 'OPTIMISE' | 'REPLACE' | 'MONITOR'.
    OPTIMISE: position improving OR pos ≤10 with SV ≥ threshold
    REPLACE:  position declining AND SV < threshold
    MONITOR:  everything else
    """
    health = row.get("health_status", "stable")
    position = float(row.get("position", 15))
    sv = float(row.get("search_volume", 0))

    if health == "rising" or (position <= 10 and sv >= sv_threshold):
        return "OPTIMISE"
    if health == "declining" and sv < sv_threshold:
        return "REPLACE"
    return "MONITOR"


# ---------------------------------------------------------------------------
# URL group builder
# ---------------------------------------------------------------------------


def build_url_groups(
    semrush_df: pd.DataFrame,
    crawl_df: pd.DataFrame,
    min_pos: int = 4,
    max_pos: int = 20,
    min_sv: int = 0,
    brand_name: str = "",
    max_urls: int = 0,
) -> list[dict[str, Any]]:
    """
    Merge, filter, score, and group keywords by URL.
    Returns list of URL-group dicts sorted by total opportunity score desc.

    Each dict:
    {
        'url': str,
        'title': str,
        'meta_description': str,
        'h1': str,
        'primary_keyword': str,
        'primary_kw_position': float,
        'primary_kw_sv': float,
        'keywords': [  # all striking-distance KWs for this URL
            {keyword, position, prev_position, search_volume, kd,
             health_status, opportunity_score, decision}
        ],
        'total_opportunity': float,
        'optimise_count': int,
        'replace_count': int,
        'monitor_count': int,
    }
    """
    filtered = filter_striking_distance(semrush_df, min_pos, max_pos, min_sv, brand_name)

    if filtered.empty:
        logger.warning("No keywords remain after filtering.")
        return []

    # Score + decide each keyword
    records = []
    for _, row in filtered.iterrows():
        health, score = score_keyword_health(row)
        decision = assign_keyword_decision(
            pd.Series({**row.to_dict(), "health_status": health})
        )
        rec = row.to_dict()
        rec["health_status"] = health
        rec["opportunity_score"] = score
        rec["decision"] = decision
        records.append(rec)

    kw_df = pd.DataFrame(records)

    # Merge with crawl data
    crawl_lookup = crawl_df.set_index("url")[["title", "meta_description", "h1"]].to_dict("index")

    groups = []
    for url, grp in kw_df.groupby("url"):
        on_page = crawl_lookup.get(url, {"title": "", "meta_description": "", "h1": ""})

        kws = grp.sort_values("opportunity_score", ascending=False).to_dict("records")

        # Primary keyword selection
        optimise_kws = [k for k in kws if k["decision"] == "OPTIMISE"]
        if optimise_kws:
            primary = max(optimise_kws, key=lambda k: k["opportunity_score"])
        else:
            primary = max(kws, key=lambda k: k.get("search_volume", 0))

        total_opp = sum(k["opportunity_score"] for k in kws)
        optimise_count = sum(1 for k in kws if k["decision"] == "OPTIMISE")
        replace_count = sum(1 for k in kws if k["decision"] == "REPLACE")
        monitor_count = sum(1 for k in kws if k["decision"] == "MONITOR")

        groups.append({
            "url": url,
            "title": on_page.get("title", ""),
            "meta_description": on_page.get("meta_description", ""),
            "h1": on_page.get("h1", ""),
            "primary_keyword": primary["keyword"],
            "primary_kw_position": primary["position"],
            "primary_kw_sv": primary.get("search_volume", 0),
            "keywords": kws,
            "total_opportunity": total_opp,
            "optimise_count": optimise_count,
            "replace_count": replace_count,
            "monitor_count": monitor_count,
        })

    # Sort by total opportunity descending
    groups.sort(key=lambda g: g["total_opportunity"], reverse=True)

    if max_urls and max_urls > 0:
        groups = groups[:max_urls]

    logger.info("URL groups built: %d", len(groups))
    return groups
