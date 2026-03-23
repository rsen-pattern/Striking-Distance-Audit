"""
output.py
Build output DataFrame, export to Excel/CSV, optionally write to Google Sheets.
"""

import io
import logging
from datetime import datetime, timezone
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)

# Columns highlighted green in Excel export (AI recommendations)
HIGHLIGHT_COLS = ["recommended_title", "recommended_meta", "recommended_h1", "ai_rationale"]

# Approximate column widths (characters) for xlsxwriter
COL_WIDTHS = {
    "url": 45,
    "primary_keyword": 30,
    "primary_kw_position": 10,
    "primary_kw_sv": 10,
    "kw_count_in_range": 10,
    "optimise_count": 10,
    "replace_count": 10,
    "current_title": 50,
    "current_meta": 60,
    "current_h1": 40,
    "recommended_title": 55,
    "recommended_meta": 65,
    "recommended_h1": 45,
    "ai_rationale": 70,
    "keywords_to_optimise": 40,
    "keywords_to_replace": 40,
    "replacement_suggestions": 50,
    "comp1_url": 45,
    "comp1_title": 50,
    "comp1_meta": 60,
    "comp2_url": 45,
    "comp2_title": 50,
    "comp2_meta": 60,
    "comp3_url": 45,
    "comp3_title": 50,
    "comp3_meta": 60,
    "run_timestamp": 22,
}


# ---------------------------------------------------------------------------
# Build output DataFrame
# ---------------------------------------------------------------------------


def build_output_dataframe(processed_results: list[dict[str, Any]]) -> pd.DataFrame:
    """
    Flatten list of processed URL result dicts into a single DataFrame.
    Each dict contains: url_group, competitors, ai_result, run_timestamp.
    """
    run_ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rows = []

    for item in processed_results:
        ug = item.get("url_group", {})
        comps = item.get("competitors", [])
        ai = item.get("ai_result") or {}

        kws = ug.get("keywords", [])
        optimise_kws = [k["keyword"] for k in kws if k.get("decision") == "OPTIMISE"]
        replace_kws = [k["keyword"] for k in kws if k.get("decision") == "REPLACE"]

        # Build replacement suggestions dict from AI keyword_decisions
        replacement_suggestions = {}
        for kd in ai.get("keyword_decisions", []):
            if kd.get("decision") == "REPLACE" and kd.get("replacement_keyword"):
                replacement_suggestions[kd["keyword"]] = kd["replacement_keyword"]

        def comp_field(idx: int, field: str) -> str:
            if idx < len(comps):
                return str(comps[idx].get(field, "") or "")
            return ""

        row = {
            "url": ug.get("url", ""),
            "primary_keyword": ug.get("primary_keyword", ""),
            "primary_kw_position": ug.get("primary_kw_position", ""),
            "primary_kw_sv": ug.get("primary_kw_sv", ""),
            "kw_count_in_range": len(kws),
            "optimise_count": ug.get("optimise_count", 0),
            "replace_count": ug.get("replace_count", 0),
            "current_title": ug.get("title", ""),
            "current_meta": ug.get("meta_description", ""),
            "current_h1": ug.get("h1", ""),
            "recommended_title": ai.get("recommended_title", ""),
            "recommended_meta": ai.get("recommended_meta", ""),
            "recommended_h1": ai.get("recommended_h1", ""),
            "ai_rationale": ai.get("rationale", ""),
            "keywords_to_optimise": ", ".join(optimise_kws),
            "keywords_to_replace": ", ".join(replace_kws),
            "replacement_suggestions": str(replacement_suggestions) if replacement_suggestions else "",
            "comp1_url": comp_field(0, "url"),
            "comp1_title": comp_field(0, "title"),
            "comp1_meta": comp_field(0, "meta_description"),
            "comp2_url": comp_field(1, "url"),
            "comp2_title": comp_field(1, "title"),
            "comp2_meta": comp_field(1, "meta_description"),
            "comp3_url": comp_field(2, "url"),
            "comp3_title": comp_field(2, "title"),
            "comp3_meta": comp_field(2, "meta_description"),
            "run_timestamp": run_ts,
        }
        rows.append(row)

    if not rows:
        return pd.DataFrame()

    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Excel export
