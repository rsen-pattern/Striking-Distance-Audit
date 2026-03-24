"""
keyword_analysis.py
Filter, score, and make decisions on striking-distance keywords.
Build per-URL groups ready for SERP fetch + AI processing.
"""

import logging
import math
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


_LOG10_SV_MAX = math.log10(10_001)   # normalisation constant


def _opportunity_score(row: pd.Series, protected_kw_set: set) -> float:
    """
    Score a striking-distance keyword 0–100.

    SV uses a log scale so that 1,900 SV is meaningfully better than 170 SV
    (linear scaling made them look almost identical).  Proximity weight is
    reduced to 30 so SV can influence primary-keyword selection.

    Weights: proximity=30, sv=40, kd=20, momentum=10
    """
    pos   = float(row.get("position", 15))
    sv    = float(row.get("search_volume", 0))
    kd    = float(row.get("kd", 50))
    delta = float(row.get("delta") or 0)

    proximity = max(0.0, (21 - pos) / 17) * 30
    sv_score  = math.log10(max(sv, 1) + 1) / _LOG10_SV_MAX * 40
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

    Decision matrix considers position, SV, and trend together:
      OPTIMISE — keyword is worth investing in:
        - Rising trend (regardless of SV/position)
        - Page 1 (pos ≤ 10) AND SV ≥ threshold
        - Prime striking (pos 4–5) AND SV ≥ 100 (very close to top 3)
      REPLACE — keyword is losing ground with low SV:
        - Declining AND SV < threshold AND pos > 10
      MONITOR — hold position, not worth major effort:
        - Everything else (stable, unknown, or declining on page 1)
    """
    health   = row.get("health_status") or row.get("trend", "stable")
    position = float(row.get("position", 15))
    sv       = float(row.get("search_volume", 0))

    # Rising keywords are always worth optimising
    if health == "rising":
        return "OPTIMISE"
    # Page 1 with decent SV — optimise
    if position <= 10 and sv >= sv_threshold:
        return "OPTIMISE"
    # Prime striking (pos 4–5) with any meaningful SV — optimise (one push from top 3)
    if position <= 5 and sv >= 100:
        return "OPTIMISE"
    # Declining on page 2 with low SV — replace
    if health == "declining" and sv < sv_threshold and position > 10:
        return "REPLACE"
    # Declining on page 1 — still monitor (don't throw away a page 1 ranking)
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
    competitor_brands: str = "",
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

    # Build crawl lookup (all on-page fields from Screaming Frog)
    _wanted_crawl = [
        "title", "meta_description", "h1",
        "h2_1", "h2_2", "h2_3", "h3_1",
        "page_copy",
        "word_count", "readability", "sentence_count",
    ]
    crawl_cols = [c for c in _wanted_crawl if c in crawl_df.columns]
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
        # Filter own brand words
        if brand_name:
            for bw in [w.strip().lower() for w in brand_name.split() if len(w) > 2]:
                striking_df = striking_df[
                    ~striking_df["keyword"].str.lower().str.contains(re.escape(bw), na=False)
                ]
        # Filter competitor brand words (comma-separated list from BrandRules)
        if competitor_brands:
            for cb in [w.strip().lower() for w in competitor_brands.split(",") if len(w.strip()) > 2]:
                striking_df = striking_df[
                    ~striking_df["keyword"].str.lower().str.contains(re.escape(cb), na=False)
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

        # Guard: if primary is None after filtering, skip this URL
        if primary is None or not primary.get("keyword"):
            continue

        # Also identify the highest-SV striking keyword (for dual SERP fetch)
        sv_sorted = sorted(striking_records, key=lambda r: r.get("search_volume", 0), reverse=True)
        highest_sv_kw = sv_sorted[0] if sv_sorted else None

        # Crawl data for this URL
        on_page = crawl_lookup.get(str(url), {})

        optimise_count = sum(1 for k in striking_records if k.get("decision") == "OPTIMISE")
        replace_count  = sum(1 for k in striking_records if k.get("decision") == "REPLACE")
        monitor_count  = sum(1 for k in striking_records if k.get("decision") == "MONITOR")
        total_opp      = sum(k.get("opp_score", 0) for k in striking_records)

        # Collect H2s and H3s from individual SF columns into lists
        h2s = [on_page.get(f"h2_{i}", "") for i in range(1, 4)]
        h2s = [h for h in h2s if h and str(h).strip()]
        h3s = [on_page.get("h3_1", "")]
        h3s = [h for h in h3s if h and str(h).strip()]

        groups.append({
            "url":                url,
            "primary_keyword":    primary["keyword"],
            "primary_kw_position":primary["position"],
            "primary_kw_sv":      primary.get("search_volume", 0),
            # Highest-SV keyword (may differ from primary) for dual SERP fetch
            "highest_sv_keyword": highest_sv_kw.get("keyword") if highest_sv_kw else None,
            "highest_sv_kw_sv":   highest_sv_kw.get("search_volume", 0) if highest_sv_kw else 0,
            "protected_keywords": protected_df.to_dict("records"),
            "striking_keywords":  striking_records,
            "kw_count":           len(striking_records),
            "protected_count":    len(protected_df),
            "optimise_count":     optimise_count,
            "replace_count":      replace_count,
            "monitor_count":      monitor_count,
            # On-page from Screaming Frog
            "current_title":      on_page.get("title", ""),
            "current_meta":       on_page.get("meta_description", ""),
            "current_h1":         on_page.get("h1", ""),
            "h2s":                h2s,
            "h3s":                h3s,
            "word_count":         on_page.get("word_count"),
            "readability":        on_page.get("readability"),
            "page_copy":          on_page.get("page_copy", ""),
            "total_opportunity":  total_opp,
        })

    groups.sort(key=lambda g: g["total_opportunity"], reverse=True)

    if max_urls and max_urls > 0:
        groups = groups[:max_urls]

    logger.info("URL groups built: %d (with %d protected-keyword URLs)",
                len(groups),
                sum(1 for g in groups if g["protected_count"] > 0))
    return groups
