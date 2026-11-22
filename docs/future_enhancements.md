# Future Enhancements — GA4 Product Analytics

Roadmap for extending this project. Items are grounded in the specific gaps and
unanswered questions from the current analysis — not generic best practice.

**Current state (2026-11-21):** E1–E6 are done. The analysis answers, in order: where the
funnel leaks (including landing pages), whether it is the traffic, the device or the
catalogue (listing pages and price bands), whether visitors come back (with the trend and
new vs returning), and what to do — three recommendations sized in revenue — plus a
data-quality section and an AI-written brief checked against the computed results. All
against the GA4 public e-commerce sample (Nov 2020 – Jan 2021).

Engineering work done alongside, outside the E-list: all SQL in one shared module
(`queries.py`); the page as Jinja2 templates; a pytest suite run by GitHub Actions on every
push (lint + tests, no BigQuery); a per-query cost cap with dry-run estimates; exact
medians; a lock file of the versions behind the committed outputs.

---

## Priority overview

| # | Enhancement | Status | Effort (hrs) | Impact | Risk | Prerequisite |
|---|---|---|---|---|---|---|
| E1 | Traffic source × funnel cross-tab | ✅ done | 4–6 | High | Low | — |
| E2 | Product category funnel breakdown | ✅ done | 5–7 | High | Low | — |
| E3 | Return-visitor vs new-visitor funnel | ✅ done | 4–5 | Medium | Low | — |
| E4 | Parameterised date range and config | ✅ done | 2–3 | Medium | Low | — |
| E5 | GitHub Pages live deployment | ✅ done | 2–3 | Medium | Low | — |
| E6 | Statistical significance (device, channel, category) | ✅ done | 3–4 | Medium | Low | — |
| E9 | Landing-page diagnosis: page, stock or tracking? | ✅ done | 2–3 | High | Low | — |
| E10 | Cross-session purchase paths | next | 3–4 | Medium | Low | — |
| E11 | Geography segment (`geo.country`) | | 2–3 | Medium | Low | — |
| E12 | Period-over-period comparison | | 4–6 | Medium | Medium | a second data window |
| E7 | Power BI dashboard build | planned — step 1 done | 10–11 | Medium | Medium | E4, Windows |
| E8 | Scheduled dashboard regeneration in CI | | 3–5 | Low | High | E4, E5 |

---

## Recommended sequence

**Next — close the open questions in the current findings (E10)**
E9 (done) diagnosed Recommendation 1's landing pages. E10 accounts for the 2,127 purchasing
sessions the sequential funnel leaves out, with the data already in hand.

**Then — widen the view (E11, E12)**
A geography split is a cheap new segment for the existing funnel menu. Period-over-period
comparison needs a second window of data; with it, the brief can reason about change, not
just a snapshot.

**Last — platform extensions (E7, E8)**
Power BI and scheduled regeneration carry platform dependencies or secret management. Their
value is marginal once the analysis is complete. Test CI is already in place, so E8 is only
the scheduled BigQuery run.

---

## Enhancements

---

### E1 · Traffic source × funnel cross-tab

**Goal:** Break the 5-step funnel by acquisition channel (`traffic_source.medium`)
to reveal whether the 78.8% top-of-funnel miss rate is a site design problem or a
traffic quality problem.

**Why it matters:** The AI product brief flags this as the single highest-priority
unanswered question: "If paid traffic has a 90% miss rate but organic has a 60%
miss rate, the problem is ad targeting — not navigation." The current analysis
treats all 354,857 sessions equally, which could lead to the wrong intervention
(redesigning navigation when the real issue is ad creative targeting low-intent
users). This segmentation would reprioritise the entire action list.

**Subtasks:**

| Step | What changes | Estimate |
|---|---|---|
| Add channel-segmented funnel SQL | New query in `generate_dashboard.py` joining `traffic_source.medium` to the existing session-funnel CTE | 1.5 h |
| Add the same query as a new notebook cell | New cell in Section 3 of `ga4_funnel_analysis.ipynb`, after the existing funnel query | 0.5 h |
| Build a grouped bar chart by channel | New `chart_div()` call in `generate_dashboard.py` using `go.Bar` with `barmode='group'` | 1.5 h |
| Add commentary cell in notebook | Interpretation cell below the new chart, consistent with existing section style | 0.5 h |
| Update dashboard HTML template | Add a new section block between the existing funnel and device sections | 1 h |
| Update README key findings | Add a caveat that channel-level analysis is in E1 if findings change | 0.5 h |

