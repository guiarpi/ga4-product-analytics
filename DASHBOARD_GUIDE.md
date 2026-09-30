# Building the Portfolio Dashboard — Step-by-Step Guide

How to regenerate, verify, publish, and present the GA4 Funnel Analysis dashboard.

**Repo:** https://github.com/guiarpi/funnel-analysis
**Output:** `dashboard.html` — a single self-contained file, no server required.

---

## Table of contents

1. [Prerequisites](#1-prerequisites)
2. [Credentials setup](#2-credentials-setup)
3. [Regenerating the dashboard](#3-regenerating-the-dashboard)
4. [Pre-flight QA checklist](#4-pre-flight-qa-checklist)
5. [Publishing it live](#5-publishing-it-live)
6. [Making the README sell it](#6-making-the-readme-sell-it)
7. [Design principles behind the dashboard](#7-design-principles-behind-the-dashboard)
8. [Presenting it in an interview](#8-presenting-it-in-an-interview)
9. [Adapting it to a new dataset](#9-adapting-it-to-a-new-dataset)

---

## 1 · Prerequisites

| Requirement | Notes |
|---|---|
| Python 3.9+ | `python3 --version` |
| Google Cloud project | With BigQuery API enabled and a billing account attached |
| Packages | `pandas`, `plotly`, `google-cloud-bigquery`, `db-dtypes`, `pyarrow` |
| Anthropic API key | Optional — only for the AI product brief section |

Install everything:

```bash
pip install pandas plotly google-cloud-bigquery db-dtypes pyarrow anthropic
```

> **Note:** `db-dtypes` and `pyarrow` are easy to forget. Without them
> `.to_dataframe()` fails with a cryptic dtype error.

Create a `requirements.txt` so a reviewer can reproduce your environment in one
command — this is a small thing that signals professionalism:

```txt
pandas>=2.0
plotly>=5.18
google-cloud-bigquery>=3.17
db-dtypes>=1.2
pyarrow>=14.0
anthropic>=0.39
```

---

## 2 · Credentials setup

The public GA4 dataset is free to *read*, but BigQuery bills the **query** to
your own project, so you still need authenticated access.

**Recommended — Application Default Credentials (no key file):**

```bash
gcloud auth application-default login
gcloud config set project YOUR_PROJECT_ID
```

**Alternative — service account key:**

```bash
export GOOGLE_APPLICATION_CREDENTIALS="$HOME/path/to/google_creds.json"
```

### Security rules — non-negotiable for a public repo

- `google_creds.json` **must never be committed.** It is already in `.gitignore`
  and confirmed untracked in this repo — keep it that way.
- If a key is ever committed, rotate it immediately in the Google Cloud console.
  Deleting the file in a later commit does **not** remove it from git history.
- Verify before every push:

  ```bash
  git ls-files | grep -iE 'cred|key|secret|\.pem|\.env'   # must return nothing
  ```

- Never hardcode an Anthropic API key. Read it from the environment:

  ```bash
  export ANTHROPIC_API_KEY="sk-ant-..."
  ```

### Cost control

The dataset is ~3 months of events. Each full run of the script executes ~6
queries. Keep costs near zero:

- BigQuery's free tier covers 1 TB of query processing per month.
- The script already prunes partitions via `_TABLE_SUFFIX BETWEEN` — this is the
  single biggest cost lever. Never remove it.
- Dry-run to see bytes billed before executing:

  ```bash
  bq query --dry_run --use_legacy_sql=false 'SELECT ...'
  ```

---

## 3 · Regenerating the dashboard

```bash
cd "/Users/guilhermearpi/Documents/Claude/Projects/Funnel Analysis"
python generate_dashboard.py
```

Expected console output:

```
Connected  |  20201101 → 20210131
Querying session funnel...
Querying unique users...
Querying revenue...
Querying shoppers funnel...
Querying time-to-convert...
Querying cohort retention...
Computing insights...
✓ dashboard.html written
```

Then open it:

```bash
open dashboard.html
```

### Critical rule: never hand-edit `dashboard.html`

`dashboard.html` is a **build artifact**. Every change must go into
`generate_dashboard.py`, which holds the full HTML template as an f-string.

This has bitten this project before — manual edits to the HTML were silently
destroyed on the next script run. If you want to change a number, a heading, a
colour, or a paragraph, edit the template in the `.py` file and regenerate.

Two f-string gotchas when editing the template:

- **CSS braces must be doubled:** `.card {{ padding: 20px; }}`
- **JavaScript braces must be doubled too:** `if (x) {{ ... }}`
- Python values inject with single braces: `{total_sessions:,}`

If you get `KeyError` or `ValueError: unexpected '{'` on run, you missed a
doubled brace.

### Changing the date range

Edit the config block at the top of the script:

```python
DATE_START = "20201101"
DATE_END   = "20210131"
```

Both are `YYYYMMDD` strings matching the `events_YYYYMMDD` table suffix. Every
query and every narrative number updates automatically — nothing is hardcoded
downstream. Demonstrating this to an interviewer is a strong moment.

> **Watch the cohort boundary.** Cohorts are bucketed with
> `DATE_TRUNC(..., WEEK(MONDAY))`. If `DATE_START` is not a Monday, the first
> cohort will contain only a partial week and will look anomalous. Either start
> on a Monday or exclude the first partial cohort.

---

## 4 · Pre-flight QA checklist

Run through this every time before you share the link. A single wrong number
undoes the credibility the rest of the dashboard earns.

### Numbers

- [ ] KPI values are non-zero and plausible
- [ ] Funnel steps decrease monotonically (each step ≤ the one before)
- [ ] Percentages that should sum to 100% do
- [ ] Every stat quoted in prose matches the chart it refers to
- [ ] Mean vs median framed correctly (AOV $79 mean / $55 median)
- [ ] No `nan`, `None`, `inf`, or `0.0%` placeholders anywhere in the page

### Charts

- [ ] Funnel renders as a tapering funnel, steps in sequence
- [ ] Time-to-convert bars match their printed labels
- [ ] Cohort heatmap colour direction is intuitive — **darker must mean better
      retention**, not worse
- [ ] Cells with no data yet (future weeks for recent cohorts) are **blank, not
      0%** — showing censored data as zero invents a finding that isn't there
- [ ] Every chart has a title, axis labels, and working hover tooltips
- [ ] Axis scales are linear unless a log scale is explicitly justified in text

### Narrative

- [ ] Every causal claim is supported by data actually shown on the page
- [ ] Anomalies are explained by evidence, not plausible-sounding speculation
- [ ] Assumptions behind projections are stated inline
- [ ] Recommendations follow Problem → Actions → Business Impact
- [ ] No leftover TODOs, placeholders, or commented-out sections

### Technical

- [ ] Opens correctly from `file://` with no console errors (⌘⌥I in Chrome)
- [ ] Renders on a 1280px laptop screen without horizontal scroll
- [ ] File size is reasonable (~1–3 MB with Plotly bundled)
- [ ] No credentials, project IDs, or internal paths visible in the HTML source

Quick automated sweep:

```bash
grep -icE 'nan|undefined|null|\{[a-z_]+\}' dashboard.html
```

Any hit is worth investigating — it usually means an f-string variable failed to
interpolate.

---

## 5 · Publishing it live

A downloadable `.html` gets opened far less often than a link. Publish it.

### GitHub Pages (free, ~2 minutes)

```bash
mkdir -p docs
cp dashboard.html docs/index.html
git add docs/
git commit -m "Publish interactive dashboard via GitHub Pages"
git push
```

Then: **repo → Settings → Pages → Source: `main` / `/docs` → Save.**

Live at `https://guiarpi.github.io/funnel-analysis/` within a minute.

Add a build step to the script so `docs/index.html` never goes stale:

```python
import shutil
shutil.copy(OUTPUT, Path("docs/index.html"))
print("✓ docs/index.html updated for GitHub Pages")
```

### Put the link where it gets seen

- Top line of the repo README
- GitHub repo "About" → Website field
- CV / cover letter, as a plain URL
- LinkedIn Featured section

---

## 6 · Making the README sell it

Most reviewers spend under 60 seconds on a portfolio repo. Optimise for that.

**Structure, in order:**

1. **One-sentence hook** — what was analysed and what was found
2. **Live demo link** — above the fold, impossible to miss
3. **Screenshot** — the single highest-ROI addition to this repo
4. **Headline findings** — 3–5 bullets with real numbers
5. **Stack & methodology** — BigQuery SQL → pandas → Plotly → HTML
6. **How to run it** — copy-pasteable commands
7. **What I'd do next** — shows analytical maturity

### Add the screenshot

```bash
mkdir -p assets
# macOS: ⇧⌘4 then space, click the window → saves to Desktop
mv ~/Desktop/Screen*.png assets/dashboard-preview.png
```

In the README:

```markdown
[**→ View the live interactive dashboard**](https://guiarpi.github.io/funnel-analysis/)

![Dashboard preview](assets/dashboard-preview.png)
```

Capture the KPI row plus the funnel chart in one frame — that's the money shot.

### Lead with findings, not tooling

Reviewers care what you concluded, not that you used pandas.

> **Weak:** "This project uses BigQuery, pandas and Plotly to analyse GA4 data."
>
> **Strong:** "79% of sessions never reach a product page. Fixing discovery —
> not checkout — is worth ~$50K per quarter at current AOV. Built on 355K
> sessions of GA4 data in BigQuery."

### Add a LICENSE

An MIT license (repo → Add file → Create new file → type `LICENSE` → GitHub
offers a template picker) signals the work is genuinely yours to share.

---

## 7 · Design principles behind the dashboard

What makes this dashboard read as senior rather than student work.

**Lead with the answer.** Executive summary and KPI row come first. A reader who
stops after 10 seconds still leaves with the finding.

**Every chart earns its place.** If a chart doesn't change a decision, cut it.
A dashboard with five sharp charts beats one with twelve.

**Pair every chart with its "so what."** The insight panel beside each chart is
what separates analysis from reporting. The chart shows *what*; the panel says
*what it means* and *what to do*.

**Quantify the opportunity in currency.** "21% view rate" is a metric.
"~641 orders / ~$50K per quarter" is a business case. Always state the
assumption behind the projection.

**Report negative results.** "Device type explains <0.1pp of variance" is a real
finding — it redirects effort away from a mobile redesign that wouldn't have
worked. Analysts who only report positive findings look like they're
storytelling rather than investigating.

**Show your uncertainty.** Naming a data limitation before someone asks builds
more trust than a flawless-looking deck. Partial cohorts, censored retention
windows, obfuscated public data — say so.

**Restraint in visual design.** One accent colour, consistent typography,
generous whitespace. Colour should encode meaning, never decorate.

**Never encode the same variable twice.** A bar chart already encodes value by
height — adding a colour gradient for the same variable adds noise.

---

## 8 · Presenting it in an interview

### The 60-second walkthrough

> "I analysed 355,000 sessions of GA4 e-commerce data in BigQuery to find where
> the funnel leaks. Session-to-purchase conversion is 0.8%, but the interesting
> part is *where* the loss happens: 79% of sessions never reach a product page
> at all. Once someone views a product, the rest of the funnel performs
> reasonably — checkout-to-purchase is above 50%.
>
> That reframes the priority. The instinct is usually to optimise checkout, but
> the data says checkout isn't the problem — discovery is. I sized it: a 5-point
> improvement in the session-to-product-view rate is worth roughly 640 extra
> orders, about $50K a quarter at current AOV.
>
> The whole thing regenerates from a single script — change two date constants
> and every query, chart, and number updates."

### Questions you should expect

**"Why session-scoped rather than user-scoped?"**
It matches how GA4 reports conversion and avoids a past purchaser carrying over
into every future session, which inflates rates. User-scoped answers a different
question — lifetime conversion — and I'd run it alongside, not instead.

**"How do you know the order of events is real?"**
The SQL enforces sequence with timestamp comparisons, not just presence. A
session only counts at `add_to_cart` if its first `add_to_cart` came after its
first `view_item`, which came after `session_start`. Without that you'd count
out-of-order events as conversions.

**"Your projection assumes marginal traffic converts like existing traffic —
does it?"**
Almost certainly not; incremental traffic usually converts worse. I'd treat
$50K as the optimistic bound and run a sensitivity range at 50% and 75% of the
observed rate before committing to a target.

**"What are the limitations of this data?"**
It's an obfuscated public sample, so absolute revenue isn't meaningful. There's
no user-level marketing spend, so I can't compute ROAS or true CAC. And the
window is three months over a holiday period, so seasonality is baked into every
number. The *methodology* transfers; the specific figures shouldn't be quoted as
benchmarks.

**"What would you do next?"**
Segment the top-of-funnel drop by traffic source. If the 79% is concentrated in
a few paid channels, it's a targeting and landing-page problem and the fix is in
acquisition. If it's uniform across sources, it's a site navigation problem.
Those are different teams and different budgets — that's the next query I'd run.

### Have a defensible answer for every anomaly

If a number looks odd on your own dashboard, an interviewer will ask about it.
The strongest possible answer is *"that's a data artifact and here's why"* —
knowing when a spike isn't real is a core analyst skill. The weakest is a
confident causal story that turns out to be a boundary effect.

---

## 9 · Adapting it to a new dataset

To point this at a real company's GA4 export:

1. Change `PROJECT_ID` and `DATASET` to the company's export
2. Change `DATE_START` / `DATE_END`
3. Map the funnel steps to their actual event names — replace
   `view_item` / `add_to_cart` / `begin_checkout` / `purchase` with whatever
   their instrumentation uses
4. Re-check the cohort grain — weekly suits e-commerce; B2B SaaS usually wants
   monthly cohorts and a longer window

Everything downstream — charts, narrative, findings, recommendations — is
computed from the query results, so it adapts without further edits.

### The B2B SaaS translation

Worth being able to recite, since it's what a SaaS interviewer is really asking:

| E-commerce | B2B SaaS equivalent |
|---|---|
| `session_start` | Login / session start |
| `view_item` | Opened a core feature |
| `add_to_cart` | Started a key workflow |
| `begin_checkout` | Configured / invited teammates |
| `purchase` | Activated / upgraded |
| Weekly cohort retention | D7 / W1 retention |
| AOV | ACV or expansion revenue |
| Session-scoped funnel | Per-session activation funnel |
| Device segmentation | Plan tier or company-size segmentation |

The analytical pattern — sequential funnel, cohort retention, opportunity
sizing, prioritised recommendations — is identical. Only the event names change.

---

*Last updated: 25 August 2026*
