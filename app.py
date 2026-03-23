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
from modules.keyword_analysis import build_url_keyword_map
from modules.serp_fetcher import fetch_serp_competitors
from modules.competitor_scraper import scrape_competitors
from modules.ai_recommendations import generate_recommendations_for_url
from modules.output import build_output_dataframe, to_excel_bytes, export_to_gsheet

logging.basicConfig(stream=sys.stdout, level=logging.INFO,
                    format="%(levelname)s %(name)s: %(message)s")
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
.metric-card .value { font-family:'DM Mono',monospace; font-size:2rem; font-weight:600; color:#c8ff6e; }
.metric-card .label { font-size:0.78rem; color:#888; margin-top:4px; text-transform:uppercase; letter-spacing:0.05em; }

.badge-optimise { background:#1a3a1a; color:#6fcf6f; padding:2px 10px; border-radius:12px; font-size:0.75rem; font-weight:600; display:inline-block; margin:2px; }
.badge-replace  { background:#3a1a1a; color:#cf6f6f; padding:2px 10px; border-radius:12px; font-size:0.75rem; font-weight:600; display:inline-block; margin:2px; }
.badge-monitor  { background:#3a3a1a; color:#cfcf6f; padding:2px 10px; border-radius:12px; font-size:0.75rem; font-weight:600; display:inline-block; margin:2px; }
.badge-protect  { background:#0d2a1a; color:#c8ff6e; padding:2px 10px; border-radius:12px; font-size:0.75rem; font-weight:600; display:inline-block; margin:2px; }
.badge-push-up  { background:#1a2a3a; color:#6fc8ff; padding:2px 10px; border-radius:12px; font-size:0.75rem; font-weight:600; display:inline-block; margin:2px; }

.section-header-protected { color:#c8ff6e; font-size:0.78rem; font-weight:600; text-transform:uppercase; letter-spacing:0.08em; border-bottom:1px solid #2a3a2a; padding-bottom:4px; margin:12px 0 8px; }
.section-header-striking  { color:#6fc8ff; font-size:0.78rem; font-weight:600; text-transform:uppercase; letter-spacing:0.08em; border-bottom:1px solid #1a2a3a; padding-bottom:4px; margin:12px 0 8px; }
.section-header-ai        { color:#cfcf6f; font-size:0.78rem; font-weight:600; text-transform:uppercase; letter-spacing:0.08em; border-bottom:1px solid #3a3a1a; padding-bottom:4px; margin:12px 0 8px; }

.kw-protected { background:#0d2a1a; border:1px solid #1a4a1a; border-radius:6px; padding:6px 10px; margin:3px 0; font-family:'DM Mono',monospace; font-size:0.78rem; }
.kw-prime     { background:#0d1a2a; border:1px solid #1a3a5a; border-radius:6px; padding:6px 10px; margin:3px 0; font-family:'DM Mono',monospace; font-size:0.78rem; }
.kw-page1     { background:#1a1a2f; border:1px solid #2a2a4a; border-radius:6px; padding:6px 10px; margin:3px 0; font-family:'DM Mono',monospace; font-size:0.78rem; }
.kw-page2     { background:#1a1a1a; border:1px solid #2a2a2a; border-radius:6px; padding:6px 10px; margin:3px 0; font-family:'DM Mono',monospace; font-size:0.78rem; }

.tag-block { background:#1a1a1f; border:1px solid #2a2a30; border-radius:8px; padding:12px 14px; margin-bottom:8px; font-family:'DM Mono',monospace; font-size:0.82rem; }
.tag-label { font-size:0.7rem; color:#888; text-transform:uppercase; letter-spacing:0.06em; margin-bottom:4px; }
.tag-value { color:#e8e6e0; }
.tag-value.recommended { color:#c8ff6e; }
.char-count { color:#666; font-size:0.7rem; margin-top:4px; }
.protection-ok   { color:#6fcf6f; font-size:0.75rem; }
.protection-warn { color:#cf6f30; font-size:0.75rem; font-weight:600; }

.log-box { background:#0d0d0f; border:1px solid #2a2a30; border-radius:6px; padding:10px 14px; font-family:'DM Mono',monospace; font-size:0.78rem; color:#aaa; max-height:200px; overflow-y:auto; }
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
    st.sidebar.markdown("## 🎯 Striking Distance Audit")
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
    st.sidebar.markdown("### Google Sheets Export *(optional)*")
    output_sheet_url     = st.sidebar.text_input("Output Sheet URL")
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
            st.dataframe(pd.DataFrame(prot_rows), use_container_width=True, hide_index=True)

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
                rows = [{
                    "Keyword":   k["keyword"],
                    "Pos":       int(k.get("position", 0)),
                    "Prev":      int(k.get("prev_position", 0)) if k.get("prev_position") else "–",
                    "SV":        int(k.get("search_volume", 0)),
                    "KD":        int(k.get("kd", 0)),
                    "Trend":     k.get("trend", ""),
                    "Score":     k.get("opp_score", ""),
                    "Decision":  k.get("decision", ""),
                } for k in bucket_kws]
                st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

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
                } for d in ai_decisions]), use_container_width=True, hide_index=True)

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
                        "Source": "scraped" if scraped else f"AI-only ({c.get('error','')[:40]})",
                    })
                st.dataframe(pd.DataFrame(comp_rows), use_container_width=True, hide_index=True)


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


# ── Audit pipeline ────────────────────────────────────────────────────────────

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

    # Count PROTECTED keywords across dataset
    protected_count = int((semrush_df["is_protected"] == True).sum()) if "is_protected" in semrush_df.columns else 0
    log(f"✓ {protected_count} protected keywords identified (pos 1–3)")
    progress_ph.progress(15)

    # Stage 2 — Build URL groups
    status_ph.markdown("### Stage 2 — Building keyword map...")
    log("Grouping keywords by URL (protected + striking)...")

    url_groups = build_url_keyword_map(
        semrush_df=semrush_df,
        crawl_df=crawl_df,
        min_sv=cfg["min_sv"],
        min_pos=cfg["min_pos"],
        max_pos=cfg["max_pos"],
        brand_name=brand_rules.get("brand_name", ""),
        max_urls=cfg["max_urls"],
    )

    if not url_groups:
        st.warning("No URLs found in striking distance range with current filters.")
        return []

    prot_urls = sum(1 for g in url_groups if g["protected_count"] > 0)
    log(f"✓ {len(url_groups)} URLs to process ({prot_urls} have protected keywords)")
    progress_ph.progress(25)

    own_domain = extract_domain(url_groups[0]["url"]) if url_groups else ""
    total = len(url_groups)

    st.session_state.processed_results = []

    for i, ug in enumerate(url_groups):
        pct = 25 + int(70 * (i / total))
        progress_ph.progress(pct)
        primary_kw = ug["primary_keyword"]

        # SERP fetch
        status_ph.markdown(f"### {i+1}/{total}: SERP fetch")
        log(f"[{i+1}/{total}] '{primary_kw}' — {ug['url'].split('/')[-1] or ug['url']}")
        if ug["protected_count"]:
            log(f"  🛡 {ug['protected_count']} protected KWs on this page")

        raw_competitors = []
        if cfg["semrush_key"] and primary_kw:
            try:
                raw_competitors = fetch_serp_competitors(
                    keyword=primary_kw, api_key=cfg["semrush_key"],
                    database=cfg["semrush_db"], limit=10,
                    own_domain=own_domain, top_n=3,
                )
                log(f"  ↳ {len(raw_competitors)} competitors")
            except Exception as exc:
                log(f"  ⚠ SERP: {exc}")

        # Scrape competitors (best-effort; failures are passed to AI as URLs)
        status_ph.markdown(f"### {i+1}/{total}: Scraping competitors")
        competitors = []
        if raw_competitors:
            try:
                competitors = scrape_competitors(raw_competitors)
                ok    = sum(1 for c in competitors if not c.get("error") and c.get("title"))
                failed = len(competitors) - ok
                msg = f"  ↳ {ok}/{len(competitors)} scraped"
                if failed:
                    msg += f" — {failed} will be fetched by AI"
                log(msg)
            except Exception as exc:
                log(f"  ⚠ Scrape: {exc}")

        # AI recommendations
        status_ph.markdown(f"### {i+1}/{total}: AI recommendations")
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
                    flag  = " ⚠ PROTECTION WARNING" if "WARNING" in str(check) else ""
                    log(f"  ↳ AI ✓{flag}")
            except Exception as exc:
                log(f"  ⚠ AI: {exc}")

        result_item = {"url_group": ug, "competitors": competitors, "ai_result": ai_result}
        st.session_state.processed_results.append(result_item)
        render_metrics(metrics_ph, st.session_state.processed_results)

    progress_ph.progress(100)
    return st.session_state.processed_results


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    cfg = render_sidebar()

    st.markdown("# 🎯 Striking Distance SEO Audit")
    st.markdown(
        "Automates the full striking distance workflow: "
        "filter → score → protect → SERP → scrape → AI recommendations."
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