**Effort:** 4–6 h | **Impact:** High | **Risk:** Low | **Prerequisite:** None

**Status:** Implemented 2026-11-12 — notebook Section 4 (then 5c–5d), dashboard section 4 "Is it the traffic?", LLM context in 9.3. First run: 8.7pp spread (cpc 21.6% → referral 30.3%), reported as borderline; 26.2% of sessions have no session-level medium and are disclosed separately.

**Expected outcome:** A grouped bar chart showing funnel conversion for each
channel (organic, cpc, email, direct, referral). If channels differ materially,
the recommended action changes. If they converge, the navigation hypothesis is
confirmed. Either way, the open question in the product brief is closed.

> **Correction (found during implementation):** `traffic_source.medium` is the
> user's **first-touch** medium, repeated on every session that user ever has —
> grouping a session funnel by it misattributes returning sessions. The
> implementation takes the session's own medium from `event_params`
> (`key = 'medium'`), falls back to first touch only if session-level coverage is
> under 50%, and the notebook shows how often the two definitions disagree.

---

### E2 · Product category funnel breakdown

**Goal:** Break the view-to-cart rate by `item_category` (from `UNNEST(items)`)
to identify which product categories have the worst conversion from product view
to add-to-cart, sizing the "product page content audit" recommendation.

**Why it matters:** The AI brief recommends auditing the 10 highest-traffic
product pages with the lowest add-to-cart rates, but the current analysis has no
category dimension at all — it can't rank categories or identify where to start.
The 80.2% view-to-add-to-cart drop is the second-largest leak in the funnel;
this enhancement identifies where within the product catalogue it is concentrated.

**Subtasks:**

| Step | What changes | Estimate |
|---|---|---|
| Write category-scoped funnel SQL | New CTE in `generate_dashboard.py` joining `UNNEST(items)` on `view_item` and `add_to_cart` events, grouped by `item_category` | 2 h |
| Handle items array correctly | `view_item` and `add_to_cart` both carry an `items` array; the join must match item to event, not session | 1 h |
| Build a horizontal bar chart of category conversion rates | `go.Bar(orientation='h')` sorted by add-to-cart rate ascending, consistent with existing funnel chart style | 1 h |
| Add notebook section (new Section 8, renumber AI brief to Section 9) | Query cell + chart cell + commentary, referencing the product brief recommendation | 1 h |
| Update `generate_dashboard.py` to call `q()` for the new query | Add new `chart_div()` call and inject into the HTML template | 0.5 h |
| Update README analysis overview table | Add row for category breakdown | 0.5 h |

**Effort:** 5–7 h | **Impact:** High | **Risk:** Low | **Prerequisite:** None

**Status:** Implemented 2026-11-12 — notebook Section 6 (then 7c–7d), dashboard section 6 "Where in the catalogue?", LLM context in 9.3. First run: 38 of 74 categories ranked, benchmark 24.6%. The run surfaced duplicate labels (`Bags` vs `Lifestyle/Bags/`), now normalised to the last path segment in SQL. **Revised 2026-11-20:** `item_category` turned out to be a listing context, not a product attribute (414 of 420 products appear under several), so rows are listing pages split by level from their paths; only product-type pages are ranked (6 of 12 below the 22.1% product-type average), and a price-band split was added.

> ⚠️ **Architecture note:** the `items` array is only present on `view_item`,
> `add_to_cart`, and `purchase` events — not on `session_start` or
> `begin_checkout`. The category query is a separate analysis (not an extension
> of the existing session-scoped funnel CTE) and should be framed as such in the
> notebook commentary. Attempting to shoehorn it into the existing CTE would
> require a structural change to the core query.

**Expected outcome:** A ranked chart of product categories by view-to-cart
conversion rate. The lowest performers become the direct input to the product page
content audit. The chart is evidence that a funnel metric can be translated into a
prioritised action list.

---

### E3 · Return-visitor vs new-visitor funnel

**Goal:** Split the funnel into first-visit and return-visit sessions by checking
whether a `user_pseudo_id` has a prior session in the dataset, then compare
conversion rates for each group.

**Why it matters:** The AI brief asks: "Do return visitors convert at a materially
higher rate? If yes, the product has retention value and the focus should be on
bringing new users back." The weekly cohort heatmap already shows most users do
not return past week 1 — but it does not show whether those who do return
actually convert. If return-visitor conversion is significantly higher, the growth
lever is retention and re-engagement (email, retargeting), not acquisition.

