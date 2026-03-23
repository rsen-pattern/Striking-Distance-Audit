"""
ai_recommendations.py
Build prompts and call Bifrost/OpenAI to generate title, meta, H1 recommendations.
One API call per URL group.

Competitor intelligence strategy:
  - If pre-scraped data is available (competitor_scraper succeeded), include it.
  - If scraping failed (403 etc.), pass the URL directly and ask the AI to
    access it. Models with browsing capability will retrieve it; others will
    use training-data knowledge of the domain.
"""

import json
import logging
import re
from typing import Any

from openai import OpenAI

logger = logging.getLogger(__name__)

BIFROST_BASE_URL = "https://bifrost.pattern.com/v1"
AI_MODEL = "openai/gpt-4o-mini"
TEMPERATURE = 0.3
MAX_TOKENS = 2000          # increased to accommodate richer protected-KW logic
MAX_KEYWORDS_IN_PROMPT = 25

BUCKET_LABELS = {
    "PRIME_STRIKING": "pos 4–5 (one push from top 3)",
    "PAGE1_STRIKING": "pos 6–10 (page 1, below fold)",
    "PAGE2_STRIKING": "pos 11–20 (page 2)",
}


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------


def _build_system_prompt(brand_rules: dict) -> str:
    br = brand_rules
    title_suffix = br.get("title_suffix", "")
    suffix_instruction = (
        f'Always append "{title_suffix}" separated by em dash (–).'
        if title_suffix
        else "No fixed title suffix required."
    )
    return f"""You are a senior SEO copywriter working for {br.get('brand_name', 'the brand')}.

BRAND VOICE: {br.get('tone_style', 'Professional and clear.')}
AUDIENCE: {br.get('audience', 'General audience')}
TITLE SUFFIX: {suffix_instruction}
CAPITALISATION: {br.get('capitalisation', 'Title Case for titles, Sentence case for metas.')}
FORBIDDEN TERMS (never use): {br.get('forbidden_terms', 'none')}
PREFERRED VOCABULARY: {br.get('preferred_vocabulary', 'none specified')}
OFFER PHRASES: {br.get('offer_phrases_allowed', 'Yes')}

META DESCRIPTION RULES:
- Maximum {br.get('meta_length_cap', 160)} characters including spaces
- Sentence case. No ellipsis. No exclamation marks.
- Must include primary keyword naturally.

TITLE TAG RULES:
- Primary keyword near start. Apply title suffix.
- Title Case. 50–65 characters ideally.

H1 RULES:
- Title Case. Contains primary keyword. Different from title tag. Max 70 chars.

PROTECTED KEYWORD RULE (critical):
- If the page has existing top-3 keywords, the recommended title and H1 MUST
  preserve the highest-SV protected keyword. Never remove a top-3 keyword from
  the title — it represents an established ranking you must not destroy.

OUTPUT: Return ONLY valid JSON. No markdown fences. No preamble."""


# ---------------------------------------------------------------------------
# Competitor block builder
# ---------------------------------------------------------------------------


def _format_competitor_block(idx: int, comp: dict) -> str:
    """
    Build the competitor section for one URL.
    If scrape data is present, include it.
    If scraping failed, pass the URL and ask the AI to access it directly.
    """
    url    = comp.get("url", "")
    rank   = comp.get("position", idx)
    error  = comp.get("error")

    if error or not comp.get("title"):
        # Scraping failed — ask the AI to access it
        return (
            f"  Competitor {idx} (rank {rank}): {url}\n"
            f"    ⚠ Scraping failed ({error or 'no data'}). "
            f"Please access this URL directly if you have browsing capability, "
            f"or use your training knowledge of this domain/page to inform your recommendations."
        )

    h2s = comp.get("h2s", [])
    h2_text = "\n    ".join(h2s) if h2s else "none found"
    return (
        f"  Competitor {idx} (rank {rank}): {url}\n"
        f"    Title: {comp.get('title', '')}\n"
        f"    Meta:  {comp.get('meta_description', '')}\n"
        f"    H1:    {comp.get('h1', '')}\n"
        f"    H2s:   {h2_text}\n"
        f"    Words: {comp.get('word_count', 'unknown')}"
    )


# ---------------------------------------------------------------------------
# User prompt builder
# ---------------------------------------------------------------------------


PAGE_COPY_SNIPPET_CHARS = 800   # max body copy chars sent to AI


def _kw_in_copy(keyword: str, copy: str) -> bool:
    """True if keyword (or any word > 4 chars from it) appears in body copy."""
    if not copy or not keyword:
        return False
    copy_l = copy.lower()
    kw_l   = keyword.lower()
    return kw_l in copy_l or any(w in copy_l for w in kw_l.split() if len(w) > 4)


