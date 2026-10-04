# Prices and performance {#prices}

## Valuation and freshness {#valuation}

```text
EUR value = quantity × quote price × EUR per unit of quote currency
```

Provider prices use the latest available unadjusted daily close; the current
bar can still change. They are not guaranteed real-time execution quotes. Quote
currency comes from provider metadata, not ticker suffixes. Recognized pence/cents
units are converted to major currency before FX. A saved manual price overrides
provider pricing until cleared. Buy-in is never substituted for market price.

Missing ticker, currency, FX or quote leaves a position unvalued, not worth zero.
A zero-quantity position has zero current value without requiring a quote. View
price details for separate quote/FX timestamps, ages and reasons.

Quotes and FX refresh in the background using private caches. Normal attempts
are limited to once per 15 minutes per key, including failures. **Refresh prices**
bypasses that interval. Failed refreshes can retain the last successful quote as
**cached fallback**. Old data is not newly observed data; inspect dates across
market closures. Crypto quotes older than 24 hours have a continuous-market note.

## Unrealized performance {#performance}

Overview Performance summarizes currently held source positions; ETF look-through
does not redefine their costs. EUR gain is current EUR value minus recorded EUR
cost for eligible positions. Category return divides their summed gain by their
summed cost; it does not average individual percentage returns.

Only positions with usable valuations and EUR costs enter EUR totals. Missing
costs, unspecified currencies and foreign-currency costs reduce coverage. Native
currency returns can be available in position details. Historical costs are never
translated using today's FX. Zero cost permits an absolute gain but no percentage
return. **Complete**, **Partial** and **Unavailable** describe coverage; a partial
return is not the return of the entire category.

These figures exclude dividends and realized gains. Purchase fees affect results
only when included in recorded cost. Reconcile remaining-position costs after
sales or corporate actions. Expanded ETF constituents have unavailable performance:
current weights cannot reconstruct their historical costs. Whole-fund performance
remains usable in instruments mode. Labels follow the same path splits as allocation.

## Market-price history {#history}

Position details provide 1M, 6M, 1Y, 5Y and Max views of closing prices in the
listing currency without dividend reinvestment. This is instrument history,
**not personal return history**. It does not know your purchase dates or cash flows.

History loads in the background and caches for one hour. A failed request waits
60 seconds before an automatic retry; **Retry history** bypasses the wait.
Manual/unsupported instruments can have no chart. Normal demo mode uses public
history; explicit offline demo history is invented. The separate
[risk calculation](analytics.md#risk) uses adjusted prices and historical FX.
