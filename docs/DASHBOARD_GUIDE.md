# Dashboard runbook

How to regenerate, check, publish and adapt `dashboard.html` — a single
self-contained file that needs no server. Run every command from the repository root.

---

## 1 · Prerequisites

| Requirement | Notes |
|---|---|
| Python 3.11 or later | Outputs were produced on 3.13; CI tests 3.11–3.13 |
| Google Cloud project | BigQuery API enabled, billing attached — the query is billed to your project even on public data |
| Packages | `pip install -r requirements.txt` |
| LLM key | Optional, only to regenerate the AI brief: `ANTHROPIC_API_KEY` or `GEMINI_API_KEY` |

`db-dtypes` and `pyarrow` (both in `requirements.txt`) are easy to drop by
accident; without them `.to_dataframe()` fails with a cryptic dtype error.

---

## 2 · Credentials and cost

Use Application Default Credentials — no key file:

```bash
gcloud auth application-default login
export GA4_PROJECT_ID="your-gcp-project"   # or: gcloud config set project …
```

### Security rules for a public repo

- Never commit `google_creds.json`, `.env` or any API key. Keys come from
  environment variables only (`ANTHROPIC_API_KEY`, `GEMINI_API_KEY`).
- If a key is ever committed, rotate it at once: deleting the file in a later
  commit does not remove it from git history.
- The project ID is printed to the console only; notebook output and the page
  never show it.
- Before pushing, scan tracked files and notebook outputs:

  ```bash
  git grep -nE 'AIza|sk-ant-|private_key'      # must return nothing but placeholders
  ```

### Cost control

A full run executes 16 queries and scans about 17 GB on the public sample —
well inside BigQuery's 1 TB monthly free tier.

- Every query is partition-pruned (`_TABLE_SUFFIX BETWEEN`) — the biggest cost
  lever. Never remove it.
- Every query runs under a bytes-billed cap (`GA4_MAX_BYTES_BILLED`, default
  5GB per query). BigQuery refuses, without charging, any query over it.
- See what a run would scan, for free, before running it:

  ```bash
  python generate_dashboard.py --dry-run
  ```

---

## 3 · Regenerating the dashboard

```bash
python generate_dashboard.py
open dashboard.html
```

The console lists each query, then `Computing insights...`,
`AI product brief included.` (when `product_brief_ga4_funnel.md` exists) and
`Done → dashboard.html (~4,900 KB)`. A note appears if the brief was written
from an older version of the analysis; regenerate it with Section 9 of the
notebook.

### Never hand-edit `dashboard.html`

It is a build artefact; the next run overwrites any manual edit. Change the
source instead:

| To change | Edit |
|---|---|
| A number, a verdict or a computed sizing | `generate_dashboard.py` (values) or `metrics.py` |
| Layout, wording, a section | `templates/dashboard.html.j2` and `templates/partials/` |
| Styles | `templates/dashboard.css` |
| A chart | `charts.py` (shared with the notebook) |
| A query | `queries.py` (shared with the notebook) |

Templates are Jinja2 with autoescaping and strict undefined values: a value the
script does not define fails the run instead of rendering blank. Format numbers
with `|fmt(',.1f')`, p-values with `|pval`, and mark trusted HTML with `|safe`.

### Changing the dataset or date range

Set environment variables; nothing is edited in code:

```bash
export GA4_DATASET="your-project.analytics_123456789"
export GA4_DATE_START="20240101"   # YYYYMMDD, inclusive
export GA4_DATE_END="20240331"
```

Every query and every number in the text follows. Cohorts are Monday-anchored:
if the window starts mid-week, the first cohort is a partial week, and it is
shown in grey and left out of the averages automatically.

---

## 4 · Pre-flight checks

Run these before sharing the link.

### Automated

```bash
python -m pytest -q                        # SQL builders, statistics, templates, chart menus
python -m pyflakes *.py tests
python generate_dashboard.py --dry-run     # scan sizes under the cap
```

### Numbers

- [ ] Funnel steps decrease monotonically, and the losses table adds up
- [ ] Every figure in the text matches the chart or table it describes
- [ ] No `nan`, `None`, `inf` or empty values in the page
- [ ] Notebook (run top to bottom) and dashboard agree on the headline figures
      and the three recommendation sizes
- [ ] Every number in the AI brief appears in `product_brief_context.json`

### Charts

- [ ] Cohort heatmap: darker means better retention; censored cells blank, not 0%;
      the partial first week grey
- [ ] Listing-page colours: orange only where the whole 95% interval is below the
      average, blue only where it is above, grey otherwise
- [ ] The menus work: funnel segments, category sort, heatmap % / users

### Narrative

