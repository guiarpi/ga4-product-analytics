# GA4 Product Funnel Analysis

End-to-end product analytics project built on Google Analytics 4 (GA4) data in BigQuery. Covers session-scoped funnel analysis, revenue metrics, device segmentation, time-to-convert, shoppers funnel, weekly cohort retention, and an AI-generated product brief — all in Python.

**Dataset:** [`bigquery-public-data.ga4_obfuscated_sample_ecommerce`](https://console.cloud.google.com/bigquery?p=bigquery-public-data&d=ga4_obfuscated_sample_ecommerce) · Nov 2020 – Jan 2021

---

## What's in this project

| File | Description |
|---|---|
| `ga4_funnel_analysis.ipynb` | Main analysis notebook — all SQL, charts, and commentary |
| `generate_dashboard.py` | Runs all queries and produces a standalone `dashboard.html` |
| `dashboard.html` | Self-contained interactive dashboard — open in any browser, no Python needed |
| `product_brief_ga4_funnel.md` | Pre-generated AI product brief (included so the dashboard works without an API key) |

---

## Analysis overview

### 1 · Session-scoped funnel
Converts the GA4 event stream into a sequential 5-step funnel: **Session Start → View Item → Add to Cart → Begin Checkout → Purchase**. Each session is counted at a step only if it completed all prior steps in order within the same session — matching how the GA4 UI calculates funnel conversion and avoiding the inflation caused by carrying past-purchase behaviour into new sessions.

The grain is `(user_pseudo_id, ga_session_id)`. Session IDs are extracted from the nested `event_params` array using `UNNEST`.

### 2 · Revenue metrics
Total revenue, average order value, and median order value for converting sessions. The mean/median gap surfaces revenue concentration from high-value orders.

### 3 · Device breakdown
The same sequential funnel split by `device.category` (desktop, mobile, tablet). Used to assess whether device type is a meaningful driver of conversion loss.

### 4 · Time-to-convert
Median time (in hours) between each funnel step, using `APPROX_QUANTILES` on microsecond-precision event timestamps. Restricted to sessions that completed the full funnel, so this measures deliberation time for buyers, not abandonment.

### 5 · Shoppers funnel
An alternative funnel starting at `view_item` — isolating users who demonstrated product intent. Separating this from the full-session funnel reveals the true add-to-cart rate among engaged users and sizes the product discovery gap.

### 6 · Weekly cohort retention
Users grouped by the week of their first session, tracked week-over-week. The heatmap shows what percentage of each cohort returned in subsequent weeks. Analogous to D7/D14 retention in a SaaS context.

### 7 · AI product brief (Section 10 of the notebook)
Structured analysis context is passed to an LLM to generate a product brief with: executive summary, key findings, hypotheses, recommended actions, and metrics to track next. Demonstrates AI-augmented analysis beyond chart generation.

A pre-generated brief (`product_brief_ga4_funnel.md`) is committed to the repo so the dashboard renders fully without an API key. To regenerate it yourself, see the API key setup options below.

---

## Key findings

- **~79% of sessions never reach a product page** — the dominant loss in the funnel, upstream of everything else
- Once users do view a product, add-to-cart rates are healthy — the problem is discovery, not the checkout flow
- Median time-to-purchase is under an hour for converting sessions — low deliberation friction
- Week-1 cohort retention averages in the single digits — most acquired users do not return
- Device type explains very little of the conversion gap — mobile checkout UX is not the lever

---

## How to run

### Prerequisites

- Python 3.9+
- A Google Cloud project with BigQuery API enabled
- Access to `bigquery-public-data` (available to all GCP accounts)

### Install dependencies

```bash
pip install google-cloud-bigquery pandas plotly db-dtypes anthropic
```

### Authenticate with Google Cloud

This project uses [Application Default Credentials](https://cloud.google.com/docs/authentication/application-default-credentials) — no service account key file needed.

```bash
gcloud auth application-default login
```

### Set your project ID

In both `ga4_funnel_analysis.ipynb` and `generate_dashboard.py`, update:

```python
PROJECT_ID = "your-gcp-project-id"
```

### Run the notebook

```bash
jupyter notebook ga4_funnel_analysis.ipynb
```

Run all cells in order. Sections 1–9 have no API key dependency. Section 10 (AI brief) requires an LLM API key — choose one:

**Option A — Anthropic Claude (paid, ~$0.01 per run)**
```bash
export ANTHROPIC_API_KEY="sk-ant-..."
```
Get a key at [console.anthropic.com](https://console.anthropic.com) → API Keys.

**Option B — Google Gemini (free, no credit card)**
```bash
export GEMINI_API_KEY="AIza..."
pip install google-generativeai
```
Get a free key at [aistudio.google.com](https://aistudio.google.com) → "Get API key". Then run **Section 10f** in the notebook instead of 10d.

**Option C — Skip (dashboard still works)**
A pre-generated brief is already in `product_brief_ga4_funnel.md`. The dashboard embeds it automatically — no API key needed.

### Generate the dashboard

```bash
python generate_dashboard.py
```

Opens as `dashboard.html` — a fully self-contained file with no runtime dependencies. If `product_brief_ga4_funnel.md` exists (generated in Section 10 of the notebook), it will be embedded in the dashboard automatically.

---

## SQL patterns used

| Pattern | Where |
|---|---|
| `UNNEST(event_params)` scalar subquery | Extracting `ga_session_id` from the nested array |
| Sequential `COUNTIF` chain | Enforcing step order within a session |
| `APPROX_QUANTILES` | Efficient percentile computation for time-to-convert |
| `DATE_TRUNC(..., WEEK(MONDAY))` | Weekly cohort bucketing |
| `_TABLE_SUFFIX BETWEEN` | Partition pruning to control BigQuery costs |

All SQL is written for **BigQuery Standard SQL** and runs against the public GA4 sample dataset with no modifications.

---

## B2B SaaS translation

The funnel events map directly to SaaS product analytics concepts:

| GA4 event | SaaS equivalent |
|---|---|
| `session_start` | User login / app open |
| `view_item` | Feature discovery — navigating to a core feature |
| `add_to_cart` | Feature first use — attempting the core action |
| `begin_checkout` | Workflow initiated — e.g. creating a first record |
| `purchase` | Activation — completing the key value moment |
| Week-1 retention | D7 retention after first login |

The SQL patterns, funnel methodology, and retention framework apply without modification to any event-based product analytics dataset.

---

## Tech stack

- **Python** — pandas, Plotly, google-cloud-bigquery
- **BigQuery** — Standard SQL, partitioned `events_*` tables, GA4 export schema
- **Plotly** — Interactive charts rendered to standalone HTML
- **Anthropic API / Google Gemini** — LLM for AI-generated product briefs (Gemini free tier supported)
