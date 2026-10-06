# Prices and performance {#prices}

## Valuation and freshness {#valuation}

```text
Reporting value = quantity × quote price × reporting currency per unit of quote currency
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

Choose **EUR**, **USD** or **GBP** during manual setup or in **Portfolio settings → Portfolio currency**.
Existing portfolios default to EUR. Changing this setting recalculates values and
gains from original records; it does not change purchase amounts or currencies.
Review affected positions before applying. Monetary planning inputs and previous
plans are cleared when currency changes.

Overview Performance summarizes currently held source positions; ETF look-through
does not redefine their costs. Gain is current reporting value minus converted
purchase cost. Category return divides summed covered gain by summed covered cost;
it does not average individual percentage returns.

Each purchase, including fees, is converted separately. A supplied converted cost
takes precedence over a supplied rate, which takes precedence over historical
market FX. Historical lookup uses the purchase date or the most recent available
observation within the preceding seven days. It never uses a future observation.
Market rates can differ from actual broker settlement rates. Same-currency costs
need no purchase date or conversion.

A position with incomplete costs is excluded from gains, while its available
current value still counts toward portfolio value and allocation. **Complete**,
**Partial** and **Unavailable** describe coverage. A partial return is not the
return of the entire category. Zero cost permits an absolute gain but no percentage
return.

For old costs without dates, supply a converted total or exchange rate, or
explicitly confirm a **latest FX estimate**. **Portfolio settings** lets you select affected
positions and review their rates before confirming. Estimates may hide currency
gains or losses, remain labelled, and keep their confirmed rates across refreshes
and restarts. An undated conversion or estimate applies only to its saved target
currency; switching again may create new coverage gaps.

These figures exclude dividends and realized gains. Purchase fees affect results
only when included in recorded cost. Reconcile remaining-position costs after
sales or corporate actions. Expanded ETF constituents have unavailable performance:
current weights cannot reconstruct their historical costs. Whole-fund performance
remains usable in instruments mode. Labels follow the same path splits as allocation.

## Market-price history {#history}

Position details provide 1M, 6M, 1Y, 5Y and Max views of closing prices in the
portfolio reporting currency, with a **Native currency** option without dividend reinvestment. This is instrument history,
**not personal return history**. It does not know your purchase dates or cash flows.

History loads in the background and caches for one hour. A failed request waits
60 seconds before an automatic retry; **Retry history** bypasses the wait.
Manual/unsupported instruments can have no chart. Normal demo mode uses public
history; explicit offline demo history is invented. The separate
[risk calculation](analytics.md#risk) uses adjusted prices and historical FX.