- [ ] Every causal claim rests on data shown on the page; open causes are named
      as open (e.g. landing pages: page, stock or tracking)
- [ ] Assumptions behind each sizing are stated where the number appears
- [ ] Each recommendation has problem, action, sized impact, metric and target,
      confidence and main risk

### Technical

- [ ] Opens from `file://` with no console errors
- [ ] No horizontal scroll at 1280px or at about 570px (phone)
- [ ] No credentials or project ID in the HTML source

---

## 5 · Publishing it live

GitHub Pages serves the `main` branch from the repo root, so there is no copy to
keep in sync:

- `dashboard.html` → `https://guiarpi.github.io/ga4-product-analytics/dashboard.html`
- `index.html` is a one-line redirect, so the site root opens the dashboard too.
- `.nojekyll` (empty) stops GitHub running Jekyll over the repo; without it the
  `{{ }}` in the templates can break the Pages build.

**To update the live dashboard:** regenerate, commit `dashboard.html`, push.
Pages redeploys within a minute or two; check "pages build and deployment" under
the repo's **Actions** tab if it doesn't.

To set it up on a fork: **Settings → Pages → Source: Deploy from a branch →
`main` / `(root)`**, or
`gh api repos/<owner>/<repo>/pages -X POST -f "source[branch]=main" -f "source[path]=/"`.

### Scheduled regeneration (E8)

`.github/workflows/regenerate_dashboard.yml` rebuilds the page from BigQuery every
Monday (06:17 UTC) and on demand (**Actions → regenerate dashboard → Run workflow**).
It installs `requirements-lock.txt`, dry-runs first (so it stops before any query
over the cost cap), regenerates, and commits `dashboard.html` **only if it changed
in anything but its generated date** (`ci_utils.py dashboard-changed`). On the static
public sample a scheduled run therefore makes no commit; on a live GA4 export it keeps
the page current.

**Authentication is keyless** (Workload Identity Federation): GitHub's short-lived
OIDC token is exchanged for a service account that can only run BigQuery jobs, and
only for this repo's `main` branch. No key exists to store, leak or rotate.

One-time setup — read the script, then run it with `gcloud` logged in as a project
owner and `gh` logged in with admin rights on the repo:

```bash
bash ci/setup_gcp_wif.sh
```

It enables the IAM APIs, creates the `ga4-dashboard-ci` service account
(`roles/bigquery.jobUser` only), the `github-actions` pool and its OIDC provider
(restricted to this repo and `main`), and stores three **secrets** —
`GA4_PROJECT_ID`, `GCP_WIF_PROVIDER`, `GCP_SERVICE_ACCOUNT`. Secrets, not
variables: this repo's Actions logs are public, and only secrets are masked in them.

**The AI brief is not regenerated in CI.** The workflow embeds the committed
`product_brief_ga4_funnel.md`; writing a new brief stays a local step (notebook
Section 9), checked before it is committed.

**Cost:** about 17 GB per run, ~70 GB a month weekly — inside BigQuery's 1 TB monthly
free tier, but it shares that quota with local runs.

---

## 6 · Adapting it to a new dataset

1. Point `GA4_DATASET`, `GA4_DATE_START` and `GA4_DATE_END` at the export and run
   `--dry-run`; raise `GA4_MAX_BYTES_BILLED` if a query is over the cap.
2. Map the funnel to the export's event names: `FUNNEL_EVENTS` in `queries.py`.
3. Check the parameters the analysis reads — `ga_session_id`, `medium`,
   `page_location`, `ga_session_number` — and the `items` fields
   (`item_category`, `price`, `quantity`). Newer exports also carry
   session-level traffic-source fields, which can replace the medium parameter.
4. Re-check the cohort grain: weekly suits e-commerce; B2B SaaS usually wants
   monthly cohorts and a longer window.
5. Review the wording that describes the public sample: the "About this
   dataset" block and the recommendations' actions.

The narrative is computed and conditional, so verdicts follow the new numbers;
the actions and SaaS mapping are written for this store and need a read-through.

### The B2B SaaS translation

| E-commerce | B2B SaaS analogue |
|---|---|
| `session_start` | Login / session start |
| `view_item` | Reached a core feature |
| `add_to_cart` | Started a key workflow |
| `begin_checkout` | Configured the workflow / invited teammates |
| `purchase` | Activated / upgraded |
| Next-week return | Early retention — closest to D7, but a calendar-week measure |
| Revenue per purchase | ACV or expansion revenue |
| Device or channel segmentation | Plan tier or company-size segmentation |

The pattern — sequential funnel, cohort retention, opportunity sizing,
prioritised recommendations — carries over; the event names, the session
definition and the retention window need mapping to the product.

---

*Last updated: 20 November 2026*