**Subtasks:**

| Step | What changes | Estimate |
|---|---|---|
| Classify sessions as first-visit or return-visit | New CTE using `ROW_NUMBER() OVER (PARTITION BY user_pseudo_id ORDER BY session_start_timestamp)` to flag first vs subsequent sessions | 1.5 h |
| Apply the funnel CTE to each group separately | Two variants of the existing funnel CTE, filtered on the first-visit flag | 1 h |
| Build a side-by-side comparison bar chart | Two `go.Bar` traces on one figure, labelled "New visitors" and "Return visitors" | 1 h |
| Add as notebook Section 7b (between cohort retention and AI brief) | Query + chart + commentary cell | 1 h |

**Effort:** 4–5 h | **Impact:** Medium | **Risk:** Low | **Prerequisite:** None

**Status:** Implemented 2026-11-19 — notebook Section 7.4 (then 8c), dashboard section 7, LLM context in 9.3. Sessions are flagged with GA4's `ga_session_number = 1` (first session ever for that cookie) rather than a `ROW_NUMBER()` over the window, so users first seen before the window are not misclassified as new. Computed on the same per-session scan as the data-quality checks (all sessions, the primary basis). First run: returning sessions buy at 1.69% vs 0.42% for new (4.1×, χ² p < 0.001) and earn $1.27 vs $0.27 per session. Because returners self-select, Recommendation 2 is sized as a range — extra returning users valued between the new and returning rates ($2.6k–$12.0k over the window). The dashboard's funnel segment menu includes new and returning sessions (added 2026-11-20).

**Expected outcome:** A chart showing funnel conversion for new vs return visitors.
If return-visitor conversion is 2× or more, the narrative shifts: the product has
demonstrated value but fails to bring users back. If the rates are similar, the
product-page problem affects even motivated users — strengthening the case for
content improvement over re-engagement.

---

### E4 · Parameterised date range and project config

**Goal:** Move hardcoded date literals and the project ID string into a single
`config.py` file (or a `.env` file) so the project can be run against any date
range or GA4 dataset without editing multiple files.

**Why it matters:** The date range `2020-11-01` to `2021-01-31` is currently
hardcoded in `generate_dashboard.py` in at least three places — once per query
that uses `_TABLE_SUFFIX BETWEEN`. Anyone adapting the project to their own data
must find and replace all occurrences manually, which is error-prone and signals
that the code is not designed for reuse. This is a prerequisite for E7 (Power BI)
and E8 (CI pipeline), both of which need a clean configuration entry point.

**Subtasks:**

| Step | What changes | Estimate |
|---|---|---|
| Create `config.py` | New file with `PROJECT_ID`, `START_DATE`, `END_DATE` constants; `_TABLE_SUFFIX` range derived from these | 0.5 h |
| Update `generate_dashboard.py` | Import from `config.py`; replace all hardcoded date strings and `PROJECT_ID` with the constants | 1 h |
| Update notebook `ga4_funnel_analysis.ipynb` | Cell 2 (currently sets `PROJECT_ID` inline) imports from `config.py` instead; SQL strings reference Python format vars already, so minimal change | 0.5 h |
| Add `config.py` to README "How to run" section | Replace the current "update PROJECT_ID manually" instruction with a single config step | 0.5 h |

**Effort:** 2–3 h | **Impact:** Medium | **Risk:** Low | **Prerequisite:** None

**Status:** Implemented 2026-11-19 — `config.py` reads `GA4_PROJECT_ID` (falling back to `GOOGLE_CLOUD_PROJECT`, then the gcloud default), `GA4_DATASET`, `GA4_DATE_START` and `GA4_DATE_END` from the environment and validates them before any query runs; the personal project ID is no longer in the repo. Dataset-specific narrative in the dashboard ("About this dataset", some recommendation text) is still written for the public sample — the script prints a warning when run on other data.

**Expected outcome:** A first-time user sets four values in `config.py` once and
runs without editing any other file. The project becomes directly reusable against
any GA4 export — which is the core portfolio claim.

---

### E5 · GitHub Pages live deployment ✅ done

**Goal:** Host `dashboard.html` as a live URL so the portfolio link opens in the
browser directly rather than triggering a file download.

