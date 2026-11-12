# Future Enhancements — GA4 Product Analytics

Roadmap for extending this project. Items are grounded in the specific gaps and
unanswered questions from the current analysis — not generic best practice.

**Current state:** the project covers a 5-step session-scoped funnel, revenue
metrics, device breakdown, time-to-convert, shoppers funnel, weekly cohort
retention, and an AI-generated product brief — all run against the GA4 public
e-commerce sample (Nov 2020 – Jan 2021).

---

## Priority overview

| # | Enhancement | Effort (hrs) | Impact | Risk | Prerequisite |
|---|---|---|---|---|---|
| E1 | Traffic source × funnel cross-tab ✅ done | 4–6 | High | Low | — |
| E2 | Product category funnel breakdown ✅ done | 5–7 | High | Low | — |
| E3 | Return-visitor vs new-visitor funnel | 4–5 | Medium | Low | — |
| E4 | Parameterised date range and config | 2–3 | Medium | Low | — |
| E5 | GitHub Pages live deployment ✅ done | 2–3 | Medium | Low | — |
| E6 | Statistical significance on device split | 3–4 | Medium | Low | — |
| E7 | Power BI dashboard build | 8–12 | Medium | High | E4 |
| E8 | GitHub Actions CI pipeline | 4–6 | Low | High | E4, E5 |

---

## Recommended sequence

**Phase 1 — Deepen the analysis (E1, E2, E3)**
These three are additive SQL queries and charts. They directly answer the
three "What to Measure Next" questions left open by the AI product brief, and
they require no architectural change. Do these first because they turn an
interesting project into a complete one — the current analysis diagnoses the
problem; these enhancements identify where to intervene.

**Phase 2 — Make it reusable and shareable (E4, E5)**
Once the analysis is complete, make the project easy to run against any dataset
and easy to share as a live link rather than a downloaded file. E4 (config)
is a prerequisite for E7 and E8, so address it before those.

**Phase 3 — Add credibility and rigour (E6)**
Statistical significance testing is a portfolio differentiator — it signals
analytical rigour rather than just descriptive reporting. It does not change
the findings (device variance is genuinely <0.1pp) but it formally closes that
question.

**Phase 4 — Platform extensions (E7, E8)**
Power BI and CI/CD are high-effort and carry platform dependencies or secrets
management complexity. Do them last, if at all, since their value is
marginal once the core analysis is strong.

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

**Status:** Implemented 2026-11-12 — notebook Sections 5c–5d, dashboard "Acquisition Channel" section, LLM context in 10c. First run: 8.7pp spread (cpc 21.6% → referral 30.3%), reported as borderline; 26.2% of sessions have no session-level medium and are disclosed separately.

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

**Status:** Implemented 2026-11-12 — notebook Sections 7c–7d (added as 7c rather than a new Section 8, so Section 10 references stay valid), dashboard "Product Category" section, LLM context in 10c. First run: 38 of 74 categories ranked, benchmark 24.6%. The run surfaced duplicate labels (`Bags` vs `Lifestyle/Bags/`), now normalised to the last path segment in SQL.

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
Update steps are in DASHBOARD_GUIDE.md section 5.

**Live:** https://guiarpi.github.io/ga4-product-analytics/

---

### E6 · Statistical significance on device split

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

---

### E7 · Power BI dashboard build

**Goal:** Implement the analysis as a Power BI report connected live to BigQuery,
following the existing `POWERBI_GUIDE.md` step-by-step guide.

**Why it matters:** `POWERBI_GUIDE.md` documents the full build — extraction SQL,
data model, DAX measures for the funnel, and all four report pages — but the
`.pbix` file has not been built yet. Completing it demonstrates BI tool
proficiency alongside the Python/SQL work, and it shows the
same analysis in the tool their team actually uses.

**Subtasks:**

