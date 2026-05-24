# Product Brief — GA4 Funnel Analysis

*Generated 2026-05-24 · Session-scoped funnel · Nov 2020 – Jan 2021 · [GA4 public e-commerce sample](https://console.cloud.google.com/marketplace/product/google-analytics-public-data/ga4_obfuscated_sample_ecommerce)*

---

## Executive Summary

Only 0.80% of sessions reach a purchase — but the data shows the problem is almost entirely top-of-funnel: 78.8% of sessions never reach a product page. Users who do discover a product and proceed to checkout convert at a respectable 52% from checkout to purchase, with a median end-to-end journey of 18 minutes and no meaningful device-based friction. The core opportunity is not fixing checkout — it is getting more sessions to encounter the product.

## Key Findings

1. **78.8% of sessions never view a product (354,857 → 75,261).** The largest single drop in the funnel happens before any product interaction. This points to a navigation, discovery, or intent-matching problem — not a product or checkout problem. In B2B SaaS terms, this maps to users who log in but never reach a core feature.

2. **Of users who do see a product, 80.2% don't add it to cart (75,261 → 14,870).** Even among sessions with product intent, the majority don't act. This is the second-largest drop and suggests product pages are not converting interest into intent — likely a pricing, content, or trust issue.

3. **Shoppers-funnel conversion is 4.4% — well below the 10% e-commerce benchmark.** When the funnel is scoped to sessions that viewed a product (eliminating unengaged traffic), conversion is still below industry norms. This confirms there is a mid-funnel problem on product pages, not just a traffic quality problem.

4. **Once users reach checkout, 52.3% complete the purchase in under 6 minutes.** The checkout flow is working. The median time from checkout initiation to purchase is 0.06 hours — there is no payment friction for users who reach this stage. Optimising checkout would be wasted effort.

5. **Purchase rates are within 0.1pp across desktop, mobile, and tablet.** Device type does not explain conversion differences. A mobile-first redesign or device-specific A/B test would not address the core drop-off problem. The funnel leaks uniformly regardless of how users access the site.

## Hypotheses

1. **[HIGH confidence] The homepage or entry experience fails to surface relevant products.** The 78.8% top-of-funnel drop is consistent with poor discovery design — users arrive but don't find a path to a product page. Strong navigation, featured categories, or personalised recommendations would directly attack this drop. The data strongly supports this as the primary lever.

2. **[MEDIUM confidence] Product pages lack sufficient trust or decision-making information.** The 80.2% drop from view to add-to-cart suggests that users who find a product are not convinced to act. Likely causes: insufficient reviews, unclear pricing, weak product descriptions, or no urgency signals. This hypothesis is supported by the data but needs qualitative research (session recordings, heatmaps) to confirm.

3. **[LOW confidence] The 4.4% shoppers-funnel rate reflects a seasonal or campaign-specific traffic mix.** The dataset covers Nov 2020 – Jan 2021, which includes Black Friday and holiday peak. High-volume, low-intent promotional traffic could inflate total sessions while suppressing conversion rates. This would mean the "real" engaged-traffic conversion is higher than it appears — but cannot be confirmed without traffic-source segmentation.

## Recommended Actions

1. **Redesign the product discovery path — test a category-forward homepage.** Instrument and A/B test a homepage variant that leads users to product categories within two clicks. Watch: `view_item` rate (target: increase from 21.2% toward 35%+) and overall conversion. This is the highest-leverage change available.

2. **Run a product-page content audit and test social proof additions.** Audit the 10 highest-traffic product pages with lowest view-to-cart rates. Add or improve review counts, ratings, and scarcity signals. Watch: `add_to_cart` rate from `view_item` sessions (current: 19.8%). Even a 5pp improvement here would materially lift end-to-end conversion.

3. **Segment the funnel by traffic source (organic, paid, direct, email).** The current analysis treats all sessions equally. Paid traffic likely has different intent profiles than organic. Segmenting by `traffic_source.medium` would reveal whether the top-of-funnel problem is a traffic quality issue or a site design issue — and inform where to direct budget. Watch: funnel conversion by channel vs. CAC by channel.

## What to Measure Next

1. **Traffic source × funnel step cross-tab.** Does the 78.8% product-page miss rate vary by acquisition channel? If paid traffic has a 90% miss rate but organic has a 60% miss rate, the problem is ad targeting — not navigation. This single query would reprioritise the entire action list.

2. **Session recording analysis on product pages with >500 views and <15% add-to-cart rate.** Quantitative data shows where users drop; session recordings show why. Identifying the top 10 underperforming product pages and reviewing recordings would generate concrete, actionable hypotheses for the content audit in under a week.

3. **Return-visitor vs. new-visitor funnel comparison.** The cohort retention data shows some users return across weeks. Do return visitors convert at a materially higher rate? If yes, the product has retention value and the focus should be on bringing new users back (email, retargeting). If not, the product-page problem affects even motivated users.

---

*⚠ Validation note: This analysis uses a public GA4 e-commerce sample dataset. All findings should be validated on production data before informing product roadmap decisions. The funnel methodology (session-scoped, sequential timestamp constraint) is directly transferable to B2B SaaS event streams.*
