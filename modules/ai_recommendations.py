"""
ai_recommendations.py
Build prompts and call Bifrost/OpenAI to generate title, meta, H1 recommendations.
One API call per URL group.
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
MAX_TOKENS = 1500
MAX_KEYWORDS_IN_PROMPT = 25


# ---------------------------------------------------------------------------
# Prompt builders
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

OUTPUT: Return ONLY valid JSON. No markdown fences. No preamble."""


def _format_keyword_line(kw: dict) -> str:
    return (
        f'  - "{kw["keyword"]}" | pos {kw.get("position", "?")} '
        f'(prev: {kw.get("prev_position", "?")}) | '
        f'SV {int(kw.get("search_volume", 0))} | '
        f'decision: {kw.get("decision", "MONITOR")} | '
        f'health: {kw.get("health_status", "stable")}'
    )


def _format_competitor_block(idx: int, comp: dict) -> str:
    h2s = comp.get("h2s", [])
    h2_text = "\n    ".join(h2s) if h2s else "None found"
    return (
        f"  Competitor {idx} (rank {comp.get('position', idx)}): {comp.get('url', '')}\n"
        f"    Title: {comp.get('title', '')}\n"
        f"    Meta:  {comp.get('meta_description', '')}\n"
        f"    H1:    {comp.get('h1', '')}\n"
        f"    H2s:\n    {h2_text}\n"
        f"    Word count: {comp.get('word_count', 'unknown')}"
    )


def _build_user_prompt(url_group: dict, competitors: list[dict]) -> str:
    url = url_group["url"]
    primary_kw = url_group["primary_keyword"]
    current_title = url_group.get("title", "")
    current_meta = url_group.get("meta_description", "")
    current_h1 = url_group.get("h1", "")

    keywords = url_group.get("keywords", [])[:MAX_KEYWORDS_IN_PROMPT]
    kw_lines = "\n".join(_format_keyword_line(kw) for kw in keywords)

    comp_blocks = "\n\n".join(
        _format_competitor_block(i + 1, comp)
        for i, comp in enumerate(competitors)
    ) or "  No competitor data available."

    json_schema = """{
  "url": "...",
  "recommended_title": "...",
  "recommended_meta": "...",
  "recommended_h1": "...",
  "rationale": "...",
  "keyword_decisions": [
    {
      "keyword": "...",
      "decision": "OPTIMISE|REPLACE|MONITOR",
      "replacement_keyword": "..." or null,
      "note": "..."
    }
  ]
}"""

    return f"""PAGE TO OPTIMISE:
URL: {url}
Primary keyword: {primary_kw}

CURRENT ON-PAGE:
  Title: {current_title}
  Meta:  {current_meta}
  H1:    {current_h1}

KEYWORDS IN STRIKING DISTANCE (up to {MAX_KEYWORDS_IN_PROMPT}):
{kw_lines}

TOP 3 SERP COMPETITORS:
{comp_blocks}

TASK:
1. Recommend optimised title, meta description, H1.
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
    # Remove ```json ... ``` fences
    text = re.sub(r"^```(?:json)?\s*", "", text.strip(), flags=re.MULTILINE)
    text = re.sub(r"\s*```$", "", text.strip(), flags=re.MULTILINE)
    text = text.strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        # Try to extract JSON object from response
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass
        logger.warning("Failed to parse AI JSON response: %s", exc)
        return None


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
    Build prompts, call Bifrost/OpenAI, parse response.
    Returns recommendation dict or None on failure.
    """
    if not api_key:
        logger.warning("No AI API key — skipping recommendations for %s", url_group["url"])
        return None

    client = OpenAI(api_key=api_key, base_url=base_url)

    system_prompt = _build_system_prompt(brand_rules)
    user_prompt = _build_user_prompt(url_group, competitors)

    try:
        response = client.chat.completions.create(
            model=AI_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=TEMPERATURE,
            max_tokens=MAX_TOKENS,
        )
    except Exception as exc:
        logger.warning("AI API call failed for %s: %s", url_group["url"], exc)
        return None

    raw = response.choices[0].message.content or ""
    parsed = _parse_ai_response(raw)

    if parsed is None:
        logger.warning("Could not parse AI response for %s", url_group["url"])
        return None

    # Ensure required fields exist with safe defaults
    parsed.setdefault("url", url_group["url"])
    parsed.setdefault("recommended_title", "")
    parsed.setdefault("recommended_meta", "")
    parsed.setdefault("recommended_h1", "")
    parsed.setdefault("rationale", "")
    parsed.setdefault("keyword_decisions", [])

    return parsed
