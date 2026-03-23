"""
Striking Distance SEO Audit Tool
Streamlit entrypoint — run with: streamlit run app.py
"""

import logging
import sys
from datetime import datetime, timezone

import pandas as pd
import streamlit as st

from modules.data_loader import extract_domain, load_brand_rules, load_crawl_data, load_semrush_data
from modules.keyword_analysis import build_url_groups
from modules.serp_fetcher import fetch_serp_competitors
from modules.competitor_scraper import scrape_competitors
from modules.ai_recommendations import generate_recommendations_for_url
from modules.output import build_output_dataframe, to_excel_bytes, export_to_gsheet

logging.basicConfig(stream=sys.stdout, level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

st.set_page_config(
    page_title="Striking Distance Audit",
    page_icon="🎯",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600&family=DM+Mono:wght@400;500&display=swap');
html, body, [class*="css"] { font-family: 'DM Sans', sans-serif !important; }

.metric-card {
    background: #1a1a1f; border: 1px solid #2a2a30; border-radius: 10px;
    padding: 16px 20px; text-align: center;
}
.metric-card .value {
    font-family: 'DM Mono', monospace; font-size: 2rem;
    font-weight: 600; color: #c8ff6e; line-height: 1.1;
}
.metric-card .label {
    font-size: 0.78rem; color: #888; margin-top: 4px;
    text-transform: uppercase; letter-spacing: 0.05em;
}
.badge-optimise { background:#1a3a1a; color:#6fcf6f; padding:2px 10px; border-radius:12px; font-size:0.75rem; font-weight:600; display:inline-block; margin:2px; }
.badge-replace  { background:#3a1a1a; color:#cf6f6f; padding:2px 10px; border-radius:12px; font-size:0.75rem; font-weight:600; display:inline-block; margin:2px; }
.badge-monitor  { background:#3a3a1a; color:#cfcf6f; padding:2px 10px; border-radius:12px; font-size:0.75rem; font-weight:600; display:inline-block; margin:2px; }
.tag-block { background:#1a1a1f; border:1px solid #2a2a30; border-radius:8px; padding:12px 14px; margin-bottom:8px; font-family:'DM Mono',monospace; font-size:0.82rem; }
.tag-label { font-size:0.7rem; color:#888; text-transform:uppercase; letter-spacing:0.06em; margin-bottom:4px; }
.tag-value { color:#e8e6e0; }
.tag-value.recommended { color:#c8ff6e; }
.char-count { color:#666; font-size:0.7rem; margin-top:4px; }
.log-box { background:#0d0d0f; border:1px solid #2a2a30; border-radius:6px; padding:10px 14px; font-family:'DM Mono',monospace; font-size:0.78rem; color:#aaa; max-height:200px; overflow-y:auto; }
</style>
""", unsafe_allow_html=True)


# ── Helpers ──────────────────────────────────────────────────────────────────

def metric_card(label: str, value) -> str:
    return f'<div class="metric-card"><div class="value">{value}</div><div class="label">{label}</div></div>'


def tag_block(label: str, value: str, recommended: bool = False) -> str:
    cls = "recommended" if recommended else ""
    char_info = f'<div class="char-count">{len(value)} chars</div>' if value else ""
    safe = value.replace("<", "&lt;").replace(">", "&gt;") if value else "<em style='color:#555'>—</em>"
    return f'<div class="tag-block"><div class="tag-label">{label}</div><div class="tag-value {cls}">{safe}</div>{char_info}</div>'


# ── Sidebar ───────────────────────────────────────────────────────────────────

def render_sidebar() -> dict:
    st.sidebar.markdown("## 🎯 Striking Distance Audit")
    st.sidebar.markdown("---")

    st.sidebar.markdown("### API Keys")
    semrush_key = st.sidebar.text_input("Semrush API Key", value=st.secrets.get("SEMRUSH_KEY", ""), type="password")
    bifrost_key = st.sidebar.text_input("Bifrost / OpenAI Key", value=st.secrets.get("BIFROST_KEY", ""), type="password")
    bifrost_base_url = st.sidebar.text_input("AI Base URL", value="https://bifrost.pattern.com/v1")

    st.sidebar.markdown("---")
    st.sidebar.markdown("### Data Sources")
    upload_mode = st.sidebar.radio("Input method", ["Upload Excel file", "Google Sheets CSV URLs"], index=0)

    excel_file = None
    semrush_csv_url = crawl_csv_url = brand_csv_url = ""

    if upload_mode == "Upload Excel file":
        excel_file = st.sidebar.file_uploader("Upload Excel (.xlsx)", type=["xlsx"])
        st.sidebar.caption("Required sheet tabs: **SEMRUSH_Ranking KWs**, **Internal_All**, **BrandRules**")
    else:
        semrush_csv_url = st.sidebar.text_input("Semrush Rankings CSV URL")
        crawl_csv_url   = st.sidebar.text_input("Crawl Data CSV URL")
        brand_csv_url   = st.sidebar.text_input("BrandRules CSV URL")

    st.sidebar.markdown("---")
    st.sidebar.markdown("### Filters")
    min_pos, max_pos = st.sidebar.slider("Position range", 1, 50, (4, 20))
    min_sv   = st.sidebar.number_input("Min search volume", min_value=0, value=0, step=50)
    semrush_db = st.sidebar.selectbox("Semrush database", ["us", "au", "uk", "ca", "nz"], index=1)
    max_urls = st.sidebar.number_input("Max URLs to process (0 = all)", min_value=0, value=50, step=10)

    st.sidebar.markdown("---")
    st.sidebar.markdown("### Google Sheets Export *(optional)*")
    output_sheet_url    = st.sidebar.text_input("Output Sheet URL")
    service_account_json = st.sidebar.text_area("Service Account JSON", height=80)

    st.sidebar.markdown("---")
    run_audit = st.sidebar.button("▶ Run Audit", use_container_width=True, type="primary")

    return dict(
        semrush_key=semrush_key, bifrost_key=bifrost_key, bifrost_base_url=bifrost_base_url,
        upload_mode=upload_mode, excel_file=excel_file,
        semrush_csv_url=semrush_csv_url, crawl_csv_url=crawl_csv_url, brand_csv_url=brand_csv_url,
        min_pos=int(min_pos), max_pos=int(max_pos), min_sv=int(min_sv),
        semrush_db=semrush_db, max_urls=int(max_urls),
        output_sheet_url=output_sheet_url, service_account_json=service_account_json,
        run_audit=run_audit,
    )


# ── URL card ─────────────────────────────────────────────────────────────────

def render_url_card(item: dict):
    ug  = item["url_group"]
    ai  = item.get("ai_result") or {}
    competitors = item.get("competitors", [])
    url = ug["url"]

    opt_c = ug.get("optimise_count", 0)
    rep_c = ug.get("replace_count", 0)
    mon_c = ug.get("monitor_count", 0)
    total_kws = len(ug.get("keywords", []))

    label = f"{url}  —  {opt_c} OPTIMISE · {rep_c} REPLACE · {mon_c} MONITOR"

    with st.expander(label, expanded=False):
        badges = (
            f'<span class="badge-optimise">{opt_c} OPTIMISE</span>'
            f'<span class="badge-replace">{rep_c} REPLACE</span>'
            f'<span class="badge-monitor">{mon_c} MONITOR</span>'
            f'<span style="color:#555;font-size:0.75rem;margin-left:8px;">'
            f'{total_kws} KWs · primary: <em>{ug.get("primary_keyword","")}</em>'
            f' (pos {ug.get("primary_kw_position","?")} · SV {int(ug.get("primary_kw_sv",0))})</span>'
        )
        st.markdown(badges, unsafe_allow_html=True)

        col_curr, col_rec = st.columns(2)
        with col_curr:
            st.markdown("**Current on-page**")
            st.markdown(tag_block("Title Tag",        ug.get("title", "")),            unsafe_allow_html=True)
            st.markdown(tag_block("Meta Description", ug.get("meta_description", "")), unsafe_allow_html=True)
            st.markdown(tag_block("H1",               ug.get("h1", "")),               unsafe_allow_html=True)
        with col_rec:
            st.markdown("**AI Recommendations**")
            if ai:
                st.markdown(tag_block("Recommended Title", ai.get("recommended_title", ""), recommended=True), unsafe_allow_html=True)
                st.markdown(tag_block("Recommended Meta",  ai.get("recommended_meta",  ""), recommended=True), unsafe_allow_html=True)
                st.markdown(tag_block("Recommended H1",    ai.get("recommended_h1",    ""), recommended=True), unsafe_allow_html=True)
                if ai.get("rationale"):
                    st.caption(f"💡 {ai['rationale']}")
            else:
                st.info("No AI recommendations (API key not set or call failed).")

        kws = ug.get("keywords", [])
        if kws:
            st.markdown("**Keyword decisions**")
            ai_decisions = {d["keyword"]: d for d in ai.get("keyword_decisions", [])}
            kw_rows = []
            for kw in kws:
                ai_d = ai_decisions.get(kw["keyword"], {})
                kw_rows.append({
                    "Keyword":     kw["keyword"],
                    "Position":    kw.get("position", ""),
                    "Prev Pos":    kw.get("prev_position", ""),
                    "SV":          int(kw.get("search_volume", 0)),
                    "Health":      kw.get("health_status", ""),
                    "Score":       kw.get("opportunity_score", ""),
                    "Decision":    ai_d.get("decision", kw.get("decision", "")),
                    "Replacement": ai_d.get("replacement_keyword", "") or "",
                    "Note":        ai_d.get("note", "") or "",
                })
            st.dataframe(pd.DataFrame(kw_rows), use_container_width=True, hide_index=True)

        if competitors:
            st.markdown("**SERP competitors**")
            comp_rows = [{
                "Rank":        c.get("position", ""),
                "Domain":      c.get("domain", ""),
                "URL":         c.get("url", ""),
                "Title":       c.get("title", ""),
                "Meta":        c.get("meta_description", ""),
                "H1":          c.get("h1", ""),
                "Words":       c.get("word_count", ""),
                "Scrape Error": c.get("error", "") or "",
            } for c in competitors]
            st.dataframe(pd.DataFrame(comp_rows), use_container_width=True, hide_index=True)


# ── Metrics renderer ─────────────────────────────────────────────────────────

def render_metrics(container, results: list):
    urls_analysed   = len(results)
    kws_in_range    = sum(len(r["url_group"].get("keywords", [])) for r in results)
    optimise_total  = sum(r["url_group"].get("optimise_count", 0) for r in results)
    replace_total   = sum(r["url_group"].get("replace_count", 0) for r in results)
    recs_generated  = sum(1 for r in results if r.get("ai_result"))

    with container:
        cols = st.columns(5)
        for col, (label, val) in zip(cols, [
            ("URLs Analysed", urls_analysed),
            ("KWs in Range",  kws_in_range),
            ("Optimise",      optimise_total),
            ("Replace",       replace_total),
            ("Recs Generated", recs_generated),
        ]):
            col.markdown(metric_card(label, val), unsafe_allow_html=True)


# ── Audit runner ──────────────────────────────────────────────────────────────

def run_audit_pipeline(cfg: dict, status_ph, progress_ph, log_ph, metrics_ph, results_ph) -> list:
    logs: list[str] = []

    def log(msg: str):
        logs.append(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] {msg}")
        log_ph.markdown(
            '<div class="log-box">' + "<br>".join(logs[-40:]) + "</div>",
            unsafe_allow_html=True,
        )

    # Stage 1 — Load data
    status_ph.markdown("### Stage 1 — Loading data...")
    progress_ph.progress(5)
    log("Loading Semrush data...")

    try:
        semrush_df = load_semrush_data(
            sheet_url=cfg["semrush_csv_url"] or None,
            excel_file=cfg["excel_file"],
        )
        log(f"✓ Semrush: {len(semrush_df)} keywords")
    except Exception as exc:
        st.error(f"Failed to load Semrush data: {exc}")
        return []

    log("Loading crawl data...")
    try:
        crawl_df = load_crawl_data(
            sheet_url=cfg["crawl_csv_url"] or None,
            excel_file=cfg["excel_file"],
        )
        log(f"✓ Crawl: {len(crawl_df)} indexable pages")
    except Exception as exc:
        st.error(f"Failed to load crawl data: {exc}")
        return []

    log("Loading brand rules...")
    try:
        brand_rules = load_brand_rules(
            sheet_url=cfg["brand_csv_url"] or None,
            excel_file=cfg["excel_file"],
        )
        log(f"✓ Brand rules: {brand_rules['brand_name']}")
    except Exception as exc:
        st.warning(f"Brand rules not loaded ({exc}), using defaults.")
        from modules.data_loader import _default_brand_rules
        brand_rules = _default_brand_rules()

    progress_ph.progress(15)

    # Stage 2 — Filter & score
    status_ph.markdown("### Stage 2 — Filtering & scoring keywords...")
    log("Building URL groups...")

    url_groups = build_url_groups(
        semrush_df=semrush_df,
        crawl_df=crawl_df,
        min_pos=cfg["min_pos"],
        max_pos=cfg["max_pos"],
        min_sv=cfg["min_sv"],
        brand_name=brand_rules.get("brand_name", ""),
        max_urls=cfg["max_urls"],
    )

    if not url_groups:
        st.warning("No URLs found in striking distance range with current filters.")
        return []

    log(f"✓ {len(url_groups)} URLs to process")
    progress_ph.progress(25)

    own_domain = extract_domain(url_groups[0]["url"]) if url_groups else ""
    total = len(url_groups)

    # Initialise progressive session state
    if "processed_results" not in st.session_state:
        st.session_state.processed_results = []
    st.session_state.processed_results = []  # fresh run

    for i, ug in enumerate(url_groups):
        pct = 25 + int(70 * (i / total))
        progress_ph.progress(pct)
        url        = ug["url"]
        primary_kw = ug["primary_keyword"]

        # SERP fetch
        status_ph.markdown(f"### Processing {i+1}/{total}: SERP fetch")
        log(f"[{i+1}/{total}] '{primary_kw}'")

        raw_competitors = []
        if cfg["semrush_key"]:
            try:
                raw_competitors = fetch_serp_competitors(
                    keyword=primary_kw,
                    api_key=cfg["semrush_key"],
                    database=cfg["semrush_db"],
                    limit=10,
                    own_domain=own_domain,
                    top_n=3,
                )
                log(f"  ↳ {len(raw_competitors)} SERP competitors")
            except Exception as exc:
                log(f"  ⚠ SERP: {exc}")

        # Scrape competitors
        status_ph.markdown(f"### Processing {i+1}/{total}: Scraping competitors")
        competitors = []
        if raw_competitors:
            try:
                competitors = scrape_competitors(raw_competitors)
                ok = sum(1 for c in competitors if not c.get("error"))
                log(f"  ↳ Scraped {ok}/{len(competitors)} pages")
            except Exception as exc:
                log(f"  ⚠ Scrape: {exc}")

        # AI recommendations
        status_ph.markdown(f"### Processing {i+1}/{total}: AI recommendations")
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
                log(f"  ↳ AI {'✓' if ai_result else '⚠ no result'}")
            except Exception as exc:
                log(f"  ⚠ AI: {exc}")

        # Save result immediately — so partial results survive a crash
        result_item = {
            "url_group":   ug,
            "competitors": competitors,
            "ai_result":   ai_result,
        }
        st.session_state.processed_results.append(result_item)

        # Update metrics live
        render_metrics(metrics_ph, st.session_state.processed_results)

    progress_ph.progress(100)
    return st.session_state.processed_results


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    cfg = render_sidebar()

    st.markdown("# 🎯 Striking Distance SEO Audit")
    st.markdown(
        "Automates the full striking distance workflow: "
        "filter → score → decide → SERP → scrape → AI recommendations."
    )

    # Pre-declare all containers so they can be updated after the audit
    metrics_ph  = st.empty()
    st.markdown("---")
    status_ph   = st.empty()
    progress_ph = st.empty()
    log_ph      = st.empty()
    st.markdown("---")
    downloads_ph = st.empty()
    results_ph   = st.empty()

    # Helper: render downloads into placeholder
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
                    use_container_width=True,
                )
            with dl2:
                st.download_button(
                    "⬇ Download CSV",
                    data=out_df.to_csv(index=False).encode(),
                    file_name=f"sda_{datetime.now().strftime('%Y%m%d_%H%M')}.csv",
                    mime="text/csv",
                    use_container_width=True,
                )

    # Helper: render URL cards into placeholder
    def render_results(results):
        if not results:
            return
        with results_ph.container():
            st.markdown(f"### Results — {len(results)} URLs")
            for item in results:
                render_url_card(item)

    # Render current session state on page load / between runs
    current_results = st.session_state.get("processed_results", [])
    current_df      = st.session_state.get("output_df")

    render_metrics(metrics_ph, current_results)
    render_downloads(current_df)
    render_results(current_results)

    # ── Run audit ────────────────────────────────────────────────────────────
    if cfg["run_audit"]:
        has_data = cfg["excel_file"] or (cfg["semrush_csv_url"] and cfg["crawl_csv_url"])
        if not has_data:
            st.error("Please upload an Excel file or provide Google Sheets CSV URLs.")
            return

        # Clear previous results from display
        downloads_ph.empty()
        results_ph.empty()

        with st.spinner("Running audit..."):
            processed = run_audit_pipeline(
                cfg, status_ph, progress_ph, log_ph, metrics_ph, results_ph
            )

        if processed:
            output_df = build_output_dataframe(processed)
            st.session_state.output_df = output_df

            status_ph.success(f"✅ Audit complete — {len(processed)} URLs processed")
            progress_ph.empty()

            # Google Sheets export
            if cfg["output_sheet_url"] and cfg["service_account_json"]:
                try:
                    import json
                    ok = export_to_gsheet(output_df, cfg["output_sheet_url"], json.loads(cfg["service_account_json"]))
                    if ok:
                        st.success("Exported to Google Sheets ✓")
                    else:
                        st.warning("Google Sheets export failed — check credentials.")
                except Exception as exc:
                    st.warning(f"Google Sheets export error: {exc}")

            # Render results directly — no st.rerun() needed
            render_metrics(metrics_ph, processed)
            render_downloads(output_df)
            render_results(processed)


if __name__ == "__main__":
    main()
