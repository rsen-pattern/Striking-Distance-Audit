"""
Striking Distance SEO Audit Tool
Streamlit entrypoint — run with: streamlit run app.py
"""

import io
import logging
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import pandas as pd
import streamlit as st

from modules.data_loader import extract_domain, load_brand_rules, load_crawl_data, load_semrush_data
from modules.keyword_analysis import build_url_keyword_map
from modules.serp_fetcher import fetch_serp_competitors
from modules.competitor_scraper import scrape_competitors
from modules.ai_recommendations import generate_recommendations_for_url
from modules.output import build_output_dataframe, to_excel_bytes, export_to_gsheet

logging.basicConfig(stream=sys.stdout, level=logging.INFO,
                    format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


# ── Cached data loaders ───────────────────────────────────────────────────────
# @st.cache_data uses the argument values as the cache key.
# We pass raw bytes (not file objects) so the key is deterministic across reruns.

@st.cache_data(show_spinner=False)
def _load_semrush(file_bytes: bytes | None, sheet_url: str | None) -> pd.DataFrame:
    src = io.BytesIO(file_bytes) if file_bytes else None
    return load_semrush_data(excel_file=src, sheet_url=sheet_url)


@st.cache_data(show_spinner=False)
def _load_crawl(file_bytes: bytes | None, sheet_url: str | None) -> pd.DataFrame:
    src = io.BytesIO(file_bytes) if file_bytes else None
    return load_crawl_data(excel_file=src, sheet_url=sheet_url)


@st.cache_data(show_spinner=False)
def _load_brand_rules(file_bytes: bytes | None, sheet_url: str | None) -> dict:
    src = io.BytesIO(file_bytes) if file_bytes else None
    try:
        return load_brand_rules(excel_file=src, sheet_url=sheet_url)
    except Exception:
        from modules.data_loader import _default_brand_rules
        return _default_brand_rules()


@st.cache_data(show_spinner=False)
def _build_keyword_map(
    semrush_key_hash: str,   # unused but forces cache invalidation when data changes
    crawl_key_hash: str,
    semrush_bytes: bytes | None,
    crawl_bytes: bytes | None,
    semrush_url: str | None,
    crawl_url: str | None,
    min_sv: int,
    min_pos: int,
    max_pos: int,
    brand_name: str,
    competitor_brands: str,
    max_urls: int,
) -> list[dict]:
    semrush_df = _load_semrush(semrush_bytes, semrush_url)
    crawl_df   = _load_crawl(crawl_bytes, crawl_url)
    return build_url_keyword_map(
        semrush_df=semrush_df,
        crawl_df=crawl_df,
        min_sv=min_sv,
        min_pos=min_pos,
        max_pos=max_pos,
        brand_name=brand_name,
        competitor_brands=competitor_brands,
        max_urls=max_urls,
    )

st.set_page_config(
    page_title="Striking Distance Audit",
    page_icon="🎯",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
/* ── Pattern Brand Tokens ─────────────────────────────────────────────────
   Primary   : #009bff  #fcfcfc  #090a0f
   Secondary : #770bff  #4cc3ae  #00084d  #b3b3b3
   Charts    : #73cdff  #076ae2  #004589  #e53e51  #f56969  #ffb548  #c2e76b
───────────────────────────────────────────────────────────────────────── */

@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap');

html, body, [class*="css"] {
    font-family: 'Inter', sans-serif !important;
    background-color: #090a0f !important;
    color: #fcfcfc !important;
}

/* ── Top header bar ──────────────────────────────────────────────────── */
header[data-testid="stHeader"] {
    background: linear-gradient(90deg, #770bff 0%, #2a0880 45%, #090a0f 100%) !important;
    border-bottom: 1px solid #770bff44;
}

/* ── Sidebar ──────────────────────────────────────────────────────────── */
section[data-testid="stSidebar"] {
    background: #0a0b12 !important;
    border-right: 1px solid #1e2133 !important;
}
section[data-testid="stSidebar"] * { color: #fcfcfc !important; }
section[data-testid="stSidebar"] .stSlider > div > div > div { background: #009bff !important; }
section[data-testid="stSidebar"] hr { border-color: #1e2133 !important; }

/* ── Main surface ─────────────────────────────────────────────────────── */
.main .block-container { background: #090a0f !important; padding-top: 1.5rem; }

/* Expanders (URL cards) */
div[data-testid="stExpander"] {
    background: #0f1119 !important;
    border: 1px solid #1e2133 !important;
    border-radius: 10px !important;
    margin-bottom: 8px;
}
div[data-testid="stExpander"] summary {
    color: #009bff !important;
    font-weight: 500;
}
div[data-testid="stExpander"] summary:hover { color: #73cdff !important; }

/* Dataframes */
div[data-testid="stDataFrame"] { border-radius: 8px; overflow: hidden; }
div[data-testid="stDataFrame"] th {
    background: #0f1119 !important;
    color: #b3b3b3 !important;
    font-size: 0.75rem !important;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    border-bottom: 1px solid #1e2133 !important;
}
div[data-testid="stDataFrame"] td {
    background: #090a0f !important;
    color: #fcfcfc !important;
    font-size: 0.82rem !important;
    border-bottom: 1px solid #1a1d2a !important;
}

/* Primary button */
div[data-testid="stButton"] > button[kind="primary"] {
    background: linear-gradient(135deg, #770bff 0%, #009bff 100%) !important;
    border: none !important;
    color: #fcfcfc !important;
    font-weight: 600 !important;
    border-radius: 8px !important;
    letter-spacing: 0.02em;
}
div[data-testid="stButton"] > button[kind="primary"]:hover {
    background: linear-gradient(135deg, #8f2fff 0%, #1eaaff 100%) !important;
    box-shadow: 0 0 16px #770bff66;
}

/* Download buttons */
div[data-testid="stDownloadButton"] > button {
    background: #0f1119 !important;
    border: 1px solid #009bff !important;
    color: #009bff !important;
    border-radius: 8px !important;
    font-weight: 500 !important;
}
div[data-testid="stDownloadButton"] > button:hover {
    background: #009bff18 !important;
    box-shadow: 0 0 10px #009bff44;
}

/* Progress bar */
div[data-testid="stProgress"] > div > div { background: #009bff !important; }

/* ── Metric cards ────────────────────────────────────────────────────── */
.metric-card {
    background: #0f1119;
    border: 1px solid #1e2133;
    border-radius: 10px;
    padding: 16px 20px;
    text-align: center;
}
.metric-card .value {
    font-family: 'JetBrains Mono', monospace;
    font-size: 2rem;
    font-weight: 600;
    color: #009bff;
}
.metric-card .label {
    font-size: 0.75rem;
    color: #b3b3b3;
    margin-top: 4px;
    text-transform: uppercase;
    letter-spacing: 0.06em;
}

/* ── Decision badges ─────────────────────────────────────────────────── */
.badge-protect  { background:#00084d; color:#009bff;  border:1px solid #009bff55; padding:2px 10px; border-radius:12px; font-size:0.75rem; font-weight:600; display:inline-block; margin:2px; }
.badge-optimise { background:#062e27; color:#4cc3ae;  border:1px solid #4cc3ae55; padding:2px 10px; border-radius:12px; font-size:0.75rem; font-weight:600; display:inline-block; margin:2px; }
.badge-replace  { background:#2d0a10; color:#e53e51;  border:1px solid #e53e5155; padding:2px 10px; border-radius:12px; font-size:0.75rem; font-weight:600; display:inline-block; margin:2px; }
.badge-monitor  { background:#18191f; color:#b3b3b3;  border:1px solid #b3b3b333; padding:2px 10px; border-radius:12px; font-size:0.75rem; font-weight:600; display:inline-block; margin:2px; }
.badge-push-up  { background:#1a0533; color:#a370ff;  border:1px solid #770bff55; padding:2px 10px; border-radius:12px; font-size:0.75rem; font-weight:600; display:inline-block; margin:2px; }

/* ── Section headers ─────────────────────────────────────────────────── */
.section-header-protected {
    color: #009bff; font-size: 0.75rem; font-weight: 600;
    text-transform: uppercase; letter-spacing: 0.08em;
    border-bottom: 1px solid #009bff33; padding-bottom: 5px; margin: 14px 0 8px;
}
.section-header-striking {
    color: #770bff; font-size: 0.75rem; font-weight: 600;
    text-transform: uppercase; letter-spacing: 0.08em;
    border-bottom: 1px solid #770bff33; padding-bottom: 5px; margin: 14px 0 8px;
}
.section-header-ai {
    color: #4cc3ae; font-size: 0.75rem; font-weight: 600;
    text-transform: uppercase; letter-spacing: 0.08em;
    border-bottom: 1px solid #4cc3ae33; padding-bottom: 5px; margin: 14px 0 8px;
}

/* ── Keyword row classes ─────────────────────────────────────────────── */
.kw-protected { background:#00084d18; border:1px solid #009bff22; border-radius:6px; padding:6px 10px; margin:3px 0; font-family:'JetBrains Mono',monospace; font-size:0.78rem; }
.kw-prime     { background:#0d0d2a;   border:1px solid #770bff44; border-radius:6px; padding:6px 10px; margin:3px 0; font-family:'JetBrains Mono',monospace; font-size:0.78rem; }
.kw-page1     { background:#090a18;   border:1px solid #1e2133;   border-radius:6px; padding:6px 10px; margin:3px 0; font-family:'JetBrains Mono',monospace; font-size:0.78rem; }
.kw-page2     { background:#0c0c11;   border:1px solid #18191f;   border-radius:6px; padding:6px 10px; margin:3px 0; font-family:'JetBrains Mono',monospace; font-size:0.78rem; }

/* ── Tag blocks (on-page / recommendations) ──────────────────────────── */
.tag-block {
    background: #0f1119;
    border: 1px solid #1e2133;
    border-radius: 8px;
    padding: 12px 14px;
    margin-bottom: 8px;
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.82rem;
}
.tag-label { font-size: 0.68rem; color: #b3b3b3; text-transform: uppercase; letter-spacing: 0.07em; margin-bottom: 5px; }
.tag-value { color: #fcfcfc; }
.tag-value.recommended { color: #4cc3ae; }
.char-count { color: #555; font-size: 0.68rem; margin-top: 4px; }
.protection-ok   { color: #4cc3ae; font-size: 0.75rem; }
.protection-warn { color: #f56969; font-size: 0.75rem; font-weight: 600; }

/* ── Log / progress box ─────────────────────────────────────────────── */
.log-box {
    background: #0a0b12;
    border: 1px solid #1e2133;
    border-radius: 6px;
    padding: 10px 14px;
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.78rem;
    color: #b3b3b3;
    max-height: 200px;
    overflow-y: auto;
}

/* ── App title banner ────────────────────────────────────────────────── */
.app-header {
    background: linear-gradient(135deg, #770bff18 0%, #009bff10 100%);
    border: 1px solid #770bff44;
    border-radius: 12px;
    padding: 18px 24px;
    margin-bottom: 20px;
    display: flex;
    align-items: center;
    gap: 14px;
}
.app-header h1 { font-size: 1.5rem; font-weight: 700; color: #fcfcfc; margin: 0; }
.app-header p  { font-size: 0.82rem; color: #b3b3b3; margin: 4px 0 0; }
</style>
""", unsafe_allow_html=True)


# ── Helpers ──────────────────────────────────────────────────────────────────

def metric_card(label: str, value) -> str:
    return f'<div class="metric-card"><div class="value">{value}</div><div class="label">{label}</div></div>'


def tag_block(label: str, value: str, recommended: bool = False) -> str:
    cls  = "recommended" if recommended else ""
    safe = value.replace("<", "&lt;").replace(">", "&gt;") if value else "<em style='color:#555'>—</em>"
    char = f'<div class="char-count">{len(value)} chars</div>' if value else ""
    return f'<div class="tag-block"><div class="tag-label">{label}</div><div class="tag-value {cls}">{safe}</div>{char}</div>'


def bucket_row_class(bucket: str) -> str:
    return {"PRIME_STRIKING": "kw-prime", "PAGE1_STRIKING": "kw-page1",
            "PAGE2_STRIKING": "kw-page2"}.get(bucket, "kw-page2")


def decision_badge(decision: str) -> str:
    cls = {"OPTIMISE": "badge-optimise", "REPLACE": "badge-replace",
           "MONITOR": "badge-monitor", "PROTECT": "badge-protect"}.get(decision, "badge-monitor")
    return f'<span class="{cls}">{decision}</span>'


# ── Sidebar ───────────────────────────────────────────────────────────────────

def render_sidebar() -> dict:
    st.sidebar.markdown(
        '<div style="padding:12px 0 8px;">'
        '<span style="font-size:1.1rem;font-weight:700;color:#009bff;">⚡ Striking Distance</span>'
        '<br><span style="font-size:0.7rem;color:#b3b3b3;letter-spacing:0.08em;text-transform:uppercase;">SEO Audit · Pattern</span>'
        '</div>',
        unsafe_allow_html=True,
    )
    st.sidebar.markdown("---")

    st.sidebar.markdown("### API Keys")
    semrush_key      = st.sidebar.text_input("Semrush API Key",      value=st.secrets.get("SEMRUSH_KEY", ""),  type="password")
    bifrost_key      = st.sidebar.text_input("Bifrost / OpenAI Key", value=st.secrets.get("BIFROST_KEY", ""),  type="password")
    bifrost_base_url = st.sidebar.text_input("AI Base URL", value="https://bifrost.pattern.com/v1")

    st.sidebar.markdown("---")
    st.sidebar.markdown("### Data Sources")
    upload_mode = st.sidebar.radio("Input method", ["Upload Excel file", "Google Sheets CSV URLs"], index=0)

    excel_file = None
    semrush_csv_url = crawl_csv_url = brand_csv_url = ""

    if upload_mode == "Upload Excel file":
        excel_file = st.sidebar.file_uploader("Upload Excel (.xlsx)", type=["xlsx"])
        st.sidebar.caption("Required tabs: **SEMRUSH_Ranking KWs** · **Internal_All** · **BrandRules**")
    else:
        semrush_csv_url = st.sidebar.text_input("Semrush Rankings CSV URL")
        crawl_csv_url   = st.sidebar.text_input("Crawl Data CSV URL")
        brand_csv_url   = st.sidebar.text_input("BrandRules CSV URL")

    st.sidebar.markdown("---")
    st.sidebar.markdown("### Filters")
    min_pos, max_pos = st.sidebar.slider("Position range", 1, 50, (4, 20))
    min_sv           = st.sidebar.number_input("Min search volume", min_value=0, value=50, step=50)
    semrush_db       = st.sidebar.selectbox("Semrush database", ["us", "au", "uk", "ca", "nz"], index=1)
    max_urls         = st.sidebar.number_input("Max URLs to process (0 = all)", min_value=0, value=50, step=10)

    st.sidebar.markdown("---")
    st.sidebar.markdown("### Performance")
    concurrent_workers = st.sidebar.slider(
        "Concurrent URL workers",
        min_value=1, max_value=8, value=3,
        help="Process N URLs at the same time. Higher = faster but more API load. 3–5 recommended.",
    )
    st.sidebar.caption(
        "Each worker runs SERP fetch → scrape (parallel) → AI in sequence. "
        "Competitor scraping is always parallelised within each URL."
    )

    st.sidebar.markdown("---")
    st.sidebar.markdown("### Google Sheets Export *(optional)*")
    output_sheet_url     = st.sidebar.text_input("Output Sheet URL")
    service_account_json = st.sidebar.text_area("Service Account JSON", height=80)

    st.sidebar.markdown("---")
    run_audit = st.sidebar.button("▶ Run Audit", width="stretch", type="primary")

    return dict(
        semrush_key=semrush_key, bifrost_key=bifrost_key, bifrost_base_url=bifrost_base_url,
        upload_mode=upload_mode, excel_file=excel_file,
        semrush_csv_url=semrush_csv_url, crawl_csv_url=crawl_csv_url, brand_csv_url=brand_csv_url,
        min_pos=int(min_pos), max_pos=int(max_pos), min_sv=int(min_sv),
        semrush_db=semrush_db, max_urls=int(max_urls),
        concurrent_workers=int(concurrent_workers),
        output_sheet_url=output_sheet_url, service_account_json=service_account_json,
        run_audit=run_audit,
    )


# ── URL card ─────────────────────────────────────────────────────────────────

def render_url_card(item: dict):
    ug          = item["url_group"]
    ai          = item.get("ai_result") or {}
    competitors = item.get("competitors", [])
    url         = ug["url"]

    protected_kws = ug.get("protected_keywords", [])
    striking_kws  = ug.get("striking_keywords", [])
    opt_c  = ug.get("optimise_count", 0)
    rep_c  = ug.get("replace_count",  0)
    mon_c  = ug.get("monitor_count",  0)
    prot_c = ug.get("protected_count", 0)

    label = (
        f"{url}  —  "
        f"{'🛡 ' + str(prot_c) + ' PROTECT · ' if prot_c else ''}"
        f"{opt_c} OPTIMISE · {rep_c} REPLACE · {mon_c} MONITOR"
    )

    with st.expander(label, expanded=False):

        # Top badge row
        badges = ""
        if prot_c:
            badges += f'<span class="badge-protect">🛡 {prot_c} PROTECT</span>'
        badges += (
            f'<span class="badge-optimise">{opt_c} OPTIMISE</span>'
            f'<span class="badge-replace">{rep_c} REPLACE</span>'
            f'<span class="badge-monitor">{mon_c} MONITOR</span>'
            f'<span style="color:#555;font-size:0.75rem;margin-left:8px;">'
            f'primary: <em>{ug.get("primary_keyword","")}</em>'
            f' (pos {ug.get("primary_kw_position","?")} · SV {int(ug.get("primary_kw_sv",0))})</span>'
        )
        st.markdown(badges, unsafe_allow_html=True)

        # ── Section A: Protected keywords ─────────────────────────────────
        if protected_kws:
            st.markdown('<div class="section-header-protected">🛡 Protected Keywords — Already Ranking Top 3 (Never Overwrite)</div>', unsafe_allow_html=True)
            prot_rows = [{
                "Keyword":  k["keyword"],
                "Position": int(k.get("position", 0)),
                "SV":       int(k.get("search_volume", 0)),
                "Trend":    k.get("trend", ""),
                "Delta":    k.get("delta", ""),
            } for k in protected_kws]
            st.dataframe(pd.DataFrame(prot_rows), width="stretch", hide_index=True)

        # ── Section B: Striking distance keywords ─────────────────────────
        if striking_kws:
            st.markdown('<div class="section-header-striking">⚡ Striking Distance Keywords (Pos 4–20)</div>', unsafe_allow_html=True)

            # Group by bucket for display
            buckets_order = ["PRIME_STRIKING", "PAGE1_STRIKING", "PAGE2_STRIKING"]
            bucket_display = {
                "PRIME_STRIKING": "Pos 4–5 — One Push from Top 3",
                "PAGE1_STRIKING": "Pos 6–10 — Page 1 Below Fold",
                "PAGE2_STRIKING": "Pos 11–20 — Page 2",
            }
            for bucket in buckets_order:
                bucket_kws = [k for k in striking_kws if k.get("keyword_bucket") == bucket]
                if not bucket_kws:
                    continue
                st.caption(bucket_display[bucket])
                def _safe_int(val, default=0):
                    try:
                        return int(val) if val is not None and str(val) not in ("", "nan") else default
                    except (ValueError, TypeError):
                        return default

                rows = [{
                    "Keyword":   k["keyword"],
                    "Pos":       _safe_int(k.get("position")),
                    "Prev":      _safe_int(k.get("prev_position")),
                    "SV":        _safe_int(k.get("search_volume")),
                    "KD":        _safe_int(k.get("kd")),
                    "Trend":     k.get("trend", ""),
                    "Score":     k.get("opp_score", ""),
                    "Decision":  k.get("decision", ""),
                } for k in bucket_kws]
                st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)

        # ── Section C: AI Recommendations ────────────────────────────────
        st.markdown('<div class="section-header-ai">✨ AI Recommendations</div>', unsafe_allow_html=True)

        col_curr, col_rec = st.columns(2)
        with col_curr:
            st.markdown("**Current on-page**")
            st.markdown(tag_block("Title Tag",        ug.get("current_title", "")),  unsafe_allow_html=True)
            st.markdown(tag_block("Meta Description", ug.get("current_meta",  "")),  unsafe_allow_html=True)
            st.markdown(tag_block("H1",               ug.get("current_h1",    "")),  unsafe_allow_html=True)

            # Content signals from Screaming Frog
            h2s_list  = ug.get("h2s", [])
            wc        = ug.get("word_count")
            read_ease = ug.get("readability")
            if wc or read_ease or h2s_list:
                meta_parts = []
                if wc:        meta_parts.append(f"{int(wc)} words")
                if read_ease: meta_parts.append(f"readability {read_ease}")
                if meta_parts:
                    st.caption("  ".join(meta_parts))
            if h2s_list:
                st.markdown(tag_block("H2 Headings", " · ".join(h2s_list)), unsafe_allow_html=True)

        with col_rec:
            st.markdown("**Recommendations**")
            if ai.get("recommended_title"):
                st.markdown(tag_block("Recommended Title", ai.get("recommended_title", ""), recommended=True), unsafe_allow_html=True)
                st.markdown(tag_block("Recommended Meta",  ai.get("recommended_meta",  ""), recommended=True), unsafe_allow_html=True)
                st.markdown(tag_block("Recommended H1",    ai.get("recommended_h1",    ""), recommended=True), unsafe_allow_html=True)

                # Protection check
                check = ai.get("protection_check", "")
                if check == "ok":
                    prot_kw = ai.get("protected_kw_used") or ai.get("top_protected_keyword", "")
                    st.markdown(f'<div class="protection-ok">✓ Protected keyword preserved{": " + prot_kw if prot_kw else ""}</div>', unsafe_allow_html=True)
                elif "WARNING" in str(check):
                    st.markdown(f'<div class="protection-warn">⚠ {check}</div>', unsafe_allow_html=True)

                if ai.get("rationale"):
                    st.caption(f"💡 {ai['rationale']}")
            else:
                st.info("No AI recommendations (key not set or call failed).")

        # Keyword decisions from AI (collapsible)
        ai_decisions = ai.get("keyword_decisions", [])
        if ai_decisions:
            with st.expander("AI keyword decisions", expanded=False):
                st.dataframe(pd.DataFrame([{
                    "Keyword":     d.get("keyword", ""),
                    "Bucket":      d.get("bucket", ""),
                    "Decision":    d.get("decision", ""),
                    "Replacement": d.get("replacement_keyword", "") or "",
                    "Note":        d.get("note", "") or "",
                } for d in ai_decisions]), width="stretch", hide_index=True)

        # Competitor SERP table
        if competitors:
            with st.expander("Competitor SERP data", expanded=False):
                comp_rows = []
                for c in competitors:
                    scraped = not c.get("error") and bool(c.get("title"))
                    comp_rows.append({
                        "Rank":   c.get("position", ""),
                        "Domain": c.get("domain", ""),
                        "URL":    c.get("url", ""),
                        "Title":  c.get("title", ""),
                        "Meta":   c.get("meta_description", ""),
                        "H1":     c.get("h1", ""),
                        "Words":  c.get("word_count", ""),
                        "Source": "scraped" if scraped else f"AI-only ({(c.get('error') or '')[:40]})",
                    })
                st.dataframe(pd.DataFrame(comp_rows), width="stretch", hide_index=True)


# ── Metrics ───────────────────────────────────────────────────────────────────

def render_metrics(container, results: list):
    urls_analysed  = len(results)
    kws_in_range   = sum(len(r["url_group"].get("striking_keywords", [])) for r in results)
    protected_total = sum(r["url_group"].get("protected_count", 0) for r in results)
    optimise_total = sum(r["url_group"].get("optimise_count", 0) for r in results)
    recs_generated = sum(1 for r in results if r.get("ai_result"))

    with container:
        cols = st.columns(5)
        for col, (label, val) in zip(cols, [
            ("URLs Analysed",  urls_analysed),
            ("KWs in Range",   kws_in_range),
            ("Protected",      protected_total),
            ("Optimise",       optimise_total),
            ("Recs Generated", recs_generated),
        ]):
            col.markdown(metric_card(label, val), unsafe_allow_html=True)


# ── Per-URL worker (no st.* calls — safe to run in a thread) ─────────────────

def _process_single_url(ug: dict, cfg: dict, brand_rules: dict, own_domain: str) -> dict:
    """
    Full pipeline for one URL: SERP fetch → parallel competitor scrape → AI.
    Returns result_item dict. All exceptions are caught and logged internally.
    """
    primary_kw  = ug.get("primary_keyword", "")
    log_lines: list[str] = []

    # SERP fetch
    raw_competitors: list[dict] = []
    if cfg["semrush_key"] and primary_kw:
        try:
            raw_competitors = fetch_serp_competitors(
                keyword=primary_kw, api_key=cfg["semrush_key"],
                database=cfg["semrush_db"], limit=10,
                own_domain=own_domain, top_n=3,
            )
            log_lines.append(f"SERP: {len(raw_competitors)} competitors")
        except Exception as exc:
            log_lines.append(f"SERP failed: {exc}")

    # Scrape competitors — internally parallel (ThreadPoolExecutor per page)
    competitors: list[dict] = []
    if raw_competitors:
        try:
            competitors = scrape_competitors(raw_competitors)
            ok     = sum(1 for c in competitors if not c.get("error") and c.get("title"))
            failed = len(competitors) - ok
            note   = f" ({failed} AI-only)" if failed else ""
            log_lines.append(f"Scrape: {ok}/{len(competitors)}{note}")
        except Exception as exc:
            log_lines.append(f"Scrape failed: {exc}")

    # AI recommendations
    ai_result = None
    if cfg["bifrost_key"]:
        try:
            ai_result = generate_recommendations_for_url(
                url_group=ug,
                competitors=competitors,
                brand_rules=brand_rules,
                api_key=cfg["bifrost_key"],
                base_url=cfg["bifrost_base_url"],
            )
            if ai_result:
                check = ai_result.get("protection_check", "")
                flag  = " ⚠ PROTECT WARNING" if "WARNING" in str(check) else " ✓"
                log_lines.append(f"AI{flag}")
        except Exception as exc:
            log_lines.append(f"AI failed: {exc}")

    return {
        "url_group":   ug,
        "competitors": competitors,
        "ai_result":   ai_result,
        "_log":        log_lines,   # merged into main log by the pipeline
    }


# ── Audit pipeline ────────────────────────────────────────────────────────────

def run_audit_pipeline(cfg: dict, status_ph, progress_ph, log_ph, metrics_ph, results_ph) -> list:
    logs: list[str] = []

    def log(msg: str):
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
        logs.append(f"[{ts}] {msg}")
        log_ph.markdown(
            '<div class="log-box">' + "<br>".join(logs[-50:]) + "</div>",
            unsafe_allow_html=True,
        )

    # ── Read uploaded file bytes once (avoids seek issues with cached loaders) ──
    excel_bytes = None
    if cfg.get("excel_file"):
        cfg["excel_file"].seek(0)
        excel_bytes = cfg["excel_file"].read()

    semrush_url = cfg.get("semrush_csv_url") or None
    crawl_url   = cfg.get("crawl_csv_url")   or None
    brand_url   = cfg.get("brand_csv_url")   or None

    # ── Stage 1 — Load data (cached after first run) ─────────────────────────
    status_ph.markdown("### Stage 1 — Loading data...")
    progress_ph.progress(5)

    log("Loading Semrush data (cached)...")
    try:
        semrush_df = _load_semrush(excel_bytes, semrush_url)
        log(f"✓ Semrush: {len(semrush_df):,} keywords")
    except Exception as exc:
        st.error(f"Failed to load Semrush data: {exc}")
        return []

    log("Loading crawl data (cached)...")
    try:
        crawl_df = _load_crawl(excel_bytes, crawl_url)
        log(f"✓ Crawl: {len(crawl_df):,} indexable pages")
    except Exception as exc:
        import traceback
        tb = traceback.format_exc()
        st.error(f"Failed to load crawl data: {exc}")
        st.expander("Full traceback (share with support)").code(tb)
        logger.error("Crawl load failed:\n%s", tb)
        return []

    log("Loading brand rules (cached)...")
    brand_rules = _load_brand_rules(excel_bytes, brand_url)
    log(f"✓ Brand rules: {brand_rules['brand_name']}")

    protected_total = int((semrush_df.get("is_protected", False) == True).sum()) \
        if "is_protected" in semrush_df.columns else 0
    log(f"✓ {protected_total:,} protected keywords (pos 1–3)")
    progress_ph.progress(15)

    # ── Stage 2 — Build URL keyword map (cached) ──────────────────────────────
    status_ph.markdown("### Stage 2 — Building keyword map...")
    log("Building keyword map (cached)...")
    try:
        import hashlib
        _sh = hashlib.md5(excel_bytes or b"").hexdigest()[:8] if excel_bytes else (semrush_url or "")[:8]
        _ch = hashlib.md5(excel_bytes or b"").hexdigest()[8:16] if excel_bytes else (crawl_url or "")[:8]
        url_groups = _build_keyword_map(
            _sh, _ch,
            excel_bytes, excel_bytes,
            semrush_url, crawl_url,
            cfg["min_sv"], cfg["min_pos"], cfg["max_pos"],
            brand_rules.get("brand_name", ""),
            brand_rules.get("competitor_brands", ""),
            cfg["max_urls"],
        )
    except Exception as exc:
        st.error(f"Failed to build keyword map: {exc}")
        return []

    if not url_groups:
        st.warning("No URLs found in striking distance range with current filters.")
        return []

    prot_urls = sum(1 for g in url_groups if g["protected_count"] > 0)
    log(f"✓ {len(url_groups)} URLs to process ({prot_urls} with protected keywords)")
    progress_ph.progress(25)

    own_domain = extract_domain(url_groups[0]["url"]) if url_groups else ""
    total      = len(url_groups)
    workers    = min(cfg.get("concurrent_workers", 3), total)

    st.session_state.processed_results = []
    completed = 0

    status_ph.markdown(
        f"### Stage 3 — Processing {total} URLs  ·  {workers} concurrent workers"
    )
    log(f"Starting {workers} concurrent workers...")

    # ── Stage 3 — Concurrent URL processing ──────────────────────────────────
    with ThreadPoolExecutor(max_workers=workers) as executor:
        future_to_ug = {
            executor.submit(_process_single_url, ug, cfg, brand_rules, own_domain): ug
            for ug in url_groups
        }

        for future in as_completed(future_to_ug):
            completed += 1
            pct = 25 + int(70 * completed / total)
            progress_ph.progress(pct)

            try:
                result = future.result()
            except Exception as exc:
                ug = future_to_ug[future]
                log(f"⚠ Worker error for {ug['url'][-50:]}: {exc}")
                result = {"url_group": ug, "competitors": [], "ai_result": None, "_log": []}

            ug      = result["url_group"]
            kw_slug = (ug.get("primary_keyword") or "")[:40]
            url_end = ug["url"].split("/")[-1] or ug["url"][-30:]
            details = " · ".join(result.get("_log", []))
            prot    = f"🛡 {ug['protected_count']} " if ug.get("protected_count") else ""
            log(f"[{completed}/{total}] {prot}'{kw_slug}' {url_end}  {details}")

            st.session_state.processed_results.append(result)
            render_metrics(metrics_ph, st.session_state.processed_results)

    progress_ph.progress(100)
    return st.session_state.processed_results


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    cfg = render_sidebar()

    st.markdown(
        '<div class="app-header">'
        '<div>'
        '<h1>🎯 Striking Distance SEO Audit</h1>'
        '<p>Automates the full striking-distance workflow: '
        'filter → score → protect → SERP → scrape → AI recommendations.</p>'
        '</div>'
        '</div>',
        unsafe_allow_html=True,
    )

    metrics_ph   = st.empty()
    st.markdown("---")
    status_ph    = st.empty()
    progress_ph  = st.empty()
    log_ph       = st.empty()
    st.markdown("---")
    downloads_ph = st.empty()
    results_ph   = st.empty()

    def render_downloads(out_df):
        if out_df is None or out_df.empty:
            return
        with downloads_ph.container():
            dl1, dl2, _ = st.columns([1, 1, 4])
            with dl1:
                st.download_button(
                    "⬇ Download Excel",
                    data=to_excel_bytes(out_df),
                    file_name=f"sda_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    width="stretch",
                )
            with dl2:
                st.download_button(
                    "⬇ Download CSV",
                    data=out_df.to_csv(index=False).encode(),
                    file_name=f"sda_{datetime.now().strftime('%Y%m%d_%H%M')}.csv",
                    mime="text/csv",
                    width="stretch",
                )

    def render_results(results):
        if not results:
            return
        warnings = sum(1 for r in results
                       if "WARNING" in str((r.get("ai_result") or {}).get("protection_check", "")))
        with results_ph.container():
            hdr = f"### Results — {len(results)} URLs"
            if warnings:
                hdr += f"  ⚠ {warnings} protection warning{'s' if warnings > 1 else ''}"
            st.markdown(hdr)
            for item in results:
                render_url_card(item)

    current_results = st.session_state.get("processed_results", [])
    current_df      = st.session_state.get("output_df")

    render_metrics(metrics_ph, current_results)
    render_downloads(current_df)
    render_results(current_results)

    if cfg["run_audit"]:
        has_data = cfg["excel_file"] or (cfg["semrush_csv_url"] and cfg["crawl_csv_url"])
        if not has_data:
            st.error("Please upload an Excel file or provide Google Sheets CSV URLs.")
            return

        downloads_ph.empty()
        results_ph.empty()

        with st.spinner("Running audit..."):
            processed = run_audit_pipeline(cfg, status_ph, progress_ph, log_ph, metrics_ph, results_ph)

        if processed:
            output_df = build_output_dataframe(processed)
            st.session_state.output_df = output_df
            status_ph.success(f"✅ Audit complete — {len(processed)} URLs processed")
            progress_ph.empty()

            if cfg["output_sheet_url"] and cfg["service_account_json"]:
                try:
                    import json
                    ok = export_to_gsheet(output_df, cfg["output_sheet_url"],
                                          json.loads(cfg["service_account_json"]))
                    st.success("Exported to Google Sheets ✓") if ok else st.warning("Sheets export failed.")
                except Exception as exc:
                    st.warning(f"Sheets export error: {exc}")

            render_metrics(metrics_ph, processed)
            render_downloads(output_df)
            render_results(processed)


if __name__ == "__main__":
    main()