# ---------------------------------------------------------------------------


def to_excel_bytes(df: pd.DataFrame) -> bytes:
    """
    Return Excel file as bytes with xlsxwriter formatting.
    Recommended columns highlighted green. Column widths auto-set.
    """
    output = io.BytesIO()

    with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
        df.to_excel(writer, index=False, sheet_name="Audit Results")

        workbook = writer.book
        worksheet = writer.sheets["Audit Results"]

        # Formats
        header_fmt = workbook.add_format({
            "bold": True,
            "bg_color": "#1a1a1f",
            "font_color": "#c8ff6e",
            "border": 1,
            "border_color": "#2a2a30",
            "text_wrap": True,
        })
        highlight_fmt = workbook.add_format({
            "bg_color": "#1a3a1a",
            "font_color": "#e8e6e0",
            "text_wrap": True,
            "valign": "top",
        })
        normal_fmt = workbook.add_format({
            "bg_color": "#1a1a1f",
            "font_color": "#e8e6e0",
            "text_wrap": True,
            "valign": "top",
        })

        col_names = list(df.columns)

        # Write header row with custom format
        for col_idx, col_name in enumerate(col_names):
            worksheet.write(0, col_idx, col_name, header_fmt)

        # Write data rows with conditional formatting
        for row_idx, row in enumerate(df.itertuples(index=False), start=1):
            for col_idx, col_name in enumerate(col_names):
                value = getattr(row, col_name, "")
                if value is None:
                    value = ""
                fmt = highlight_fmt if col_name in HIGHLIGHT_COLS else normal_fmt
                worksheet.write(row_idx, col_idx, str(value), fmt)

        # Set column widths
        for col_idx, col_name in enumerate(col_names):
            width = COL_WIDTHS.get(col_name, 20)
            worksheet.set_column(col_idx, col_idx, width)

        # Freeze first row
        worksheet.freeze_panes(1, 0)

        # Row height for data rows
        for row_idx in range(1, len(df) + 1):
            worksheet.set_row(row_idx, 60)

    output.seek(0)
    return output.read()


# ---------------------------------------------------------------------------
# Google Sheets export
# ---------------------------------------------------------------------------


def export_to_gsheet(
    df: pd.DataFrame,
    sheet_url: str,
    credentials_json: dict,
) -> bool:
    """
    Write DataFrame to 'Audit Output' tab in the given Google Sheet.
    Creates tab if it doesn't exist.
    Returns True on success, False on failure.
    """
    try:
        import gspread
        from google.oauth2.service_account import Credentials

        scopes = [
            "https://spreadsheets.google.com/feeds",
            "https://www.googleapis.com/auth/drive",
        ]
        creds = Credentials.from_service_account_info(credentials_json, scopes=scopes)
        gc = gspread.authorize(creds)

        # Open sheet by URL
        sh = gc.open_by_url(sheet_url)

        # Get or create 'Audit Output' worksheet
        try:
            ws = sh.worksheet("Audit Output")
            ws.clear()
        except gspread.WorksheetNotFound:
            ws = sh.add_worksheet(title="Audit Output", rows=str(len(df) + 10), cols=str(len(df.columns) + 5))

        # Write header + data
        data = [df.columns.tolist()] + df.fillna("").astype(str).values.tolist()
        ws.update(data, "A1")

        logger.info("Exported %d rows to Google Sheets 'Audit Output'", len(df))
        return True

    except ImportError:
        logger.error("gspread not installed — cannot export to Google Sheets.")
        return False
    except Exception as exc:
        logger.error("Google Sheets export failed: %s", exc)
        return False
