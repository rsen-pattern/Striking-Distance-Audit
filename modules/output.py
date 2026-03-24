"""
output.py
Build output DataFrame, export to Excel/CSV, optionally write to Google Sheets.

Excel styling uses Pattern brand colours:
  Primary   : #009bff  #fcfcfc  #090a0f
  Secondary : #770bff  #4cc3ae  #00084d  #b3b3b3
"""

import io
import logging
from datetime import datetime, timezone
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)

# Columns highlighted in Excel export (AI recommendations)
HIGHLIGHT_COLS = ["recommended_title", "recommended_meta", "recommended_h1", "ai_rationale"]

# Columns highlighted for protection warnings
PROTECTION_WARNING_COLS = ["protection_check"]

COL_WIDTHS = {
    "url":                   45,
    "primary_keyword":       30,
    "primary_kw_position":   10,
    "primary_kw_sv":         10,
    "kw_count_in_range":     10,
    "protected_count":       10,
    "optimise_count":        10,
    "replace_count":         10,
    "current_title":         50,
    "current_meta":          60,
    "current_h1":            40,
    "recommended_title":     55,
    "recommended_meta":      65,
    "recommended_h1":        45,
    "ai_rationale":          70,
    "protected_kw_used":     30,
    "protection_check":      35,
    "protected_keywords":    50,
    "keywords_to_optimise":  40,
    "keywords_to_replace":   40,
    "replacement_suggestions": 50,
    "word_count":            10,
    "readability":           12,
    "page_h2s":              55,
    "comp1_url":             45,
    "comp1_title":           50,
    "comp1_meta":            60,
    "comp2_url":             45,
    "comp2_title":           50,
    "comp2_meta":            60,
    "comp3_url":             45,
    "comp3_title":           50,
    "comp3_meta":            60,
    "scrape_success":        14,
    "run_timestamp":         22,
}


def _format_replacements(replacement_dict: dict) -> str:
    """Format replacement suggestions as clean readable lines instead of raw dict."""
    if not replacement_dict:
        return ""
    lines = [f"{kw} → {repl}" for kw, repl in replacement_dict.items()]
    return " | ".join(lines)


def build_output_dataframe(processed_results: list[dict[str, Any]]) -> pd.DataFrame:
    """
    Flatten processed URL results into a single DataFrame.
    Each item: {url_group, competitors, ai_result}
    """
    run_ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rows = []

    for item in processed_results:
        ug    = item.get("url_group", {})
        comps = item.get("competitors", [])
        ai    = item.get("ai_result") or {}

        striking = ug.get("striking_keywords", [])
        protected = ug.get("protected_keywords", [])

        optimise_kws = [k["keyword"] for k in striking if k.get("decision") == "OPTIMISE"]
        replace_kws  = [k["keyword"] for k in striking if k.get("decision") == "REPLACE"]

        # Replacement suggestions from AI decisions
        replacement_suggestions = {
            d["keyword"]: d["replacement_keyword"]
            for d in ai.get("keyword_decisions", [])
            if d.get("decision") == "REPLACE" and d.get("replacement_keyword")
        }

        # Scrape success metric
        scrape_ok   = sum(1 for c in comps if not c.get("error") and c.get("title"))
        scrape_total = len(comps)
        scrape_pct  = f"{scrape_ok}/{scrape_total}" if scrape_total else "N/A"

        def comp_field(idx: int, field: str) -> str:
            if idx < len(comps):
                return str(comps[idx].get(field, "") or "")
            return ""

        row = {
            "url":                   ug.get("url", ""),
            "primary_keyword":       ug.get("primary_keyword", ""),
            "primary_kw_position":   ug.get("primary_kw_position", ""),
            "primary_kw_sv":         ug.get("primary_kw_sv", ""),
            "kw_count_in_range":     ug.get("kw_count", len(striking)),
            "protected_count":       ug.get("protected_count", len(protected)),
            "optimise_count":        ug.get("optimise_count", len(optimise_kws)),
            "replace_count":         ug.get("replace_count", len(replace_kws)),
            # Current on-page
            "current_title":         ug.get("current_title", ""),
            "current_meta":          ug.get("current_meta", ""),
            "current_h1":            ug.get("current_h1", ""),
            # AI recommendations
            "recommended_title":     ai.get("recommended_title", ""),
            "recommended_meta":      ai.get("recommended_meta", ""),
            "recommended_h1":        ai.get("recommended_h1", ""),
            "ai_rationale":          ai.get("rationale", ""),
            # Protection fields
            "protected_kw_used":     ai.get("protected_kw_used", "") or "",
            "protection_check":      ai.get("protection_check", "") or "",
            "protected_keywords":    ", ".join(k["keyword"] for k in protected),
            # Keyword lists
            "keywords_to_optimise":  ", ".join(optimise_kws),
            "keywords_to_replace":   ", ".join(replace_kws),
            "replacement_suggestions": _format_replacements(replacement_suggestions),
            # On-page content signals
            "word_count":            ug.get("word_count") or "",
            "readability":           ug.get("readability") or "",
            "page_h2s":              " | ".join(ug.get("h2s", [])),
            # Competitors
            "comp1_url":   comp_field(0, "url"),
            "comp1_title": comp_field(0, "title"),
            "comp1_meta":  comp_field(0, "meta_description"),
            "comp2_url":   comp_field(1, "url"),
            "comp2_title": comp_field(1, "title"),
            "comp2_meta":  comp_field(1, "meta_description"),
            "comp3_url":   comp_field(2, "url"),
            "comp3_title": comp_field(2, "title"),
            "comp3_meta":  comp_field(2, "meta_description"),
            # Metadata
            "scrape_success": scrape_pct,
            "run_timestamp": run_ts,
        }
        rows.append(row)

    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows)


