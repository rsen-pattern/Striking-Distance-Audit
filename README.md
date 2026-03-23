# 🎯 Striking Distance SEO Audit Tool

A Streamlit web app that automates the full striking-distance SEO workflow — from raw data exports to AI-generated meta tag recommendations constrained by your brand rules.

## What it does

| Stage | Step | Description |
|-------|------|-------------|
| 1 | Ingest | Screaming Frog crawl + Semrush keyword rankings from Google Sheets or uploaded Excel |
| 2 | Filter + score | Keywords in positions 4–20 (configurable), scored by trend, SV, and KD |
| 3 | Decide | Per keyword: OPTIMISE · REPLACE · MONITOR |
| 4 | SERP fetch | Top 3 competitor URLs via Semrush `phrase_organic` |
| 5 | Scrape | Competitor pages scraped for title, meta, H1, H2s |
| 6 | AI generation | Optimised title, meta, H1 per URL via Bifrost/OpenAI, constrained by BrandRules |
| 7 | Export | Downloadable Excel + CSV, optional Google Sheets write-back |

## Quick start

```bash
git clone <your-repo>
cd striking-distance-audit
pip install -r requirements.txt
streamlit run app.py
```

## Input data

Upload a single Excel file (`.xlsx`) with three sheet tabs:

| Sheet tab | Source |
|-----------|--------|
| `SEMRUSH_Ranking KWs` | Semrush → Organic Research → Export |
| `Internal_All` | Screaming Frog → Export → All Internal URLs |
| `BrandRules` | Your brand rules spreadsheet |

Alternatively, paste Google Sheets CSV URLs for each tab.

## API keys

| Key | Where to get it |
|-----|----------------|
| Semrush API Key | Semrush → Management → API Keys |
| Bifrost Key | `sk-bf-...` from your Bifrost account |

For local development, create `.streamlit/secrets.toml`:

```toml
SEMRUSH_KEY = "your_semrush_key"
BIFROST_KEY = "sk-bf-..."
```

## Deploy to Streamlit Community Cloud

1. Push this repo to GitHub (can be private)
2. Go to [share.streamlit.io](https://share.streamlit.io) → New app
3. Set **Main file path**: `app.py`
4. In **Advanced Settings → Secrets**, add:
   ```toml
   SEMRUSH_KEY = "your_semrush_key"
   BIFROST_KEY = "sk-bf-..."
   ```
5. Click **Deploy** — live in ~2 minutes

## Cost estimates

| API | 50 URLs | 200 URLs |
|-----|---------|----------|
| Semrush `phrase_organic` | 50 units | 200 units |
| AI (gpt-4o-mini via Bifrost) | ~$0.05 | ~$0.20 |
| Competitor HTTP scraping | Free | Free |

## Repository structure

```
striking-distance-audit/
├── app.py                      # Main Streamlit entrypoint
├── requirements.txt
├── .streamlit/
│   └── config.toml             # Dark theme config
├── modules/
│   ├── __init__.py
│   ├── data_loader.py          # Load Semrush, crawl, brand rules
│   ├── keyword_analysis.py     # Filter, score, keep/replace decisions
│   ├── serp_fetcher.py         # Semrush phrase_organic API
│   ├── competitor_scraper.py   # HTTP scrape + BeautifulSoup
│   ├── ai_recommendations.py   # Bifrost/OpenAI prompt + JSON parse
│   └── output.py               # Build DataFrame, Excel export, Sheets write
└── README.md
```

## Keyword decision logic

| Decision | Trigger | Action |
|----------|---------|--------|
| **OPTIMISE** | Position improving OR pos ≤10 with SV ≥ 200 | Write better title/meta targeting this KW |
| **REPLACE** | Position declining AND SV < 200 | AI suggests a better replacement keyword |
| **MONITOR** | Everything else in range | Re-evaluate next cycle |

### Opportunity score formula

```
opportunity_score = (
    proximity_to_p1  * 40   # (21 - position) / 17 × 40
  + sv_score         * 30   # min(SV / 10000, 1) × 30
  + kd_inverse       * 20   # (100 - KD) / 100 × 20
  + momentum         * 10   # position delta / 5 × 10
)
```
