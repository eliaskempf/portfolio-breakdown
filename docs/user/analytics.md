# Analytics and risk {#analytics}

Overview Analytics follows the selected strategic category and optional account
filter. Position search and Exposure filters do not change that scope. Positions
also offers Valuation, Income & fees and Risk views, with definitions and sources
under Key metrics. Blank metrics mean unavailable, not zero.

## Historical risk {#risk}

Choose **Calculate risk**. The default is three years against `IUSQ.DE`, a global
equity ETF proxy. Options offers the benchmark and one-, three- or five-year
windows. Read the sample size, exclusions and coverage alongside each result.

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
categories. Borrowing, liabilities and net-equity leverage are not modeled.

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
