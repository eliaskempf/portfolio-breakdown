# Analytics and risk {#analytics}

Overview Analytics follows the selected strategic category and optional account
filter. Position search and Exposure filters do not change that scope. Positions
also offers Valuation, Income & fees and Risk views, with definitions and sources
under Key metrics. Blank metrics mean unavailable, not zero.

## Historical risk {#risk}

Choose **Calculate risk**. The default is three years against **MSCI ACWI Net
Return**, using direct index levels in the portfolio reporting currency. Net
return reinvests dividends after index-assumed withholding tax, without ETF fees
or personal taxes. Currency translation does not hedge currency risk.
Options offers the benchmark and one-, three- or five-year windows.

Usable cached index history takes priority over switching sources. If direct
index history is unavailable or has fewer than 52 weekly returns, the calculation
uses the adjusted `IUSQ.DE` ETF history in the reporting currency. The benchmark
label changes to **MSCI ACWI · ETF proxy** and **ETF benchmark fallback** appears
beside the estimate. Sources are never stitched. Custom benchmark tickers do not
fall back automatically. The direct reference is `MSCI:892400:NETR`.

Coverage and exclusions appear in a compact status beside **Details**, which
contains the sample dates, calculation basis and sources. The correlation heatmap
is visible above the holdings risk table; **Expand** enlarges it and shows full
names. Full names remain available on hover in compact mode. Blank cells mean
undefined correlation, not zero. **Columns** controls the table's risk measures.
Positions with no defined correlations, such as cash in the reporting currency,
are omitted from the heatmap but remain in the risk calculations and holdings
table. Foreign-currency cash remains visible when its FX returns vary.

The calculation applies **today's weights held constant** to historical weekly
returns. It is a hypothetical allocation, not your personal historical portfolio
return or a forecast. It uses dividend/split-adjusted prices and historical FX to
the portfolio reporting currency, selecting the last observation in each completed Friday-ending week. Missing
weeks are not filled, and returns do not bridge missing endpoints. All included
holdings and the benchmark need at least 52 common weekly returns.

Beta is covariance with the benchmark divided by benchmark variance. Volatility
annualizes weekly variation using the square root of 52. Correlation measures
co-movement, not the size of a possible loss. Volatility contributions sum to the
portfolio estimate; negative contributions can reflect diversification.

Cash in the reporting currency has zero FX returns; foreign cash uses historical FX returns. Manual or unlisted assets without suitable
history are excluded. Covered weights are renormalized; missing valuations prevent
claiming whole-portfolio coverage. Funds use their own histories, not expanded
constituents. Category risk still uses the selected benchmark, even for non-equity
categories. Version 0.2.0 also supports [actual-account leverage](futures.md)
in EUR/USD/GBP with net-equity denominators, as described below. The separate
legacy simulation remains EUR-only.

Integrated account risk also shows a **covered-holdings estimate** when some
histories are missing or too short. Covered positions retain their weights against
whole-portfolio net equity, including signed futures notionals and debt; these
weights are not renormalized. The displayed beta, volatility and correlation omit
the excluded positions, whose count appears beside the estimate and names and
reasons under **Details → Risk coverage & sources**. Excluded positions can increase or decrease actual
risk. Positive, complete net equity and at least 52 common weekly returns are
still required.

When you select a category or filter accounts, the main metrics are **Subset
beta**, **Subset volatility (annualized)** and benchmark correlation. Ordinary
subsets normalize the covered holdings to their own net value, including any
selected debt. Unselected debt is not assigned to the subset. For example, a
subset worth 20% of portfolio equity with beta 1.5 has standalone beta 1.5 and
contributes 0.3 to portfolio beta. The view shows that contribution
separately below the main metrics, using whole-portfolio net equity.

Subsets containing futures use their selected funded capital, including cash
attributed to the investment and unsettled claims counted once. Signed notional
remains the future's risk weight numerator. Missing histories do not shrink this
capital base. A bare contract's notional, margin requirement or unsettled gain
does not establish a standalone capital base: select its funded investment or
include its backing capital. Without a positive, fully valued base, standalone
metrics remain unavailable while a supported portfolio beta contribution is
still shown. Contributions use the subset's common history sample; comparisons
across categories need matching dates and coverage to add up exactly.

For reviewed equivalent equity listings, risk can use another venue's adjusted
history when the held listing has fewer than 52 benchmark-matched weekly returns.
Vertiv's `49V.DE` can use its same-share US listing `VRT`. Historical FX converts
that history into the reporting currency; prices from different listings are
never stitched together. Coverage sources identify the substitution. This does
not change the held ticker or its current valuation, and does not apply to funds
or futures proxies.

## Valuation, income and costs {#metrics}

Aggregate P/E is `sum(value) / sum(value / PE)` for covered profitable direct
stocks. It excludes nonpositive ratios and fund-reported P/E. Fund ratios remain
separate. Monetary fundamentals retain their reported currency.

Annual fund cost estimates multiply current fund value by annual fee rate. Fees
are already reflected in fund prices; do not subtract the estimate again from
gains. Trailing cash distribution estimates multiply current value by reported
yield. They are not forecasts or dividends actually received. Verified accumulating
share classes have zero cash yield. Coverage limits every aggregate.

Instrument concentration combines repeated instrument IDs across accounts.
Underlying-company concentration depends on ETF coverage and reviewed identities;
it is distinct from historical volatility.

## Sources and fee overrides {#sources}

Fundamentals and adjusted histories have separate private caches, normally valid
for 24 hours, with a 15-minute failed-request cooldown. Explicit refresh retries
immediately; failures may retain visibly stale data. Ordinary holdings remain
available if analytics fails.

Dated issuer fees match the exact share class; an ETF exposure proxy cannot supply
another fund's fee. Provider expense ratios are labeled fallbacks. **Maintain fund
fee** stores a private override with share-class identity, annual rate, source,
verification date and accumulation policy. Enter rates as percentages. Verification
older than 90 days is flagged; removing an override restores issuer/provider data.