def _build_user_prompt(url_group: dict, competitors: list[dict]) -> str:
    url         = url_group["url"]
    primary_kw  = url_group.get("primary_keyword") or ""
    curr_title  = url_group.get("current_title", "")
    curr_meta   = url_group.get("current_meta", "")
    curr_h1     = url_group.get("current_h1", "")

    protected_kws  = url_group.get("protected_keywords", [])
    striking_kws   = url_group.get("striking_keywords", [])[:MAX_KEYWORDS_IN_PROMPT]

    # On-page content signals from Screaming Frog
    h2s        = url_group.get("h2s", [])
    word_count = url_group.get("word_count")
    readability = url_group.get("readability")
    page_copy  = (url_group.get("page_copy") or "").strip()

    # ── Protected keyword block ───────────────────────────────────────────
    if protected_kws:
        prot_lines = "\n".join(
            f'  - "{k["keyword"]}" | pos {k["position"]} | '
            f'SV {int(k.get("search_volume", 0)):,} | trend: {k.get("trend", "")}'
            for k in protected_kws[:5]
        )
        protected_block = f"""
PROTECTED KEYWORDS — MUST KEEP IN TITLE AND H1:
These keywords already rank in the top 3. Removing them from the title risks
destroying established rankings that took months to earn.
{prot_lines}

INSTRUCTION: The recommended title and H1 MUST contain the highest-SV protected
keyword (or a very close variant). Striking-distance keywords below should appear
as secondary terms in the meta description — NOT as the primary title target.
"""
    else:
        protected_block = """
PROTECTED KEYWORDS: None — this page has no top-3 keywords.
You have full flexibility to choose the primary keyword from the striking distance list.
"""

    # ── Striking keywords block ───────────────────────────────────────────
    kw_lines = []
    for kw in striking_kws:
        bucket = BUCKET_LABELS.get(kw.get("keyword_bucket", ""), kw.get("keyword_bucket", ""))
        kw_lines.append(
            f'  - "{kw["keyword"]}" | {bucket} | SV {int(kw.get("search_volume", 0)):,} | '
            f'KD {kw.get("kd", 0)} | trend: {kw.get("trend", "stable")} | '
            f'decision: {kw.get("decision", "MONITOR")}'
        )
    kw_block = "\n".join(kw_lines) if kw_lines else "  No striking distance keywords."

    # ── Page content block (from Screaming Frog) ─────────────────────────
    content_lines = []
    if word_count is not None:
        content_lines.append(f"  Word count:   {int(word_count)}")
    if readability is not None:
        content_lines.append(f"  Readability:  {readability} (Flesch — higher = easier)")
    if h2s:
        content_lines.append("  H2 headings:")
        content_lines.extend(f"    • {h}" for h in h2s)
    if page_copy:
        snippet = page_copy[:PAGE_COPY_SNIPPET_CHARS]
        if len(page_copy) > PAGE_COPY_SNIPPET_CHARS:
            snippet += "…"
        content_lines.append(f"  Body copy snippet:\n    {snippet}")

        # Flag which striking keywords are (not) present in body copy
        missing_from_copy = [
            kw["keyword"] for kw in striking_kws
            if not _kw_in_copy(kw["keyword"], page_copy)
        ]
        present_in_copy = [
            kw["keyword"] for kw in striking_kws
            if _kw_in_copy(kw["keyword"], page_copy)
        ]
        if present_in_copy:
            content_lines.append(
                f"  Keywords present in body copy: {', '.join(present_in_copy[:10])}"
            )
        if missing_from_copy:
            content_lines.append(
                f"  ⚠ Keywords NOT in body copy (risky to target without content support): "
                f"{', '.join(missing_from_copy[:10])}"
            )

    page_content_block = (
        "\nPAGE CONTENT (from Screaming Frog):\n" + "\n".join(content_lines)
        if content_lines
        else "\nPAGE CONTENT: Not available from crawl export."
    )

    # ── Competitor block ──────────────────────────────────────────────────
    comp_blocks = "\n\n".join(
        _format_competitor_block(i + 1, comp)
        for i, comp in enumerate(competitors)
    ) or "  No competitor data available."

    # ── JSON schema ───────────────────────────────────────────────────────
    json_schema = """{
  "url": "...",
  "recommended_title": "...",
  "recommended_meta": "...",
  "recommended_h1": "...",
  "rationale": "...",
  "protected_kw_retained": true,
  "protected_kw_used": "..." or null,
  "keyword_decisions": [
    {
      "keyword": "...",
      "bucket": "PRIME_STRIKING|PAGE1_STRIKING|PAGE2_STRIKING|PROTECTED",
      "decision": "OPTIMISE|REPLACE|MONITOR|PROTECT",
      "replacement_keyword": null,
      "note": "..."
    }
  ]
}"""

    return f"""PAGE TO OPTIMISE:
URL: {url}
Primary keyword: {primary_kw}

CURRENT ON-PAGE:
  Title: {curr_title}
  Meta:  {curr_meta}
  H1:    {curr_h1}
{page_content_block}
{protected_block}
STRIKING DISTANCE KEYWORDS TO IMPROVE (pos 4–20):
{kw_block}

TOP 3 SERP COMPETITORS:
{comp_blocks}

TASK:
1. Recommend optimised title, meta description, H1 — respecting protected keyword constraints above.
2. For each REPLACE keyword: suggest 1 better replacement.
3. Confirm or override each keyword decision.
4. One-sentence rationale for your approach.

Return this exact JSON:
{json_schema}"""


