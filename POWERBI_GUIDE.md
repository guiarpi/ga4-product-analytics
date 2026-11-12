# Building the GA4 Funnel Dashboard in Power BI

A step-by-step guide to rebuilding this analysis as a Power BI report, connected
to BigQuery (Import mode), running on a Windows VM under VMware Fusion on macOS.

**Status:** planned, not yet built (roadmap E7). The SQL, measures and expected
numbers below are kept in line with the notebook and `dashboard.html` as of the
12 Nov 2026 run, including the acquisition-channel (E1) and product-category
(E2) analyses.

**What this build demonstrates:** that you can push heavy
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
8. [Page 3 — Product categories](#8-page-3--product-categories)
9. [Page 4 — Cohort retention](#9-page-4--cohort-retention)
10. [Page 5 — Opportunity sizing](#10-page-5--opportunity-sizing)
11. [Drill-through and tooltips](#11-drill-through-and-tooltips)
12. [Theme and formatting](#12-theme-and-formatting)
13. [QA checklist](#13-qa-checklist)
14. [Publishing and portfolio packaging](#14-publishing-and-portfolio-packaging)

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

Five queries build the model. Each keeps the partition pruning
(`_TABLE_SUFFIX BETWEEN`) that keeps the bill near zero. The funnel logic
mirrors the notebook exactly (Sections 3–7), so the numbers reconcile.

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
    traffic_source.medium                              AS first_touch_medium,
    event_name,
    event_timestamp,
    event_date,
    ecommerce.purchase_revenue                         AS purchase_revenue
  FROM `bigquery-public-data.ga4_obfuscated_sample_ecommerce.events_*`
  WHERE _TABLE_SUFFIX BETWEEN '20201101' AND '20210131'
    AND event_name IN ('session_start','view_item','add_to_cart',
                       'begin_checkout','purchase')
),
-- The session's own medium: taken from the earliest event in the session that
-- carries one. Scans every event type — medium is not guaranteed on funnel events.
session_medium AS (
  SELECT
    user_pseudo_id, session_id,
    ARRAY_AGG(medium ORDER BY event_timestamp LIMIT 1)[OFFSET(0)] AS session_medium
  FROM (
    SELECT
      user_pseudo_id, event_timestamp,
      (SELECT ep.value.int_value    FROM UNNEST(event_params) ep
       WHERE ep.key = 'ga_session_id' LIMIT 1)        AS session_id,
      (SELECT ep.value.string_value FROM UNNEST(event_params) ep
       WHERE ep.key = 'medium' LIMIT 1)               AS medium
    FROM `bigquery-public-data.ga4_obfuscated_sample_ecommerce.events_*`
    WHERE _TABLE_SUFFIX BETWEEN '20201101' AND '20210131'
  )
  WHERE session_id IS NOT NULL AND medium IS NOT NULL
  GROUP BY user_pseudo_id, session_id
),
se AS (
  SELECT
    user_pseudo_id, session_id, event_name,
    ANY_VALUE(device_category)     AS device_category,
    ANY_VALUE(first_touch_medium)  AS first_touch_medium,
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
    ANY_VALUE(first_touch_medium)  AS first_touch_medium,
    MIN(first_date)                AS session_date_raw,
    MAX(CASE WHEN event_name='session_start'  THEN first_ts END) AS ts_s,
    MAX(CASE WHEN event_name='view_item'      THEN first_ts END) AS ts_v,
    MAX(CASE WHEN event_name='add_to_cart'    THEN first_ts END) AS ts_c,
    MAX(CASE WHEN event_name='begin_checkout' THEN first_ts END) AS ts_k,
    MAX(CASE WHEN event_name='purchase'       THEN first_ts END) AS ts_p,
    MAX(CASE WHEN event_name='purchase'       THEN rev     END) AS revenue
  FROM se
  GROUP BY user_pseudo_id, session_id
),
flagged AS (
  SELECT
    pv.*,
    COALESCE(sm.session_medium, '(not set)')                  AS channel,
    ts_p IS NOT NULL AND ts_p >= ts_k AND ts_k >= ts_c
                     AND ts_c >= ts_v AND ts_v >= ts_s        AS purchased
  FROM pv
  LEFT JOIN session_medium sm USING (user_pseudo_id, session_id)
  WHERE ts_s IS NOT NULL
)
SELECT
  CONCAT(user_pseudo_id, '-', CAST(session_id AS STRING))   AS session_key,
  user_pseudo_id,
  PARSE_DATE('%Y%m%d', session_date_raw)                    AS session_date,
  device_category,
  channel,
  -- Channels under 1% of sessions are pooled: tiny denominators give noisy rates.
  IF(COUNT(*) OVER (PARTITION BY channel) / COUNT(*) OVER () < 0.01,
     'other (small channels)', channel)                     AS channel_group,
  COALESCE(first_touch_medium, '(not set)')                 AS first_touch_medium,
  ts_v IS NOT NULL AND ts_v >= ts_s                         AS viewed_item,
  ts_c IS NOT NULL AND ts_c >= ts_v AND ts_v >= ts_s        AS added_to_cart,
  ts_k IS NOT NULL AND ts_k >= ts_c AND ts_c >= ts_v
                   AND ts_v >= ts_s                         AS began_checkout,
  purchased,
  -- Revenue counts only on sessions that completed the sequential funnel (notebook §4).
  IF(purchased, IFNULL(revenue, 0), 0)                      AS revenue,
  IF(purchased, SAFE_DIVIDE(ts_p - ts_s, 60000000.0), NULL) AS minutes_to_purchase
FROM flagged
```

> **Channel = the session's own medium, not `traffic_source.medium`.** In the
> GA4 export `traffic_source.medium` is the *user's* first-touch medium,
> repeated on every session that user ever has. Group a session funnel by it and
> a returning direct visit is credited to the campaign that first acquired the
> user. The two definitions agree on only **52.9%** of sessions here. Keep
> `first_touch_medium` in the table purely for that agreement check — never as a
> slicer. Sessions with no session-level medium are labelled `(not set)` (26.2%
> of sessions) and are shown separately, not dropped.

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

### 3.4 · `fct_session_category` — one row per (session, product category)

Category conversion needs its own grain: one session can view several
categories, so it cannot live on `fct_sessions`.

```sql
-- One row per item on each view_item / add_to_cart event.
WITH item_events AS (
  SELECT
    user_pseudo_id,
    (SELECT ep.value.int_value FROM UNNEST(event_params) ep
     WHERE ep.key = 'ga_session_id' LIMIT 1)                     AS session_id,
    event_name,
    event_timestamp,
    COALESCE(NULLIF(TRIM(it.item_category), ''), '(not set)')    AS raw_category
  FROM `bigquery-public-data.ga4_obfuscated_sample_ecommerce.events_*`,
       UNNEST(items) AS it
  WHERE _TABLE_SUFFIX BETWEEN '20201101' AND '20210131'
    AND event_name IN ('view_item', 'add_to_cart')
),
-- item_category mixes flat labels ("Bags") with paths ("Lifestyle/Bags/").
-- Keep the last path segment, BEFORE grouping by session, so a session that
-- viewed both counts once. ' / ' (spaced) is part of a name, e.g. "Men's / Unisex".
normalised AS (
  SELECT
    *,
    IF(raw_category = '(not set)', raw_category,
       COALESCE(NULLIF(TRIM(REPLACE(ARRAY_REVERSE(SPLIT(TRIM(REPLACE(
         raw_category, ' / ', ' | '), '/'), '/'))[SAFE_OFFSET(0)], ' | ', ' / ')), ''),
                '(not set)'))                                     AS item_category
  FROM item_events
)
SELECT
  CONCAT(user_pseudo_id, '-', CAST(session_id AS STRING))         AS session_key,
  item_category,
  MIN(IF(event_name = 'view_item', event_timestamp, NULL)) IS NOT NULL
                                                                  AS viewed_category,
  -- Carted at or after the first view of this category in the session.
  IFNULL(MAX(IF(event_name = 'add_to_cart', event_timestamp, NULL))
         >= MIN(IF(event_name = 'view_item', event_timestamp, NULL)), FALSE)
                                                                  AS carted_after_view
FROM normalised
WHERE session_id IS NOT NULL
GROUP BY session_key, item_category
```

> **What this metric measures.** Category view → cart starts *after* a product
> page has been reached. It is evidence about product pages in that category
> (price, imagery, sizing, stock) — it says nothing about category or listing
> pages, or clicks from them. Label the page accordingly.

### 3.5 · `dim_category` — label audit

Every normalisation rule needs its output read, not just its row count
checked — this is how the "Men's / Unisex" → "Unisex" bug was caught.

```sql
-- Reuse the item_events and normalised CTEs from 3.4, then:
SELECT
  item_category,
  COUNT(DISTINCT raw_category)                                    AS n_source_labels,
  STRING_AGG(DISTINCT raw_category, ' · ' ORDER BY raw_category)  AS source_labels
FROM normalised
GROUP BY item_category
```

Put it in a small table on the category page (filter `n_source_labels > 1`) so
every merge is visible to the reader.

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
| `fct_session_category[session_key]` | `fct_sessions[session_key]` | Many-to-one | Single |
| `fct_session_category[item_category]` | `dim_category[item_category]` | Many-to-one | Single |

`dim_weeks_since` stays disconnected.

The session-to-category relationship lets the device, channel and date slicers
filter the category page. A few category rows come from sessions with no
`session_start` event; they have no match in `fct_sessions`, sit under a blank
member when sliced, and are still counted in the unfiltered totals — exactly as
in the notebook.

### Model hygiene

- Hide every raw key from report view (`session_key`, `user_pseudo_id`)
- Hide the `MonthSort` and `DayOfWeekNo` helper columns
- Put all measures in a dedicated `_Measures` table
  (**Home → Enter Data**, name it `_Measures`, delete the placeholder column)
- Hide `first_touch_medium` and `channel` (slice by `channel_group`)
- Set `revenue` to Currency, `minutes_to_purchase` to Decimal

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
-- Averages only purchasing sessions that recorded revenue, as the notebook does.
-- Some sequential purchases carry no purchase_revenue; dividing by all
-- [Sessions Purchased] would understate AOV (~$71 instead of $79).
Orders with Revenue = CALCULATE( [Sessions], fct_sessions[purchased] = TRUE(),
                                 fct_sessions[revenue] > 0 )
AOV           = DIVIDE( [Total Revenue], [Orders with Revenue] )
Median Order  = MEDIANX( FILTER( fct_sessions, fct_sessions[purchased] = TRUE()
                                  && fct_sessions[revenue] > 0 ),
                         fct_sessions[revenue] )
Median Minutes to Purchase =
    MEDIANX( FILTER( fct_sessions, fct_sessions[purchased] = TRUE() ),
             fct_sessions[minutes_to_purchase] )
```

Show time to purchase in minutes (~16), not hours (0.26) — nobody reads
"0.26 hours" correctly at a glance. The notebook uses `APPROX_QUANTILES`, so
expect the exact DAX median to differ by a few seconds.

### Acquisition channel

```dax
-- Purchase rate and revenue per session are only trustworthy with 30+ purchases.
Low Purchase Volume = IF( [Sessions Purchased] < 30, 1, 0 )

Revenue per Session = DIVIDE( [Total Revenue], [Sessions] )

-- Spread of product-page reach across actionable channels:
-- excludes '(not set)' and the pooled small channels, as in the notebook.
Channel View Spread pp =
VAR T =
    FILTER(
        ADDCOLUMNS( ALLSELECTED( fct_sessions[channel_group] ),
                    "@rate", [View Item Rate %] ),
        NOT fct_sessions[channel_group] IN { "(not set)", "other (small channels)" }
    )
RETURN ( MAXX( T, [@rate] ) - MINX( T, [@rate] ) ) * 100

Channel Verdict =
VAR S = [Channel View Spread pp]
RETURN SWITCH( TRUE(),
    S >= 10, "Traffic mix is a driver — check the weakest channel's landing pages",
    S >= 7,  "Borderline — leaning site problem; review the weakest channel first",
             "Uniform across channels — the leak is on the site" )

-- How often the session medium matches the user's first-touch medium.
Medium Agreement % =
DIVIDE(
    COUNTROWS( FILTER( fct_sessions,
        fct_sessions[channel] = fct_sessions[first_touch_medium] ) ),
    [Sessions] )
```

The decision rule was set before looking at the data: a spread of 10pp or more
means traffic quality drives the top-of-funnel loss; 7–10pp is reported as
**borderline**, not rounded into a story either way.

### Product category

```dax
Category Viewing Sessions =
    CALCULATE( COUNTROWS( fct_session_category ),
               fct_session_category[viewed_category] = TRUE() )

Category Carting Sessions =
    CALCULATE( COUNTROWS( fct_session_category ),
               fct_session_category[carted_after_view] = TRUE() )

Category View to Cart % = DIVIDE( [Category Carting Sessions], [Category Viewing Sessions] )

-- Volume-weighted benchmark over ranked categories (500+ viewing sessions,
-- '(not set)' excluded). ALL on the category column so each row compares
-- against the same pooled rate.
Category Benchmark % =
VAR Ranked =
    FILTER(
        ADDCOLUMNS( ALL( fct_session_category[item_category] ),
                    "@views", [Category Viewing Sessions],
                    "@carts", [Category Carting Sessions] ),
        fct_session_category[item_category] <> "(not set)" && [@views] >= 500 )
RETURN DIVIDE( SUMX( Ranked, [@carts] ), SUMX( Ranked, [@views] ) )

-- Extra carting sessions if a below-benchmark category converted at the benchmark.
Carts at Stake =
VAR Bench = [Category Benchmark %]
RETURN
SUMX(
    FILTER( VALUES( fct_session_category[item_category] ),
            fct_session_category[item_category] <> "(not set)"
            && [Category Viewing Sessions] >= 500 ),
    MAX( 0, ROUND( ( Bench - [Category View to Cart %] ) * [Category Viewing Sessions], 0 ) )
)
```

Rank by **carts at stake**, not by the rate: a high-traffic category with a
moderate gap outranks a niche one with a dramatic rate.

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
IF( [Avg Week 1 Retention] < 0.10, "#DC2626", "#059669" )
```

Card → Format → Callout value → Colour → **fx** → Field value →
`Retention Status Colour`.

The 10% line matches the HTML dashboard's warning. It is a rule-of-thumb flag,
not a sourced industry benchmark — say so if asked.

---

## 7 · Page 2 — Funnel detail

This page is where slicers earn their place.

**Visuals:**

- Funnel bar chart (as page 1, larger)
- **Matrix**: rows `device_category`, values `[Sessions]`,
  `[View Item Rate %]`, `[Cart Rate of All %]`, `[Session to Purchase %]`
- **Matrix**: rows `channel_group`, values `[Sessions]`, `[View Item Rate %]`,
  `[Session to Purchase %]`, `[Revenue per Session]`
- Card: `[Channel View Spread pp]` with `[Channel Verdict]` beneath it
- Slicers: `device_category`, `channel_group`, `dim_date[Date]`

### The device result — present it as a table

Purchase rates differ by roughly **0.05 percentage points** across desktop,
mobile and tablet. Three near-identical bars in a chart invite the reader to hunt
for a difference that isn't there; a matrix states it plainly.

Add a text box beside it:

> **Device is not the lever.** Purchase conversion differs by <0.1pp across
> devices. A mobile-first redesign — the intuitive response to a 0.8% conversion
> rate — would not have moved the number.

Reporting a null result clearly is a signal of analytical maturity. Don't bury it.

### Acquisition channel — mirror the notebook, don't reinvent it

Slice the funnel by `channel_group` (the session's own medium, see 3.1) and the
top-of-funnel drop stops being one number and becomes a distribution. If the
79% loss were concentrated in one or two paid mediums, it would be a targeting
and landing-page problem owned by acquisition; if it is flat, it is site
navigation owned by product.

Expected result: product-page reach spreads **8.7pp**, from cpc 21.6% to
referral 30.3% — **borderline** under the 10pp rule, leaning towards a site
problem. Present it that way.

Formatting rules for the channel matrix:

- **Grey out** `[Session to Purchase %]` and `[Revenue per Session]` where
  `[Low Purchase Volume] = 1` (conditional formatting → font colour → rules).
  cpc has about 28 purchases: its purchase rate must not be ranked or acted on.
  `[View Item Rate %]` rests on session counts and stays reliable.
- Keep `(not set)` as its own row with a note: 26.2% of sessions, 6.7% reach a
  product page. It is excluded from the spread, not hidden.
- Optional card: `[Medium Agreement %]` (52.9%) beside a one-line note on why
  first-touch medium is the wrong field for a session funnel.

---

## 8 · Page 3 — Product categories

**Visuals:**

- **Bar chart**: axis `item_category`, value `[Category View to Cart %]`,
  sorted ascending; add a constant line at `[Category Benchmark %]` (Analytics
  pane → Constant line → fx). Conditional colour: below benchmark = orange.
- **Table**: `item_category`, `[Category Viewing Sessions]`,
  `[Category View to Cart %]`, `[Carts at Stake]`, sorted by carts at stake.
- **Visual filter** on both: `item_category` is not `(not set)` and
  `[Category Viewing Sessions]` ≥ 500 (23 categories in this run).
- **Small table** from `dim_category`, filtered to `n_source_labels > 1` —
  the merge audit.
- Title the page "Product page → cart by category", not "Category pages".

Expected result: benchmark **25.0%**; YouTube (15.6%), Apparel (19.1%) and
Bags (17.6%) hold **1,185 of the 2,157** carts at stake — the starting list for
a product-page audit.

---

## 9 · Page 4 — Cohort retention

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

## 10 · Page 5 — Opportunity sizing

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

---

## 11 · Drill-through and tooltips

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

## 12 · Theme and formatting

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

## 13 · QA checklist

Before exporting or publishing:

**Numbers**

- [ ] Total sessions matches the Python dashboard (**354,857**)
- [ ] Session-to-purchase matches (**0.80%**)
- [ ] AOV matches (**$79**, median $55)
- [ ] Avg Week-1 retention matches (**4.2%**, full cohorts only)
- [ ] Censored cells: **91 of 196** matrix cells blank
- [ ] Device purchase-rate spread matches (**~0.05pp**)
- [ ] Median time to purchase matches (**~15.6 min**)
- [ ] Channel: `(not set)` is **26.2%** of sessions; medium agreement **52.9%**;
      view-item spread **8.7pp** (cpc 21.6% → referral 30.3%)
- [ ] Category: **23** ranked categories, benchmark **25.0%**, carts at stake
      **2,157** in total, **1,185** in the top three
- [ ] Revenue total and AOV include converting sessions only; AOV divides by
      `[Orders with Revenue]`, not `[Sessions Purchased]`

Figures are from the 12 Nov 2026 run. If the notebook is re-run, take the
expected values from the fresh `dashboard.html`, not from this list.

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
- [ ] No slicer or axis uses `first_touch_medium`
- [ ] Low-volume channel rates are greyed out
- [ ] Drill-through works from cohort rows and returns cleanly
- [ ] What-if sliders update all downstream cards
- [ ] Nothing shows `(Blank)` where a zero is meant

**Presentation**

- [ ] Page tab names are readable ("Overview", not "Page 1")
- [ ] Every page has a title
- [ ] Consistent fonts and colours throughout
- [ ] Renders correctly at 1280×720 (**View → Page view → Fit to page**)

---

## 14 · Publishing and portfolio packaging

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

**3 · Screenshot each page** to `assets/` (it already holds
`dashboard-preview.png` for the HTML dashboard) and embed them in the README.
Reviewers look at images; they don't open files.

> GitHub Pages serves `main` from the repo root, so anything committed under
> `powerbi/` is also downloadable from `https://guiarpi.github.io/ga4-product-analytics/powerbi/…`.
> Commit only the public-sample report — nothing else belongs there.

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
cross-filtering slicers across device and acquisition channel, a product-category
page, drill-through cohort detail, and a what-if parameter for opportunity sizing.
Its numbers reconcile with the notebook and the
[live HTML dashboard](https://guiarpi.github.io/ga4-product-analytics/dashboard.html).

![Power BI overview](assets/powerbi-overview.png)

- [`powerbi/GA4_Funnel_Analysis.pbix`](powerbi/) — open in Power BI Desktop
- [`powerbi/GA4_Funnel_Analysis.pdf`](powerbi/) — static export, no Power BI needed

Connected directly to BigQuery via the native connector in Import mode, with the
sequential funnel logic pushed down into SQL and all rates expressed as DAX
measures over a session-grain fact table and a (session, category) fact table.
```

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
*Last updated: 12 November 2026*