**What was done:** GitHub Pages serves the `main` branch from the repo root —
no `gh-pages` branch and no copied file, so the live site can never drift from
the committed deliverable. An `index.html` redirect makes the short URL open the
dashboard, and an empty `.nojekyll` skips the Jekyll build. The README links to
the live page and shows a preview screenshot (`assets/dashboard-preview.png`).
Update steps are in [DASHBOARD_GUIDE.md](DASHBOARD_GUIDE.md), section 5.

**Live:** https://guiarpi.github.io/ga4-product-analytics/

---

### E6 · Statistical significance (device, channel, category)

**Goal:** Add Wilson confidence intervals (or a chi-square test) to the device
conversion rates so the claim "device type explains very little of the conversion
gap" is backed by a formal significance test rather than a visual inspection.

**Why it matters:** The current device chart shows <0.1pp variance across desktop,
mobile, and tablet — which visually looks conclusive. But with 354,857 sessions
distributed unevenly across devices, it is worth confirming the result formally
rather than asserting it. The current analysis cannot answer "is this difference
real or sampling noise?" — which is the exact question a data-literate reviewer
will ask.

**Subtasks:**

| Step | What changes | Estimate |
|---|---|---|
| Add a chi-square test between device groups | New Python cell in the device section of `ga4_funnel_analysis.ipynb` using `scipy.stats.chi2_contingency` on the conversion counts | 1 h |
| Compute Wilson confidence intervals per device | Python cell computing `lower, upper = proportion_confint(count=conversions, nobs=sessions, method='wilson')` from `statsmodels` | 1 h |
| Annotate the device bar chart with confidence interval whiskers | Update `generate_dashboard.py` to add `error_y` parameters to the device `go.Bar` trace | 1 h |
| Add `scipy` and `statsmodels` to `requirements.txt` | Two new lines | 0.1 h |
| Add interpretation cell | One markdown cell concluding whether the device difference is statistically significant | 0.5 h |

**Effort:** 3–4 h | **Impact:** Medium | **Risk:** Low | **Prerequisite:** None