| Step | What changes | Estimate |
|---|---|---|
| Provision a Windows VM or Parallels environment | External to this repo — documented in POWERBI_GUIDE.md section 1 | 1 h |
| Connect Power BI Desktop to BigQuery using the extraction SQL from the guide | POWERBI_GUIDE.md section 3; SQL adapts the existing `generate_dashboard.py` queries | 1.5 h |
| Build the data model (4 tables: funnel, device, time-to-convert, cohort) | POWERBI_GUIDE.md section 4 | 1 h |
| Write DAX measures (conversion rate, step drop, cumulative retention) | POWERBI_GUIDE.md section 5 | 2 h |
| Build the 4 report pages | POWERBI_GUIDE.md sections 6–9 | 2.5 h |
| Export as PDF and add screenshots to `_private/` | Not committed to the repo; shared as a portfolio PDF only | 1 h |
| Update README to reference the Power BI guide | Add a note in the Tech stack section | 0.5 h |

**Effort:** 8–12 h | **Impact:** Medium | **Risk:** High | **Prerequisite:** E4

> ⚠️ **Architecture note:** Power BI's BigQuery connector does not support
> `UNNEST` in DirectQuery mode — the session-funnel logic must be fully
> pre-computed in the extraction SQL (a single flat result set), not rebuilt in
> DAX. This is a meaningful design difference from the Python approach, where the
> nested GA4 schema is handled in BigQuery and results are passed to Python as flat
> DataFrames. The extraction SQL in POWERBI_GUIDE.md already accounts for this.

**Risk:** Requires a Windows environment (Parallels or a cloud VM). The BigQuery
connector version in Power BI Desktop has changed since the guide was written —
authentication steps may differ. Allow extra time for environment setup.

**Expected outcome:** A `.pbix` file (stored privately, not committed) and a
multi-page PDF export that can be shared alongside the GitHub repo link in
applications.

---

### E8 · GitHub Actions CI pipeline

**Goal:** A GitHub Actions workflow that regenerates `dashboard.html` on a
schedule (or on push to `main`) using BigQuery credentials stored as a GitHub
secret, then commits it to `main`, which GitHub Pages serves (E5).

**Why it matters:** At the moment, `dashboard.html` goes stale as soon as the
underlying dataset or queries change. An automated pipeline would keep the live
dashboard current with no manual step — and demonstrates awareness of data
engineering practices (automation, secret management, idempotent pipelines).

**Subtasks:**

| Step | What changes | Estimate |
|---|---|---|
| Create `.github/workflows/regenerate_dashboard.yml` | New file: triggers on schedule + push; runs `pip install -r requirements.txt`, `python generate_dashboard.py`, commits `dashboard.html` to `main` | 1.5 h |
| Store GCP service account key as a GitHub Actions secret | `Settings → Secrets → GOOGLE_APPLICATION_CREDENTIALS_JSON`; configure workflow to write it to a temp file and set `GOOGLE_APPLICATION_CREDENTIALS` env var | 1 h |
| Store `ANTHROPIC_API_KEY` or `GEMINI_API_KEY` as a second secret | Allows the brief to regenerate in CI | 0.5 h |
| Update `config.py` to read dates from environment variables | Enables the CI workflow to override dates if needed | 0.5 h |
| Test the workflow on a branch before merging | Validate that the bot commit touches only `dashboard.html` | 1 h |

**Effort:** 4–6 h | **Impact:** Low | **Risk:** High | **Prerequisite:** E4, E5

> ⚠️ **Architecture note:** this requires committing a service account key as a
> GitHub Actions secret — a meaningful change in the project's security posture.
> The ADC-based local setup deliberately avoids key files; CI cannot use ADC and
> needs a service account. The key must be scoped to BigQuery read-only on the
> public dataset only. Document this change explicitly if implemented.

**Risk:** Billing — even against a public dataset, a CI run on every push adds
BigQuery costs. Limit the workflow trigger to `schedule` (weekly), not every push.

**Expected outcome:** A GitHub Actions badge in the README showing the last
successful pipeline run, and a live dashboard that updates weekly without
any manual step.

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

*Last updated: 2026-11-12 · Project status: active — E1, E2 done*