def to_excel_bytes(df: pd.DataFrame) -> bytes:
    output = io.BytesIO()

    with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
        df.to_excel(writer, index=False, sheet_name="Audit Results")

        wb  = writer.book
        ws  = writer.sheets["Audit Results"]

        # ── Pattern brand colours ────────────────────────────────────────
        header_fmt = wb.add_format({
            "bold": True,
            "bg_color": "#00084d",      # Secondary dark navy
            "font_color": "#009bff",    # Primary blue
            "border": 1,
            "border_color": "#1e2133",
            "text_wrap": True,
        })
        highlight_fmt = wb.add_format({
            "bg_color": "#062e27",      # Dark teal (optimise surface)
            "font_color": "#fcfcfc",    # Primary white
            "text_wrap": True,
            "valign": "top",
        })
        warning_fmt = wb.add_format({
            "bg_color": "#2d0a10",      # Dark red (replace surface)
            "font_color": "#f56969",    # Chart red
            "text_wrap": True,
            "valign": "top",
        })
        normal_fmt = wb.add_format({
            "bg_color": "#0f1119",      # Dark surface
            "font_color": "#fcfcfc",    # Primary white
            "text_wrap": True,
            "valign": "top",
        })

        col_names = list(df.columns)

        for ci, cn in enumerate(col_names):
            ws.write(0, ci, cn, header_fmt)

        for ri, row in enumerate(df.itertuples(index=False), start=1):
            for ci, cn in enumerate(col_names):
                val = getattr(row, cn, "") or ""
                if cn in HIGHLIGHT_COLS:
                    fmt = highlight_fmt
                elif cn in PROTECTION_WARNING_COLS and "WARNING" in str(val):
                    fmt = warning_fmt
                else:
                    fmt = normal_fmt
                ws.write(ri, ci, str(val), fmt)

        for ci, cn in enumerate(col_names):
            ws.set_column(ci, ci, COL_WIDTHS.get(cn, 20))

        ws.freeze_panes(1, 0)
        for ri in range(1, len(df) + 1):
            ws.set_row(ri, 60)

    output.seek(0)
    return output.read()


def export_to_gsheet(df: pd.DataFrame, sheet_url: str, credentials_json: dict) -> bool:
    try:
        import gspread
        from google.oauth2.service_account import Credentials

        scopes = [
            "https://spreadsheets.google.com/feeds",
            "https://www.googleapis.com/auth/drive",
        ]
        creds = Credentials.from_service_account_info(credentials_json, scopes=scopes)
        gc = gspread.authorize(creds)
        sh = gc.open_by_url(sheet_url)

        try:
            ws = sh.worksheet("Audit Output")
            ws.clear()
        except gspread.WorksheetNotFound:
            ws = sh.add_worksheet(title="Audit Output",
                                  rows=str(len(df) + 10),
                                  cols=str(len(df.columns) + 5))

        data = [df.columns.tolist()] + df.fillna("").astype(str).values.tolist()
        ws.update(data, "A1")
        logger.info("Exported %d rows to Google Sheets 'Audit Output'", len(df))
        return True

    except ImportError:
        logger.error("gspread not installed.")
        return False
    except Exception as exc:
        logger.error("Google Sheets export failed: %s", exc)
        return False
