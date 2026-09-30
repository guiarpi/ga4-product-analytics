# Building the GA4 Funnel Dashboard in Power BI

A step-by-step guide to rebuilding this analysis as a Power BI report, connected
live to BigQuery, running on a Windows VM under VMware Fusion on macOS.

**What this demonstrates to a hiring manager:** that you can push heavy
sequential logic down into warehouse SQL, model at the right grain, express
business logic as DAX measures rather than pre-baked columns, and design a report
someone can actually interrogate — not just a set of static charts.

---

## Table of contents

1. [Environment setup](#1-environment-setup)
2. [Connecting Power BI to BigQuery](#2-connecting-power-bi-to-bigquery)
3. [The extraction SQL](#3-the-extraction-sql)
4. [Building the data model](#4-building-the-data-model)
5. [DAX measures](#5-dax-measures)
6. [Page 1 — Executive overview](#6-page-1--executive-overview)
7. [Page 2 — Funnel detail](#7-page-2--funnel-detail)
8. [Page 3 — Cohort retention](#8-page-3--cohort-retention)
9. [Page 4 — Opportunity sizing](#9-page-4--opportunity-sizing)
10. [Drill-through and tooltips](#10-drill-through-and-tooltips)
11. [Theme and formatting](#11-theme-and-formatting)
12. [QA checklist](#12-qa-checklist)
13. [Publishing and portfolio packaging](#13-publishing-and-portfolio-packaging)
14. [Power BI interview talking points](#14-power-bi-interview-talking-points)

---

## 1 · Environment setup

Power BI Desktop is Windows-only — there is no Mac build, which is why the VM
exists.

### VM specification

Power BI is memory-hungry. Under-provisioning is the most common reason people
conclude "Power BI is slow on a Mac."

| Resource | Minimum | Recommended |
|---|---|---|
| RAM | 8 GB | **16 GB** |
| vCPUs | 2 | **4** |
| Disk | 60 GB | 80 GB+ |
| Windows | 10 | **11** |

On Apple Silicon you need **Windows 11 ARM**; VMware Fusion (free for personal
use) supports this. Power BI Desktop runs under Windows' x64 emulation — slower
to launch, fine in use.

### Install Power BI Desktop

Prefer the **Microsoft Store** version — it auto-updates, which matters because
the BigQuery connector improves regularly.

> Microsoft Store → search "Power BI Desktop" → Install

The standalone MSI from Microsoft's download centre works too if the Store is
unavailable in your Windows image.

### Sign-in

Power BI Desktop needs a **work or school account** to publish. A personal
Outlook/Gmail address will not work for the Service. If you don't have one, sign
up for a Microsoft 365 Developer account or use a custom domain — but note you
can build the entire report locally and export to PDF without ever signing in.

---

## 2 · Connecting Power BI to BigQuery

### Establish the connection

1. **Home → Get Data → More → Database → Google BigQuery**
2. Expand **Advanced options** before clicking OK
3. Set **Billing Project ID** to your own GCP project ID

> **This step is essential and easy to miss.** The GA4 sample lives in
> `bigquery-public-data`, which you can read but cannot bill against. Without a
> billing project the connector fails with a permissions error that doesn't
> mention billing at all.

4. Click **OK**, then **Sign in** and complete Google OAuth in the browser
5. Choose **Import** (not DirectQuery)

### Why Import, not DirectQuery

DirectQuery against `events_*` wildcard tables re-scans partitions on every
visual interaction — slow and expensive. Import runs the SQL once, compresses the
result into the VertiPaq engine, and every subsequent interaction is in-memory.

Say this out loud in an interview if Power BI comes up. It's a cost-and-
architecture judgment, and knowing *when* DirectQuery is wrong signals more than
knowing it exists.

### Feeding it custom SQL

Do **not** navigate the table tree and let Power Query pull raw events. That
imports hundreds of millions of rows of nested event data.

Instead, in the same **Advanced options** panel, paste your query into the
**SQL statement** box. One query per table you want in the model.

Alternatively, use **Transform Data → Advanced Editor**:

```m
let
    Source = Value.NativeQuery(
        GoogleBigQuery.Database([BillingProject="YOUR_PROJECT_ID"]),
        "-- paste SQL here --",
        null,
        [EnableFolding=false]
    )
in
    Source
```

---

## 3 · The extraction SQL

Three queries build the model. Each keeps the partition pruning
(`_TABLE_SUFFIX BETWEEN`) that keeps the bill near zero.

### 3.1 · `fct_sessions` — one row per session

This is the core design decision. Rather than importing pre-aggregated funnel
counts, extract at **session grain** with boolean step flags. Power BI then
computes every rate as a DAX measure, so slicers work across the whole report.
~355,000 rows — trivial for the VertiPaq engine.

```sql
WITH base AS (
  SELECT
    user_pseudo_id,
    (SELECT ep.value.int_value FROM UNNEST(event_params) ep
     WHERE ep.key = 'ga_session_id' LIMIT 1)          AS session_id,
    device.category                                    AS device_category,
    traffic_source.medium                              AS traffic_medium,
    traffic_source.source                              AS traffic_source_name,
    event_name,
    event_timestamp,
    event_date,
    ecommerce.purchase_revenue                         AS purchase_revenue
  FROM `bigquery-public-data.ga4_obfuscated_sample_ecommerce.events_*`
  WHERE _TABLE_SUFFIX BETWEEN '20201101' AND '20210131'
    AND event_name IN ('session_start','view_item','add_to_cart',
                       'begin_checkout','purchase')
),
se AS (
  SELECT
    user_pseudo_id, session_id, event_name,
    ANY_VALUE(device_category)     AS device_category,
    ANY_VALUE(traffic_medium)      AS traffic_medium,
    ANY_VALUE(traffic_source_name) AS traffic_source_name,
    MIN(event_timestamp)           AS first_ts,
    MIN(event_date)                AS first_date,
    SUM(purchase_revenue)          AS rev
  FROM base
  WHERE session_id IS NOT NULL
  GROUP BY user_pseudo_id, session_id, event_name
),
pv AS (
  SELECT
    user_pseudo_id, session_id,
    ANY_VALUE(device_category)     AS device_category,
    ANY_VALUE(traffic_medium)      AS traffic_medium,
    ANY_VALUE(traffic_source_name) AS traffic_source_name,
    MIN(first_date)                AS session_date_raw,
    MAX(CASE WHEN event_name='session_start'  THEN first_ts END) AS ts_s,
    MAX(CASE WHEN event_name='view_item'      THEN first_ts END) AS ts_v,
    MAX(CASE WHEN event_name='add_to_cart'    THEN first_ts END) AS ts_c,
    MAX(CASE WHEN event_name='begin_checkout' THEN first_ts END) AS ts_k,
    MAX(CASE WHEN event_name='purchase'       THEN first_ts END) AS ts_p,
    MAX(CASE WHEN event_name='purchase'       THEN rev     END) AS revenue
  FROM se
  GROUP BY user_pseudo_id, session_id
)
SELECT
  CONCAT(user_pseudo_id, '-', CAST(session_id AS STRING))   AS session_key,
  user_pseudo_id,
  PARSE_DATE('%Y%m%d', session_date_raw)                    AS session_date,
  device_category,
  IFNULL(traffic_medium, '(none)')                          AS traffic_medium,
  IFNULL(traffic_source_name, '(direct)')                   AS traffic_source,
  ts_v IS NOT NULL AND ts_v >= ts_s                         AS viewed_item,
  ts_c IS NOT NULL AND ts_c >= ts_v AND ts_v >= ts_s        AS added_to_cart,
  ts_k IS NOT NULL AND ts_k >= ts_c AND ts_c >= ts_v
                   AND ts_v >= ts_s                         AS began_checkout,
  ts_p IS NOT NULL AND ts_p >= ts_k AND ts_k >= ts_c
                   AND ts_c >= ts_v AND ts_v >= ts_s        AS purchased,
  IFNULL(revenue, 0)                                        AS revenue,
  SAFE_DIVIDE(ts_p - ts_s, 3600000000.0)                    AS hours_to_purchase
FROM pv
WHERE ts_s IS NOT NULL
```

> **The sequence logic stays in SQL deliberately.** Comparing five timestamps per
> session is exactly what a columnar warehouse is good at and exactly what DAX is
> bad at. The boolean flags cross into Power BI already trustworthy — the
> semantic layer never has to re-derive event ordering.

### 3.2 · `dim_users` — cohort assignment

```sql
SELECT
  user_pseudo_id,
  MIN(PARSE_DATE('%Y%m%d', event_date))                                  AS first_session_date,
  DATE_TRUNC(MIN(PARSE_DATE('%Y%m%d', event_date)), WEEK(MONDAY))        AS cohort_week
FROM `bigquery-public-data.ga4_obfuscated_sample_ecommerce.events_*`
WHERE _TABLE_SUFFIX BETWEEN '20201101' AND '20210131'
  AND event_name = 'session_start'
GROUP BY user_pseudo_id
```

### 3.3 · `fct_user_activity` — weekly return activity

```sql
SELECT DISTINCT
  user_pseudo_id,
  DATE_TRUNC(PARSE_DATE('%Y%m%d', event_date), WEEK(MONDAY)) AS activity_week
FROM `bigquery-public-data.ga4_obfuscated_sample_ecommerce.events_*`
WHERE _TABLE_SUFFIX BETWEEN '20201101' AND '20210131'
  AND event_name = 'session_start'
```

---

## 4 · Building the data model

### Add a date dimension

**Modeling → New table:**

```dax
dim_date =
VAR MinDate = MIN( fct_sessions[session_date] )
VAR MaxDate = MAX( fct_sessions[session_date] )
RETURN
ADDCOLUMNS(
    CALENDAR( MinDate, MaxDate ),
    "Year",        YEAR([Date]),
    "Month",       FORMAT([Date], "mmm yyyy"),
    "MonthSort",   YEAR([Date]) * 100 + MONTH([Date]),
    "Week",        WEEKNUM([Date], 2),
    "WeekStart",   [Date] - WEEKDAY([Date], 3),
    "DayOfWeek",   FORMAT([Date], "ddd"),
    "DayOfWeekNo", WEEKDAY([Date], 2)
)
```

Then **Table tools → Mark as date table → Date**. Skipping this quietly breaks
every time-intelligence function.

Sort `Month` by `MonthSort` and `DayOfWeek` by `DayOfWeekNo`
(**Column tools → Sort by column**), or your axes will read alphabetically —
"Apr, Aug, Dec…"

### Add a weeks-since disconnected table

Cohort matrices need an axis that isn't related to anything:

```dax
dim_weeks_since = SELECTCOLUMNS( GENERATESERIES(0, 13, 1), "WeeksSince", [Value] )
```

Leave it **unrelated** to every other table. It exists purely to give the matrix
columns a home, and the retention measure reads it with `SELECTEDVALUE`.

### Relationships

| From | To | Cardinality | Direction |
|---|---|---|---|
| `fct_sessions[session_date]` | `dim_date[Date]` | Many-to-one | Single |
| `fct_sessions[user_pseudo_id]` | `dim_users[user_pseudo_id]` | Many-to-one | Single |
| `fct_user_activity[user_pseudo_id]` | `dim_users[user_pseudo_id]` | Many-to-one | Single |

`dim_weeks_since` stays disconnected.

### Model hygiene

- Hide every raw key from report view (`session_key`, `user_pseudo_id`)
- Hide the `MonthSort` and `DayOfWeekNo` helper columns
- Put all measures in a dedicated `_Measures` table
  (**Home → Enter Data**, name it `_Measures`, delete the placeholder column)
- Set `revenue` to Currency, `hours_to_purchase` to Decimal

The measures table with a leading underscore sorts to the top of the field list.
Small thing; reviewers who use Power BI daily notice it.

---

## 5 · DAX measures

### Core counts

```dax
Sessions = COUNTROWS( fct_sessions )

Unique Users = DISTINCTCOUNT( fct_sessions[user_pseudo_id] )

Sessions Viewed Item   = CALCULATE( [Sessions], fct_sessions[viewed_item]    = TRUE() )
Sessions Added to Cart = CALCULATE( [Sessions], fct_sessions[added_to_cart]  = TRUE() )
Sessions Checkout      = CALCULATE( [Sessions], fct_sessions[began_checkout] = TRUE() )
Sessions Purchased     = CALCULATE( [Sessions], fct_sessions[purchased]      = TRUE() )
```

### Conversion rates

```dax
Session to Purchase % = DIVIDE( [Sessions Purchased], [Sessions] )

View Item Rate % = DIVIDE( [Sessions Viewed Item], [Sessions] )

-- Add-to-cart among product viewers (the meaningful denominator)
Cart Rate of Viewers % = DIVIDE( [Sessions Added to Cart], [Sessions Viewed Item] )

-- Add-to-cart across all sessions (the headline denominator)
Cart Rate of All % = DIVIDE( [Sessions Added to Cart], [Sessions] )
```

> Both cart-rate measures exist on purpose. Reporting only the first overstates
> performance; only the second hides that the product page converts fine. The gap
> between them *is* the discovery problem — keep them adjacent on the page so the
> comparison is unavoidable.

### Revenue

```dax
Total Revenue = SUM( fct_sessions[revenue] )
AOV           = DIVIDE( [Total Revenue], [Sessions Purchased] )
Median Order  = MEDIANX( FILTER( fct_sessions, fct_sessions[purchased] = TRUE() ),
                         fct_sessions[revenue] )
Median Hours to Purchase =
    MEDIANX( FILTER( fct_sessions, fct_sessions[purchased] = TRUE() ),
             fct_sessions[hours_to_purchase] )
```

### Funnel as a measure-driven visual

Create a step dimension (**Enter Data**):

| StepOrder | StepName |
|---|---|
| 1 | Session Start |
| 2 | View Item |
| 3 | Add to Cart |
| 4 | Begin Checkout |
| 5 | Purchase |

```dax
Funnel Sessions =
SWITCH(
    SELECTEDVALUE( dim_funnel_step[StepOrder] ),
    1, [Sessions],
    2, [Sessions Viewed Item],
    3, [Sessions Added to Cart],
    4, [Sessions Checkout],
    5, [Sessions Purchased]
)

Funnel % of Start = DIVIDE( [Funnel Sessions], [Sessions] )

Funnel % of Previous =
VAR CurrentStep = SELECTEDVALUE( dim_funnel_step[StepOrder] )
VAR PrevValue =
    CALCULATE( [Funnel Sessions],
        ALL( dim_funnel_step ),
        dim_funnel_step[StepOrder] = CurrentStep - 1 )
RETURN IF( CurrentStep = 1, BLANK(), DIVIDE( [Funnel Sessions], PrevValue ) )
```

Leave `dim_funnel_step` disconnected — `SWITCH` reads it directly.

### Retention, with the censoring guard

This is the measure worth being able to explain line by line.

```dax
Cohort Size = DISTINCTCOUNT( dim_users[user_pseudo_id] )

Retention % =
VAR CohortStart = SELECTEDVALUE( dim_users[cohort_week] )
VAR Offset      = SELECTEDVALUE( dim_weeks_since[WeeksSince] )
VAR WindowEnd   = MAX( dim_date[Date] )
VAR TargetWeek  = CohortStart + ( Offset * 7 )

-- A cohort's week N is only observable if that entire week fits in the window.
VAR IsObserved  = ( TargetWeek + 6 ) <= WindowEnd

VAR Returned =
    CALCULATE(
        DISTINCTCOUNT( fct_user_activity[user_pseudo_id] ),
        fct_user_activity[activity_week] = TargetWeek
    )
RETURN
    IF( IsObserved, DIVIDE( Returned, [Cohort Size] ), BLANK() )
```

**Why `BLANK()` and not `0`.** A user who joined in the final week has not had
the *opportunity* to return. Returning 0 would report "hasn't happened yet" as
"did not retain" — in this dataset that fabricates results in **46% of the
matrix cells** and makes the retention trend look far steeper than it is. `BLANK()`
renders as an empty cell, which is the honest answer.

This single measure is the strongest thing in the report to talk about. It's a
correctness decision, not a formatting one.

### Excluding the partial first cohort

Cohort buckets are Monday-anchored, but the data window opens **Sunday 1 Nov
2020**. The `2020-10-26` bucket therefore holds a single day of users — 2,339
against 19,194 in the first full cohort — and its inflated retention is
small-sample noise, not a behavioural signal.

```dax
Is Full Cohort =
VAR CohortStart  = SELECTEDVALUE( dim_users[cohort_week] )
VAR WindowStart  = MIN( dim_date[Date] )
RETURN IF( CohortStart >= WindowStart, 1, 0 )

Avg Week 1 Retention =
AVERAGEX(
    FILTER( VALUES( dim_users[cohort_week] ), [Is Full Cohort] = 1 ),
    CALCULATE( [Retention %], dim_weeks_since[WeeksSince] = 1 )
)
```

---

## 6 · Page 1 — Executive overview

Layout on a 1280×720 canvas:

```
┌──────────────────────────────────────────────────────────┐
│  GA4 E-Commerce Funnel Analysis      [date slicer]       │
├──────────┬──────────┬──────────┬──────────┬──────────────┤
│ Sessions │ Users    │ Conv %   │ AOV      │ Wk-1 Retain  │
│ 354.9K   │ 267.1K   │ 0.80%    │ $79      │ 4.2%    ⚠    │
├──────────────────────────────┬───────────────────────────┤
│  Funnel (bar, % of start)    │  Key findings (text)      │
│                              │                           │
├──────────────────────────────┴───────────────────────────┤
│  Sessions & conversion trend over time (line + column)   │
└──────────────────────────────────────────────────────────┘
```

**Build order:**

1. **Cards** — five KPI cards across the top. Format → Callout value → set
   decimal places explicitly; Power BI's auto-formatting will show `0.8%` as
   `1%` at small card sizes.
2. **Funnel** — use a **stacked bar chart**, not the built-in Funnel visual. The
   native funnel visual can't easily show two different percentage bases at once.
   Axis: `dim_funnel_step[StepName]`. Values: `[Funnel Sessions]`.
   Add `[Funnel % of Start]` and `[Funnel % of Previous]` as data labels or
   tooltip fields.
3. **Trend** — line and stacked column combo: columns = `[Sessions]` by
   `dim_date[Date]`, line = `[Session to Purchase %]` on the secondary axis.
4. **Date slicer** — `dim_date[Date]`, Between style.

### Conditional formatting on the retention card

Make the warning automatic rather than typed:

```dax
Retention Status Colour =
IF( [Avg Week 1 Retention] < 0.20, "#DC2626", "#059669" )
```

Card → Format → Callout value → Colour → **fx** → Field value →
`Retention Status Colour`.

---

## 7 · Page 2 — Funnel detail

This page is where slicers earn their place.

**Visuals:**

- Funnel bar chart (as page 1, larger)
- **Matrix**: rows `device_category`, values `[Sessions]`,
  `[View Item Rate %]`, `[Cart Rate of All %]`, `[Session to Purchase %]`
- **Matrix**: rows `traffic_medium`, same measures
- Slicers: `device_category`, `traffic_medium`, `dim_date[Date]`

### The device result — present it as a table

Purchase rates differ by roughly **0.08 percentage points** across desktop,
mobile and tablet. Three near-identical bars in a chart invite the reader to hunt
for a difference that isn't there; a matrix states it plainly.

Add a text box beside it:

> **Device is not the lever.** Purchase conversion differs by <0.1pp across
> devices. A mobile-first redesign — the intuitive response to a 0.8% conversion
> rate — would not have moved the number.

Reporting a null result clearly is a signal of analytical maturity. Don't bury it.

### Traffic source — the genuinely new analysis

This is what Power BI adds that the static HTML dashboard never did. Slice the
funnel by `traffic_medium` and the top-of-funnel drop stops being one number and
becomes a distribution.

Sort the matrix by `[View Item Rate %]` ascending. If the 79% top-of-funnel loss
is concentrated in two or three paid mediums, it's a targeting and landing-page
problem owned by acquisition. If it's flat across all mediums, it's site
navigation owned by product. Those are different teams and different budgets.

Have that sentence ready — it's the natural answer to "what would you do next?"

---

## 8 · Page 3 — Cohort retention

**The visual is a Matrix**, formatted as a heatmap:

- **Rows:** `dim_users[cohort_week]`
- **Columns:** `dim_weeks_since[WeeksSince]`
- **Values:** `[Retention %]`

**Format → Cell elements → Background color → On → fx:**

| Setting | Value |
|---|---|
| Format style | Gradient |
| Apply to | `Retention %` |
| Minimum | Number `0` → light `#F7FBFF` |
| Maximum | Number `0.12` → dark `#08306B` |

Two details that matter:

- **Darker must mean better retention.** Getting this backwards is a common and
  very visible mistake — it makes your worst region look like your best.
- **Cap the maximum at 0.12, not "highest value."** Week 0 is 100% for every
  cohort by definition. Let it set the scale and every other cell collapses into
  one indistinguishable shade.

Blank cells (censored weeks) receive no fill automatically — that's the intended
result, and it's what the `BLANK()` in the retention measure buys you.

Add a text box documenting both boundary effects:

> **Reading this matrix.** Blank cells are weeks that hadn't occurred yet when
> the data window closed — not zero retention. The `2020-10-26` cohort is a
> single day of users (the window opens mid-week), so it's excluded from the
> averages rather than explained.

---

## 9 · Page 4 — Opportunity sizing

Power BI's what-if parameter turns a static claim into something a stakeholder
can interrogate. This is the page that makes the report feel like a tool.

### Create the parameters

**Modeling → New parameter → Numeric range:**

| Parameter | Min | Max | Increment | Default |
|---|---|---|---|---|
| `Discovery Lift pp` | 0 | 20 | 1 | 5 |
| `Conversion Haircut %` | 0 | 100 | 5 | 100 |

Both create a slicer and a `<name> Value` measure automatically.

### The measures

```dax
Incremental Viewers = [Sessions] * ( 'Discovery Lift pp'[Discovery Lift pp Value] / 100 )

View to Purchase Rate = DIVIDE( [Sessions Purchased], [Sessions Viewed Item] )

Incremental Orders =
    [Incremental Viewers]
  * [View to Purchase Rate]
  * ( 'Conversion Haircut %'[Conversion Haircut % Value] / 100 )

Incremental Revenue = [Incremental Orders] * [AOV]
```

### Why the haircut parameter exists

The naive projection assumes incremental sessions convert exactly like today's
traffic. They won't — marginal traffic is nearly always lower intent, so the
unadjusted figure is an optimistic upper bound.

Exposing the haircut as a slider does two things. It makes the assumption
visible instead of buried, and it lets a stakeholder pressure-test the number
themselves rather than taking your word for it.

Put a text box under the slicers:

> At 100% the model assumes incremental traffic converts like existing traffic —
> an upper bound. Planning range: 50–75%.

If an interviewer asks one question about this report, it will probably be about
this assumption. Having modelled it explicitly is a much stronger position than
defending a single number.

---

## 10 · Drill-through and tooltips

### Drill-through page

1. New page, rename **"Cohort Detail"**, set **Page information → Hide page**
2. **Visualizations → Drill through → Add** `dim_users[cohort_week]`
3. Build the page: cohort size card, retention curve line chart
   (`[Retention %]` by `WeeksSince`), device split matrix

Right-clicking any cohort row now jumps here filtered to that cohort. Power BI
adds the back arrow automatically.

### Report page tooltip

1. New page → **Page information → Allow use as tooltip → On**
2. **Canvas settings → Type: Tooltip**
3. Add a small funnel visual
4. On page 1, select the trend chart → **Format → Tooltip → Type: Report page →**
   pick your tooltip page

Hovering any day in the trend now shows that day's full funnel. Cheap to build,
and it reads as a level above default tooltips.

---

## 11 · Theme and formatting

Save as `ga4-theme.json` and load via **View → Themes → Browse for themes**:

```json
{
  "name": "GA4 Funnel Analysis",
  "dataColors": ["#2563EB","#4C78A8","#F58518","#54A24B","#DC2626",
                 "#7C3AED","#059669","#EA580C"],
  "background": "#FFFFFF",
  "foreground": "#374151",
  "tableAccent": "#2563EB",
  "visualStyles": {
    "*": {
      "*": {
        "title": [{ "fontSize": 12, "fontFamily": "Segoe UI Semibold",
                    "color": { "solid": { "color": "#111827" } } }],
        "background": [{ "show": true,
                         "color": { "solid": { "color": "#FFFFFF" } } }],
        "border": [{ "show": true,
                     "color": { "solid": { "color": "#E5E7EB" } },
                     "radius": 8 }]
      }
    }
  }
}
```

**Formatting rules worth following:**

- Set percentage measures to a fixed decimal count in **Model view**, not per
  visual — one place, applied everywhere
- Turn off gridlines on bar charts; keep them faint on line charts
- Left-align text, right-align numbers in every matrix
- Use one accent colour. Multi-colour categorical palettes on a single-series
  chart encode nothing
- Align visuals with **Format → Align**, not by eye

---

## 12 · QA checklist

Before exporting or publishing:

**Numbers**

- [ ] Total sessions matches the Python dashboard (**354,857**)
- [ ] Session-to-purchase matches (**0.80%**)
- [ ] AOV matches (**$79**, median $55)
- [ ] Avg Week-1 retention matches (**4.2%**, full cohorts only)
- [ ] Device purchase-rate spread matches (**~0.08pp**)

> Cross-checking Power BI against the Python pipeline is the single most valuable
> QA step available to you. Two independent implementations agreeing is real
> evidence; one implementation agreeing with itself is not.

**Model**

- [ ] `dim_date` is marked as a date table
- [ ] Month and day-of-week columns sort by their numeric helpers
- [ ] No bidirectional relationships (none are needed here)
- [ ] Raw keys and helper columns hidden from report view
- [ ] Every measure lives in `_Measures`

**Retention matrix**

- [ ] Censored cells are **blank**, not `0%`
- [ ] Darker = better retention
- [ ] Colour scale capped at 12%, not "highest value"
- [ ] Partial first cohort excluded from averages and annotated

**Interaction**

- [ ] Every slicer affects the visuals it should — and only those
- [ ] Drill-through works from cohort rows and returns cleanly
- [ ] What-if sliders update all downstream cards
- [ ] Nothing shows `(Blank)` where a zero is meant

**Presentation**

- [ ] Page tab names are readable ("Overview", not "Page 1")
- [ ] Every page has a title
- [ ] Consistent fonts and colours throughout
- [ ] Renders correctly at 1280×720 (**View → Page view → Fit to page**)

---

## 13 · Publishing and portfolio packaging

### The problem

A `.pbix` file is useless to a reviewer on a Mac — they can't open it. Assume
your audience cannot run Power BI at all, and package accordingly.

### Layered approach — do all four

**1 · Commit the `.pbix`** so the work is inspectable by anyone who does have
Power BI:

```bash
mkdir -p powerbi
# copy GA4_Funnel_Analysis.pbix from the VM into powerbi/
```

Move it out of the VM via a VMware shared folder, or drag-and-drop if VMware
Tools is installed.

**2 · Export a PDF** — **File → Export → Export to PDF**. This is the reliable
universal artefact. Commit it as `powerbi/GA4_Funnel_Analysis.pdf`.

**3 · Screenshot each page** to `assets/` and embed in the README. Same argument
as before: reviewers look at images, they don't open files.

**4 · Publish to the Power BI Service** if you have a work or school account:
**Home → Publish → My workspace.**

> **On "Publish to web":** this generates a public embed link, but it depends on
> your tenant's admin settings and licensing, and it makes the report **fully
> public** — anyone with the URL sees the data. It's fine for this public GA4
> sample; never do it with real company data. If it isn't available to you, the
> PDF plus screenshots cover you completely.

### README section

```markdown
## Power BI version

The same analysis rebuilt as an interactive Power BI report — DAX measures,
cross-filtering slicers, drill-through cohort detail, and a what-if parameter
for opportunity sizing.

![Power BI overview](assets/powerbi-overview.png)

- [`powerbi/GA4_Funnel_Analysis.pbix`](powerbi/) — open in Power BI Desktop
- [`powerbi/GA4_Funnel_Analysis.pdf`](powerbi/) — static export, no Power BI needed

Connected directly to BigQuery via the native connector in Import mode, with the
sequential funnel logic pushed down into SQL and all rates expressed as DAX
measures over a session-grain fact table.
```

---

## 14 · Power BI interview talking points

**"Why did you model at session grain instead of importing the aggregates?"**
> Because aggregates freeze the questions you're allowed to ask. At session grain
> with boolean step flags, every rate is a DAX measure, so a slicer on traffic
> medium or device recomputes the entire funnel for free. It's 355,000 rows —
> nothing for VertiPaq. If it were 500 million I'd aggregate in BigQuery and
> accept the loss of flexibility, but not at this size.

**"Why Import rather than DirectQuery?"**
> DirectQuery would re-scan wildcard partitions on every visual interaction —
> slow for the user and billable every time. The data is a fixed historical
> window, so there's nothing to gain from live queries. DirectQuery earns its
> place when data freshness matters more than interaction speed; here it's the
> opposite.

**"Why is the sequence logic in SQL rather than DAX?"**
> Comparing five timestamps per session across 355,000 sessions is exactly what a
> columnar warehouse is built for and exactly what DAX handles badly. Doing it in
> SQL also means the flags arrive in the model already trustworthy — the semantic
> layer never re-derives event ordering, so there's one definition of "reached
> checkout" rather than two that can drift apart.

**"What's the hardest thing in this report?"**
> The retention measure. Naively it's returned users over cohort size, but a
> cohort from the final week hasn't *had* the chance to return — so a zero there
> is fabricated. In this dataset that's 46% of the matrix cells. The measure
> checks whether each cohort-week actually fits inside the observation window and
> returns `BLANK()` when it doesn't. Same reasoning excludes the first cohort,
> which is one day of users because the window opens mid-week.

**"How do you know the numbers are right?"**
> I built the same analysis twice — once as a Python and BigQuery pipeline
> producing a static HTML dashboard, once in Power BI — and reconciled them.
> Sessions, conversion rate, AOV and retention agree across both. Two independent
> implementations landing on the same number is meaningful evidence; one
> implementation agreeing with itself isn't.

**"What would you add next?"**
> Row-level security if this were multi-tenant, incremental refresh so it doesn't
> re-import the full window nightly, and a proper date-comparison layer —
> period-over-period measures so the report shows movement rather than a
> snapshot. Right now it answers "what is the funnel"; it should answer "what
> changed and why".

---

## Appendix — VM-specific troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| BigQuery sign-in loop | VM browser can't complete OAuth redirect | Use Edge inside the VM, not a shared-clipboard paste from macOS |
| "Access Denied: Project" | Billing Project ID not set | Advanced options → Billing Project ID → your own GCP project |
| Refresh runs for many minutes | Query returning raw events | Confirm your SQL is in the connector, not table navigation |
| Power BI won't start on Apple Silicon | x64 emulation not enabled | Windows 11 ARM required; verify VMware Tools installed |
| Visuals render slowly | VM under-provisioned | Raise to 16 GB RAM / 4 vCPU |
| Can't copy `.pbix` to macOS | No shared folder | VMware Fusion → Settings → Sharing → add a folder |

---

*Companion documents: `DASHBOARD_GUIDE.md` for the Python and HTML pipeline,
`README.md` for project overview.*
*Last updated: 25 August 2026*
