# Product Brief — GA4 Funnel Analysis

*Generated 2026-11-22 · Model: Gemini (gemini-3.5-flash) · Context v5 · Session-scoped funnel · Nov 2020 – Jan 2021 · [GA4 public e-commerce sample](https://console.cloud.google.com/marketplace/product/google-analytics-public-data/ga4_obfuscated_sample_ecommerce)*

---

## Executive Summary
The primary funnel leak occurs at initial product discovery, where 78.8% of sessions (279,596 sessions) drop off before seeing any product (in B2B SaaS terms: login to feature discovery). The channel acquisition spread of 8.7 percentage points leans borderline toward a site issue rather than traffic quality, while post-intent cart abandonment creates a secondary leak with 65.0% of carting sessions failing to begin checkout. *Note: The underlying data is an obfuscated public sample and all findings require validation on real data.*

## Key Findings
1. **Initial discovery is the primary leak in the funnel.** Only 21.2% of sessions (75,261 of 354,857) ever saw products on a listing page.
   - **So what:** Product squad focus must prioritize guiding initial site visitors to product listings over top-of-funnel acquisition channel shifts.
2. **Post-intent cart abandonment eliminates nearly two-thirds of intent.** 65.0% of carting sessions (9,665 of 14,870) never begin checkout.
   - **So what:** Re-engagement and cart recovery mechanisms for identifiable users represent an immediate path to capture abandoned high-intent visits.
3. **Six product-type listing pages underperform the site's listing average.** Out of 12 ranked product-type listing pages, 6 perform below the 22.1% product-type average with 95% confidence; the top 3 by carts at stake—Bags (13.6%), Women's (15.1%), and Men's / Unisex (19.7%)—account for 1,054 of the 1,635 carts at stake across all underperforming listing pages.
   - **So what:** Merchandising and listing layout audits should focus specifically on these three high-impact product-type listings rather than broad site-wide redesigns.
4. **Untagged entry traffic drags down landing page performance.** Three entry-traffic landing pages (/Google+Redesign/Apparel, /store.html, and /Google+Redesign/Lifestyle/Drinkware) represent 57,996 landings with saw-a-product rates between 8.9% and 9.8% (versus the 21.2% site average), despite converting at 25.8% to 35.2% when visitors arrive from inside the site.
   - **So what:** Address off-site traffic sources sending untagged entry traffic rather than redesigning page layouts that function when reached internally.

## Hypotheses
1. **[MEDIUM confidence] — Listing page item layout and display positioning cause lower saw-to-cart rates in underperforming categories.**
   - **Evidence:** 6 of 12 ranked product-type listing pages convert below the 22.1% product-type average with 95% confidence, led by Bags (13.6%), Women's (15.1%), and Men's / Unisex (19.7%).
   - **Confounder:** Price variations (median listed prices range from $12.0 in Bags to $37.0 in Men's / Unisex, and item-level price bands show $10–20 items convert at 12.2% while $30–50 items convert at 6.3%) and listing composition (whether categories appear in broad, mixed listings seen by less intent-driven sessions).
2. **[MEDIUM confidence] — Low product exposure on entry landing pages stems from off-site visitor intent mismatch rather than page usability.**
   - **Evidence:** Entry-traffic landing pages such as /Google+Redesign/Apparel have an 8.9% saw-a-product rate for landing traffic but reach 28.1% when reached internally, with 59.7% of landings lacking channel tags.
   - **Confounder:** Who lands there (very short untagged sessions that may represent automated or accidental clicks, reducing the actual reachable opportunity size).
3. **[MEDIUM confidence] — Prompted re-engagement of past visitors will increase overall conversion volume.**
   - **Evidence:** Returning sessions buy at 1.7% compared to 0.4% for new sessions (a 4.1x ratio).
   - **Confounder:** Self-selection (users who return unprompted were inherently higher-intent to begin with, meaning brought-back users will not fully match the 4.1x conversion ratio).

## Recommended Actions
1. **Fix discovery on weak entry-traffic landing pages and address measurement gaps.**
   - **What:** Investigate traffic sources sending untagged landing traffic to /Google+Redesign/Apparel, /store.html, and /Google+Redesign/Lifestyle/Drinkware to route them to higher-converting destinations, and set up tracking alerts for measurement gaps like /Google+Redesign/Apparel/Google+Dino+Game+Tee.
   - **Why:** Unlocking discovery on entry pages captures missed intent without altering internal navigation architecture.
   - **Metric:** 253 orders and $9.0k–$18.0k in revenue (based on the planning assumption that added product-viewing sessions convert at 50–100% of today's saw-to-purchase rate).
2. **Deploy cart recovery and user re-engagement programs.**
   - **What:** Execute targeted recovery notifications for the 9,665 sessions that carted but dropped off before checkout, and run re-engagement campaigns targeting inactive accounts.
   - **Why:** 65.0% of carting sessions abandon before checkout, and returning sessions yield higher conversion rates.
   - **Metric:** $35.9k in cart recovery revenue (505 orders, using the planning assumption that 10.0% of abandoned carts reach checkout) and $2.6k–$12.0k in re-engagement revenue (using the planning assumption of reaching an 8.0% Week-1 return target).
3. **Audit product listings for Bags, Women's, and Men's / Unisex pages.**
   - **What:** Review item sequencing, sorting defaults, and listing presentation for Bags, Women's, and Men's / Unisex listing pages.
   - **Why:** These three specific listing pages account for 1,054 carts at stake out of 1,635 across all six underperforming product-type listings.
   - **Metric:** 193 orders and $6.9k–$13.7k in revenue (based on the upper-end planning assumption that added carts convert like today's carts).

## What to Measure Next
1. **Untagged Landing Traffic Source Audit:** Evaluate incoming HTTP referrers and raw URL parameters for the 59.7%–74.8% untagged sessions on entry landing pages to determine whether non-converting traffic originates from untagged marketing campaigns, direct links, or mismatched search queries.
2. **In-Listing Item Position and Impression Ranking:** Measure item-level impression position and click-through rates on the Bags, Women's, and Men's / Unisex listing pages to evaluate whether featured products at top listing positions correlate with lower carting rates.
3. **Cross-Session Purchase Mapping:** Evaluate user-level identification to measure how many of the 2,127 purchasing sessions outside the sequential funnel originated from cart additions in prior sessions.