**Expected outcome:** The device chart displays error bars and the notebook
includes a one-line significance conclusion ("chi-square p=0.61 — device type
does not significantly predict conversion, confirming the visual result"). This
closes the device question formally.

**Status:** Implemented 2026-11-19, widened beyond devices — shared helpers in `stats_utils.py` (Wilson interval, chi-square with Cramér's V; `scipy` added to requirements, no `statsmodels` needed), notebook Sections 4.3, 5.3 and 6.4 (then 5f and 7d), dashboard device table, channel and catalogue sections, LLM context in 9.3. First run: device × purchase χ² p = 0.259 (not significant; intervals overlap). Channel × saw-a-product is significant (p < 0.001) but small (Cramér's V = 0.069) — the page states that the site-vs-traffic verdict rests on practical size (the 10pp threshold), not significance. Categories are flagged as underperforming only when their whole 95% interval lies below the site average (12 of 23 below, 8 above, 3 indistinguishable), which replaces an arbitrary neutral band; carts at stake are counted for flagged categories only. (Those category figures are from the first run; after the 2026-11-20 revision — see E2 — 6 of 12 product-type pages are below, 5 above and 1 indistinguishable.) The device result is shown as interval columns in the existing table rather than chart error bars, because the device breakdown is deliberately a table.

---

### E7 · Power BI dashboard build

**Status:** planned. Step 1 is done: the build guide is aligned with the current
dashboard (kept out of the public repo until the build starts), and the data path
exists — `powerbi_export.py` runs the dashboard's own SQL (`queries.py`) under the
cost cap and checks that its session table reproduces the funnel before writing
anything. Building the report needs Windows (a Windows PC or a Windows 11 for ARM VM).

**Goal:** the same analysis as a Power BI report, in the dashboard's question order,
reconciling number for number with `dashboard.html`.

**Why it matters:** the same analysis in a BI tool that many product and analytics teams
use — warehouse SQL for the sequential logic, a model at the right grain, and business
logic as DAX measures rather than pre-baked columns.

**Subtasks:**

| Step | What changes | Estimate |
|---|---|---|
| ✅ Bring the build guide up to date | Story order, data quality, new vs returning, significance, listing pages and price bands, landing diagnosis, retention trend, recommendations, Phase 5 interactions, glossary names, current QA numbers | done |
| ✅ Data from `queries.py`, not hand-copied SQL | `pbi_*` extract builders reuse the dashboard's query text; `powerbi_export.py` (files under the cost cap, or `--sql-only` for the connector) | done |
| Run the export | `python powerbi_export.py` (~11 GB on the public sample; needs BigQuery quota) | 0.25 h |
| Build the model | Import the files; date, weeks-since and funnel-step tables; relationships; `_Measures` | 1 h |
| Write the DAX measures | Funnel and continue rates, Wilson intervals, revenue and exact medians, untagged sensitivity, channel spread and verdict, listing status and carts at stake, retention with censoring, mix-adjusted trend, recommendation sizes | 2.5 h |
| Build the pages | One per question (2–8) plus method and glossary; conclusion titles as measures | 3 h |
| Interactions | Slicers / field parameter for segments, what-if calculator and cart recovery, % ↔ users switch, bookmarks for caveats, page navigator | 1.5 h |
| QA against the dashboard | The guide's checklist, every figure from the same run | 1 h |
| Publish screenshots and a PDF; README section | Public-sample data only; the `.pbix` stays out of the repo | 0.75 h |

**Effort:** 10–11 h remaining | **Impact:** Medium | **Risk:** Medium | **Prerequisite:** E4, a Windows environment

> ⚠️ **Data route:** Power BI's BigQuery connector cannot cap bytes billed, and a live
> connection would re-implement nothing but also guard nothing. The recommended route
> imports files written by `powerbi_export.py` under the existing cap; the connector
> route (`--sql-only`) is documented for live refreshes, with its cost stated.

**Risk:** Power BI Desktop needs Windows; on Apple silicon that means a Windows 11 for ARM
VM running Power BI under emulation — check Microsoft's current requirements first.

**Expected outcome:** a multi-page report whose figures match the dashboard's, with
screenshots and a PDF export linked from the README.

---

### E8 · Scheduled dashboard regeneration in CI

**Status:** not started. GitHub Actions already runs lint and the test suite on every
push (no BigQuery, no secrets); E8 is the scheduled BigQuery run on top of that.

**Goal:** A GitHub Actions workflow that regenerates `dashboard.html` on a
schedule using BigQuery credentials stored as a GitHub secret, then commits it
to `main`, which GitHub Pages serves (E5).

**Why it matters:** At the moment, `dashboard.html` goes stale as soon as the
underlying dataset or queries change. An automated pipeline would keep the live
dashboard current with no manual step — and demonstrates awareness of data
engineering practices (automation, secret management, idempotent pipelines).

**Subtasks:**

| Step | What changes | Estimate |
|---|---|---|
| Create `.github/workflows/regenerate_dashboard.yml` | New file: triggers on a weekly schedule; installs `requirements-lock.txt`, runs `python generate_dashboard.py` under the cost cap, commits `dashboard.html` to `main` | 1.5 h |
| Store GCP service account key as a GitHub Actions secret | `Settings → Secrets → GOOGLE_APPLICATION_CREDENTIALS_JSON`; configure workflow to write it to a temp file and set `GOOGLE_APPLICATION_CREDENTIALS` env var | 1 h |
| Store `ANTHROPIC_API_KEY` or `GEMINI_API_KEY` as a second secret | Allows the brief to regenerate in CI | 0.5 h |
| Test the workflow on a branch before merging | Validate that the bot commit touches only `dashboard.html` | 1 h |

**Effort:** 3–5 h | **Impact:** Low | **Risk:** High | **Prerequisite:** E4, E5

> ⚠️ **Architecture note:** this requires committing a service account key as a
> GitHub Actions secret — a meaningful change in the project's security posture.
> The ADC-based local setup deliberately avoids key files; CI cannot use ADC and
> needs a service account. The key must be scoped to BigQuery read-only on the
> public dataset only. Document this change explicitly if implemented.

**Risk:** Billing — even against a public dataset, a CI run on every push adds
BigQuery costs. Limit the workflow trigger to `schedule` (weekly), not every push.

**Expected outcome:** a second badge in the README for the last successful
regeneration, and a live dashboard that updates weekly without a manual step.

---

### E9 · Landing-page diagnosis: page, stock or tracking?

**Goal:** For the four landing pages behind Recommendation 1 (Apparel listing, the Dino Game
Tee page, `/store.html`, Drinkware), decide which of three causes explains the low
saw-a-product rate.

**Why it matters:** the fix differs completely — a page that fails to load products, a
product that is unavailable, or a `view_item` tag that does not fire. Only the first two
change orders; a tracking fix only changes the measured rate.

**Approach:** per page and per week, compare `view_item` against other events that need
products on screen (`select_item`, `view_item_list`, `add_to_cart` from the page), engagement
time and scroll; check whether the low rate starts on a date (a release or a stock-out) or is
constant; compare the Dino Game Tee page's sessions with and without a `view_item`.

**Effort:** 2–3 h | **Impact:** High | **Risk:** Low | **Prerequisite:** —

**Expected outcome:** each page labelled page, stock or tracking, and Recommendation 1's
action and sizing updated to match.

**Status:** Implemented 2026-11-21 — `queries.landing_diagnosis_sql` (one pass; visitors who land on
a page vs those who reach it from inside, week by week) and `metrics.diagnose_landing_page` (tracking /
page / entry traffic, with stated thresholds), notebook Section 3.4, dashboard Recommendation 1, LLM
context v5. Result: none of the four is a stock problem. The Apparel and Drinkware listings and
`store.html` are **entry traffic** — they show products to 25.7–35.2% of visitors who reach them from
inside the site, but landing visitors leave at once and 62% carry no channel tag. The Dino Game Tee
page is **tracking**: `view_item` was silent from 30 Nov 2020 to 17 Jan 2021 (0.7% of visits vs 51.6%)
while scrolling held, and resumed on 18 Jan with behaviour unchanged. It is now left out of the
sizing (~253 orders, $9.0k–$18.0k, down from ~316 and $11.2k–$22.5k). Open: what sends the untagged
landing traffic — campaign or partner links without UTM tags are the first suspect.

---

### E10 · Cross-session purchase paths

**Goal:** Account for the 2,127 purchasing sessions outside the sequential funnel — 2,000 of
them with no add-to-cart in the same session.

**Why it matters:** the session-scoped funnel undercounts buyers who cart in one visit and pay
in another. A user-scoped view (cart in session N, purchase in session N+k) shows how large
that path is and how long it takes, and tests the cart-recovery recommendation directly.

**Effort:** 3–4 h | **Impact:** Medium | **Risk:** Low | **Prerequisite:** —

---

### E11 · Geography segment

**Goal:** Add `geo.country` to the funnel segment menu and the channel-style spread test.

**Why it matters:** a cheap check of whether the discovery loss is uniform across markets,
reusing the existing funnel builder and significance helpers.

**Effort:** 2–3 h | **Impact:** Medium | **Risk:** Low | **Prerequisite:** —

---

### E12 · Period-over-period comparison

**Goal:** Run the same analysis on two windows and report what changed, with intervals on the
differences; pass both periods to the LLM so the brief describes change, not a snapshot.

**Why it matters:** every finding today is a single snapshot; a trend is what a product team
acts on. The public sample covers only three months, so this needs a second export or a split
of the window.

**Effort:** 4–6 h | **Impact:** Medium | **Risk:** Medium | **Prerequisite:** a second data window

---

## Out of scope

These items were explicitly considered and decided against. The boundary is
recorded here so it is deliberate rather than an oversight.

| Decision | Reason |
|---|---|
| Service account key file checked into the repo | Decided to use Application Default Credentials (`gcloud auth application-default login`) for all local runs. A key file introduces unnecessary credential management risk for a personal portfolio project. The `google_creds.json` file is gitignored and will never be committed. |
| `product_brief_ga4_funnel.md` tracked in git | Was committed initially so the dashboard would render without an API key. Reversed as part of the convention audit — it is generated output and is now gitignored. The dashboard falls back gracefully if the file is absent. |
| Career or application-specific content on GitHub | CV and job-application material is kept outside this repository. The repo documents the analysis only. |
| Proprietary data as sample data | Only Google's public GA4 sample is used. No real company data is committed. |
| Session recording or heatmap analysis | Recommended in the AI brief as a qualitative follow-on ("session recordings show why users drop"). Not implementable within this project — it requires a separate tool (Hotjar, FullStory) connected to a live site, not a BigQuery dataset. Documented as a recommended next step for a practitioner, not a project deliverable. |
| OpenAI or other LLM providers in the notebook | Claude (Anthropic) and Gemini (Google, free tier) are the two supported options. Adding a third provider would lengthen the setup instructions without adding analysis value — the brief output is equivalent regardless of model. |
| Real-time streaming pipeline | The GA4 export to BigQuery is batch (daily). A streaming pipeline would require Pub/Sub and a different event schema. Out of scope for a portfolio project demonstrating analytical SQL, not data engineering. |

---

*Last updated: 2026-11-21 · Project status: active — E1–E6 and E9 done; E10 next*
