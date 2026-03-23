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
    r"\b(?:login|sign in|sign up|register|careers|jobs|contact|about us|sitemap|"
    r"privacy policy|terms|cookie|faq|help center|support)\b",
    re.IGNORECASE,
)

NAVIGATIONAL_URL_PATTERNS = re.compile(
    r"/(?:login|sign-?in|sign-?up|register|careers|jobs|contact|about|sitemap|"
    r"privacy|terms|cookie|faq|help|support)(?:/|$)",
    re.IGNORECASE,
)

STRIKING_BUCKETS = {"PRIME_STRIKING", "PAGE1_STRIKING", "PAGE2_STRIKING"}

# ---------------------------------------------------------------------------
# Opportunity score (protected-aware)
# ---------------------------------------------------------------------------


def _opportunity_score(row: pd.Series, protected_kw_set: set) -> float:
    """
    Score a striking-distance keyword 0–100.
    Penalise by 90% if the keyword already appears in the protected set
    (it ranks top 3 — should not become the primary optimisation target).
    """
    pos   = float(row.get("position", 15))
    sv    = float(row.get("search_volume", 0))
    kd    = float(row.get("kd", 50))
    delta = float(row.get("delta") or 0)

    proximity = max(0.0, (21 - pos) / 17) * 40
    sv_score  = min(sv / 10_000, 1.0) * 30
    kd_score  = max(0.0, (100 - kd) / 100) * 20
    momentum  = min(max(delta / 5, -1.0), 1.0) * 10
    score     = proximity + sv_score + kd_score + momentum

    if row.get("keyword", "").lower() in protected_kw_set:
        score *= 0.1   # effectively removes it from primary selection

    return round(score, 1)


# ---------------------------------------------------------------------------
# Keyword decision
# ---------------------------------------------------------------------------

REPLACE_SV_THRESHOLD = 200


def assign_keyword_decision(row: pd.Series, sv_threshold: int = REPLACE_SV_THRESHOLD) -> str:
    """
    Returns 'OPTIMISE' | 'REPLACE' | 'MONITOR'.
    Uses health_status (trend) + position + SV.
    """
    health   = row.get("health_status") or row.get("trend", "stable")
    position = float(row.get("position", 15))
    sv       = float(row.get("search_volume", 0))

    if health == "rising" or (position <= 10 and sv >= sv_threshold):
        return "OPTIMISE"
    if health == "declining" and sv < sv_threshold:
        return "REPLACE"
    return "MONITOR"


# ---------------------------------------------------------------------------
# Main URL-group builder
# ---------------------------------------------------------------------------


def build_url_keyword_map(
    semrush_df: pd.DataFrame,
    crawl_df: pd.DataFrame,
    min_sv: int = 50,
    min_pos: int = 4,
    max_pos: int = 20,
    brand_name: str = "",
    max_urls: int = 0,
) -> list[dict[str, Any]]:
    """
    Build a per-URL keyword map that separates PROTECTED (pos 1–3) keywords
    from STRIKING DISTANCE (pos 4–20) keywords, prevents the tool from
    selecting a primary keyword that would overwrite an existing top-3 ranking.

    Returns list of URL-group dicts sorted by total opportunity score desc.

    Each dict:
    {
        url, primary_keyword, primary_kw_position, primary_kw_sv,
        protected_keywords   – list of pos 1–3 keyword dicts (never overwrite)
        striking_keywords    – list of pos 4–20 keyword dicts (scored + decided)
        kw_count, protected_count,
        optimise_count, replace_count, monitor_count,
        current_title, current_meta, current_h1,
        total_opportunity,
    }
    """
    df = semrush_df.copy()

    # Build crawl lookup
    crawl_cols = [c for c in ["title", "meta_description", "h1"] if c in crawl_df.columns]
    crawl_lookup = (
        crawl_df.set_index("url")[crawl_cols].to_dict("index")
        if crawl_cols else {}
    )

    groups: list[dict] = []

    for url, group in df.groupby("url"):

        # ── Protected keywords (pos 1–3) ─────────────────────────────────
        protected_df = group[group["is_protected"] == True].copy()
        protected_df = protected_df.sort_values("search_volume", ascending=False)
        protected_kw_set = set(protected_df["keyword"].str.lower().tolist())

        # ── Striking keywords (pos in [min_pos, max_pos], SV ≥ min_sv) ──
        striking_df = group[
            (group["position"] >= min_pos) &
            (group["position"] <= max_pos) &
            (group["search_volume"] >= min_sv)
        ].copy()

        # Remove navigational / branded from striking only
        striking_df = striking_df[
            ~striking_df["keyword"].str.contains(NAVIGATIONAL_PATTERNS, na=False, regex=True)
        ]
        striking_df = striking_df[
            ~striking_df["url"].str.contains(NAVIGATIONAL_URL_PATTERNS, na=False, regex=True)
        ]
        if brand_name:
            for bw in [w.strip().lower() for w in brand_name.split() if len(w) > 2]:
                striking_df = striking_df[
                    ~striking_df["keyword"].str.lower().str.contains(re.escape(bw), na=False)
                ]

        # Skip URLs with no striking keywords — nothing to optimise
        if striking_df.empty:
            continue

        # Score + decide each striking keyword
        striking_df["opp_score"] = striking_df.apply(
            lambda r: _opportunity_score(r, protected_kw_set), axis=1
        )

        striking_records: list[dict] = []
        for _, row in striking_df.iterrows():
            health = row.get("trend", "stable")
            decision = assign_keyword_decision(
                pd.Series({**row.to_dict(), "health_status": health})
            )
            rec = row.to_dict()
            rec["health_status"] = health
            rec["decision"] = decision
            striking_records.append(rec)

        striking_records.sort(key=lambda r: r.get("opp_score", 0), reverse=True)

        # Primary keyword = highest opportunity striking KW
        # (protected_kw_set penalty ensures a top-3 KW never wins primary selection)
        primary = striking_records[0] if striking_records else None

        # Crawl data for this URL
        on_page = crawl_lookup.get(url, {})

        optimise_count = sum(1 for k in striking_records if k.get("decision") == "OPTIMISE")
        replace_count  = sum(1 for k in striking_records if k.get("decision") == "REPLACE")
        monitor_count  = sum(1 for k in striking_records if k.get("decision") == "MONITOR")
        total_opp      = sum(k.get("opp_score", 0) for k in striking_records)

        groups.append({
            "url":                url,
            "primary_keyword":    primary["keyword"]         if primary else None,
            "primary_kw_position":primary["position"]        if primary else None,
            "primary_kw_sv":      primary.get("search_volume", 0) if primary else 0,
            "protected_keywords": protected_df.to_dict("records"),
            "striking_keywords":  striking_records,
            "kw_count":           len(striking_records),
            "protected_count":    len(protected_df),
            "optimise_count":     optimise_count,
            "replace_count":      replace_count,
            "monitor_count":      monitor_count,
            "current_title":      on_page.get("title", ""),
            "current_meta":       on_page.get("meta_description", ""),
            "current_h1":         on_page.get("h1", ""),
            "total_opportunity":  total_opp,
        })

    groups.sort(key=lambda g: g["total_opportunity"], reverse=True)

    if max_urls and max_urls > 0:
        groups = groups[:max_urls]

    logger.info("URL groups built: %d (with %d protected-keyword URLs)",
                len(groups),
                sum(1 for g in groups if g["protected_count"] > 0))
    return groups
