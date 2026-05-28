# GA4 Product Funnel Analysis
### Portfolio Project · Product Analyst · Behavioural Analytics & AI-Augmented Insights

End-to-end product analytics project built on the Google Analytics 4 public dataset in BigQuery. Covers event instrumentation understanding, session-scoped funnel analysis, cohort retention, revenue metrics, device segmentation, time-to-convert, and an AI-generated product brief — fully automated via a parameterised Python pipeline that outputs a self-serve interactive dashboard.

**Dataset:** [`bigquery-public-data.ga4_obfuscated_sample_ecommerce`](https://console.cloud.google.com/bigquery?p=bigquery-public-data&d=ga4_obfuscated_sample_ecommerce) · Google Merchandise Store (store.google.com) · Nov 2020 – Jan 2021

---

## Skills demonstrated — mapped to a product analytics role

| JD Responsibility | What this project demonstrates |
|---|---|
| **Instrument & Track** — event-based behavioural data | Deep work with raw GA4 event stream: `UNNEST(event_params)`, session-ID extraction, event deduplication, sequential timestamp validation |
| **Generate Product Insights** — funnels, retention, segmentation | 5-step session-scoped funnel · weekly cohort retention heatmap · device segmentation · shoppers funnel (intent-qualified users) |
| **Measure Impact** — reporting outcomes with rigour | Quantified bottlenecks (79% drop before product view) · opportunity sizing (~641 extra orders from 5pp funnel lift) · cohort benchmarking vs industry norms |
| **Build Dashboards & Data Apps** — self-serve, decision-ready | Standalone `dashboard.html` (Plotly) — no server, no login, opens in any browser · parameterised `generate_dashboard.py` refreshes everything by changing two variables |
| **Contribute to Product Context Layer** — metric definitions, documentation | B2B SaaS translation table mapping e-commerce events to SaaS equivalents · methodology section documenting session grain, sequential constraint logic, and BigQuery SQL patterns |
| **Leverage AI to Accelerate Insight** — LLMs, automated insight deliverables | Claude API generates a structured product brief (exec summary, hypotheses, recommendations) from structured analysis context · `generate_dashboard.py` automates the full pipeline end-to-end |
| **Partner Across Data & Product** — translate findings into narratives | Executive summary · prioritised findings grid · recommendations ranked by impact · non-technical cohort retention explanation |

---

## Project structure

| File | Description |
|---|---|
| `ga4_funnel_analysis.ipynb` | Main analysis notebook — all SQL, charts, and commentary across 10 sections |
| `generate_dashboard.py` | Runs every BigQuery query and produces a fully refreshed `dashboard.html` — change two variables for any date range or GA4 export |
| `dashboard.html` | Self-contained interactive dashboard — open in any browser, zero runtime dependencies |
| `product_brief_ga4_funnel.md` | Pre-generated AI product brief (committed so dashboard works without an API key) |

---

## Analysis — what was built and why

### 1 · Session-scoped funnel
**Funnel:** `session_start → view_item → add_to_cart → begin_checkout → purchase`

Each session is counted at a step only if it completed all prior steps *in order within the same session* — matching GA4 UI funnel logic and eliminating the inflation caused by carrying past-purchase behaviour into new sessions. Grain: `(user_pseudo_id, ga_session_id)`. Session IDs extracted from the nested `event_params` array via `UNNEST`.

This is the same methodology used in any product analytics tool (Amplitude, Mixpanel, Heap) for conversion funnel analysis — the SQL pattern is directly transferable to any event-based dataset.

**Key finding:** 79% of sessions never reach a product page. The top-of-funnel is the bottleneck, not checkout.

### 2 · Revenue layer
Total revenue, average order value ($79), and median order value ($55) for converting sessions. The mean/median gap surfaces revenue concentration from high-value orders and informs opportunity sizing.

### 3 · Device segmentation
Same funnel split by `device.category`. Purchase conversion rates differ by only 0.1pp across devices — confirming device-specific checkout optimisation is not the lever. A finding that redirects prioritisation effort correctly.

### 4 · Time-to-convert
Median time between each funnel step using `APPROX_QUANTILES` on microsecond-precision event timestamps, restricted to full-funnel sessions. Reveals that converting users move quickly (under 20 minutes end-to-end) — ruling out cart-abandonment recovery as a priority investment.

### 5 · Shoppers funnel (intent-qualified)
Alternative funnel starting at `view_item` — isolating sessions with demonstrated product intent. Separating this from the full-session funnel reveals the true add-to-cart rate among engaged users (19.8%) and precisely sizes the product discovery problem vs. the product page problem.

### 6 · Weekly cohort retention
Users grouped by week of first session, tracked week-over-week. Week-1 retention averages 4.4% across cohorts — well below the ~20% e-commerce benchmark. The earliest cohort (Oct 26) shows 11% — approximately double — attributed to a more organically engaged pre-holiday audience before broad acquisition campaigns began. Heatmap visualisation with adjusted colour scale (0–12%) to make within-range differences legible.

Analogous to D7/D30 retention in a SaaS context — the same query pattern applies directly to a product event stream.

### 7 · AI product brief + pipeline automation
Structured analysis output (funnel data, revenue metrics, cohort results) is passed as context to Claude (Anthropic API) to generate a product brief covering: executive summary, key findings, hypotheses ranked by confidence, recommended actions, and metrics to track next.

The full pipeline (`generate_dashboard.py`) is parameterised: change `DATE_START`, `DATE_END`, and `DATASET` to point at any GA4 BigQuery export and regenerate the entire dashboard — queries, charts, AI brief, and all — with a single command. This demonstrates AI-augmented analysis beyond one-off chart generation: a repeatable, automated insight delivery workflow.

---

## Key findings

- **79% of sessions never reach a product page** — the dominant funnel loss, upstream of everything else; 641 additional orders possible from a 5pp improvement in discovery rate
- **Once users view a product, checkout rates are healthy** — the problem is getting users there, not the checkout flow itself
- **Median time-to-purchase under 20 minutes** for converting sessions — low deliberation friction; cart-abandonment recovery is lower priority than discovery
- **Week-1 cohort retention at 4.4%** — below benchmark; early organic cohort at 11% suggests the site has retention potential when audience quality is high
- **Device type explains less than 0.1pp of conversion variance** — mobile checkout UX is not the lever; effort should go to discovery and lifecycle, not device-specific fixes

---

## Hypotheses and next analytical steps

Each finding was translated into a testable hypothesis and recommended action:

1. **[High confidence]** Homepage fails to surface products — test a category-forward layout; measure `view_item` rate (target: 21% → 35%+)
2. **[Medium confidence]** Product pages lack trust/social proof signals — test review counts and scarcity indicators; measure `add_to_cart` rate from `view_item` sessions
3. **[Low confidence]** Traffic mix is seasonally skewed — segment funnel by `traffic_source.medium` to separate paid vs organic intent before attributing the top-of-funnel drop to navigation design

---

## SQL patterns used

| Pattern | Purpose |
|---|---|
| `UNNEST(event_params)` scalar subquery | Extract `ga_session_id` from the nested parameter array |
| Sequential `COUNTIF` chain with timestamp constraints | Enforce funnel step ordering within a session |
| `APPROX_QUANTILES(value, 100)[OFFSET(50)]` | Efficient median computation for time-to-convert |
| `DATE_TRUNC(date, WEEK(MONDAY))` | Weekly cohort bucketing |
| `_TABLE_SUFFIX BETWEEN` | Partition pruning — controls BigQuery scan costs |
| CTE chaining (`base → session_events → pivoted`) | Modular, readable query structure analogous to dbt model layering |

All SQL is **BigQuery Standard SQL**, runs against the public GA4 schema, and transfers directly to any GA4 BigQuery export.

---

## B2B SaaS / HR platform translation

The methodology applies directly to a product like Personio. The event names change; the analytical patterns do not:

| GA4 e-commerce event | Personio / HR SaaS equivalent | Analytical metric |
|---|---|---|
| `session_start` | User login / app open | DAU, session volume |
| `view_item` | Navigate to a core feature (e.g. open Payroll module) | Feature reach rate |
| `add_to_cart` | Initiate the core action (e.g. start a payroll run) | Activation attempt rate |
| `begin_checkout` | Complete a key workflow step (e.g. submit for approval) | Workflow completion rate |
| `purchase` | Achieve value moment (e.g. first payroll processed) | Activation rate |
| Week-1 return | D7 retention — user returns within 7 days of first login | Early retention / stickiness |

The same sequential funnel SQL, cohort retention query, and segmentation patterns from this project would be used directly to analyse feature adoption and user activation in a B2B SaaS product analytics context.

---

## Tech stack

| Layer | Tools |
|---|---|
| Data warehouse | Google BigQuery — Standard SQL, partitioned `events_*` wildcard tables |
| Data processing | Python (pandas, google-cloud-bigquery, db-dtypes) |
| Visualisation | Plotly (interactive charts → standalone HTML) |
| Dashboard delivery | Self-contained `dashboard.html` — no server, framework, or login required |
| AI / LLM | Anthropic Claude API (prompt engineering, structured context → product brief) |
| Automation | Parameterised Python pipeline — one command refreshes every query, chart, and narrative |

---

## How to run

### Prerequisites
- Python 3.9+
- Google Cloud project with BigQuery API enabled
- Access to `bigquery-public-data` (available to all GCP accounts)

### Install dependencies
```bash
pip install google-cloud-bigquery pandas plotly db-dtypes anthropic
```

### Authenticate
```bash
gcloud auth application-default login
```

### Configure
In `ga4_funnel_analysis.ipynb` and `generate_dashboard.py`, set:
```python
PROJECT_ID = "your-gcp-project-id"
DATE_START = "20201101"   # change to any range
DATE_END   = "20210131"
DATASET    = "bigquery-public-data.ga4_obfuscated_sample_ecommerce"  # or your own GA4 export
```

### Run the notebook
```bash
jupyter notebook ga4_funnel_analysis.ipynb
```
Sections 1–9 have no API key dependency. Section 10 (AI brief) requires one of:

- **Anthropic Claude** (`export ANTHROPIC_API_KEY="sk-ant-..."`) — [console.anthropic.com](https://console.anthropic.com)
- **Google Gemini (free)** (`export GEMINI_API_KEY="AIza..."`) — [aistudio.google.com](https://aistudio.google.com) → run Section 10f instead of 10d
- **Skip** — a pre-generated brief is already in `product_brief_ga4_funnel.md`; the dashboard embeds it automatically

### Generate the dashboard
```bash
python generate_dashboard.py
```
Produces a fully refreshed `dashboard.html`. Point `DATASET` at your own GA4 export and change the date range to run this on live data.