# ---------------------------------------------------------------------------
# JSON parsing
# ---------------------------------------------------------------------------


def _parse_ai_response(text: str) -> dict | None:
    """Strip markdown fences if present and parse JSON. Returns None on failure."""
    text = re.sub(r"^```(?:json)?\s*", "", text.strip(), flags=re.MULTILINE)
    text = re.sub(r"\s*```$", "", text.strip(), flags=re.MULTILINE)
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass
        logger.warning("Failed to parse AI JSON response for text starting: %s", text[:120])
        return None


# ---------------------------------------------------------------------------
# Protection validator
# ---------------------------------------------------------------------------


def validate_protection(rec: dict, url_group: dict) -> dict:
    """
    After AI parse: confirm the highest-SV protected keyword still appears
    in the recommended title or H1. Adds protection_check field.
    """
    protected_kws = url_group.get("protected_keywords", [])
    if not protected_kws:
        rec["protection_check"] = "no_protected_kws"
        return rec

    title = (rec.get("recommended_title") or "").lower()
    h1    = (rec.get("recommended_h1")    or "").lower()
    top   = sorted(protected_kws, key=lambda k: k.get("search_volume", 0), reverse=True)[0]
    kw    = top["keyword"].lower()

    # Check full phrase or any significant word (len > 4) present
    retained = (
        kw in title or kw in h1 or
        any(w in title for w in kw.split() if len(w) > 4) or
        any(w in h1    for w in kw.split() if len(w) > 4)
    )

    rec["protection_check"]    = "ok" if retained else "WARNING: protected keyword may be missing"
    rec["top_protected_keyword"] = kw

    if not retained:
        logger.warning(
            "Protection check FAILED for %s — protected KW '%s' not found in title/H1",
            rec.get("url", ""), kw,
        )
    return rec


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def generate_recommendations_for_url(
    url_group: dict,
    competitors: list[dict],
    brand_rules: dict,
    api_key: str,
    base_url: str = BIFROST_BASE_URL,
) -> dict | None:
    """
    Build prompts, call Bifrost/OpenAI, parse + validate response.
    Returns recommendation dict or None on failure.

    Competitor data: includes pre-scraped data where available; passes raw URLs
    to the AI for pages that could not be scraped (403 etc.) so the model can
    access them directly if it has browsing capability.
    """
    if not api_key:
        logger.warning("No AI API key — skipping recommendations for %s", url_group["url"])
        return None

    client = OpenAI(api_key=api_key, base_url=base_url)

    system_prompt = _build_system_prompt(brand_rules)
    user_prompt   = _build_user_prompt(url_group, competitors)

    try:
        response = client.chat.completions.create(
            model=AI_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": user_prompt},
            ],
            temperature=TEMPERATURE,
            max_tokens=MAX_TOKENS,
        )
    except Exception as exc:
        logger.warning("AI API call failed for %s: %s", url_group["url"], exc)
        return None

    raw    = response.choices[0].message.content or ""
    parsed = _parse_ai_response(raw)

    if parsed is None:
        logger.warning("Could not parse AI response for %s", url_group["url"])
        return None

    # Safe defaults
    parsed.setdefault("url",                  url_group["url"])
    parsed.setdefault("recommended_title",    "")
    parsed.setdefault("recommended_meta",     "")
    parsed.setdefault("recommended_h1",       "")
    parsed.setdefault("rationale",            "")
    parsed.setdefault("protected_kw_retained", None)
    parsed.setdefault("protected_kw_used",    None)
    parsed.setdefault("keyword_decisions",    [])

    # Validate protected keyword was respected
    parsed = validate_protection(parsed, url_group)

    return parsed
