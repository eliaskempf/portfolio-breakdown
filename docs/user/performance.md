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

## Gains and returns {#performance}

Choose **EUR**, **USD** or **GBP** during manual setup or in **Portfolio settings → Portfolio currency**.
Existing portfolios default to EUR. Changing this setting recalculates values and
gains from original records; it does not change purchase amounts or currencies.
Review affected positions before applying. Monetary planning inputs and previous
plans are cleared when currency changes.

Allocation and Performance **All** share total known gain: recorded realized and
unrealized gains plus recorded distributions and interest, less recorded costs
once. They use the same current valuation inputs, currency and scope, including
retained closed positions. Quote and FX timestamps remain visible in price details;
current inputs do not fill missing historical observations. Category comparison
uses the same monetary calculation. ETF look-through does not redefine costs.

The Overview percentage is cumulative **time-weighted return (TWR)** over the
supported recorded interval. It neutralizes external contributions and withdrawals
and requires exact flow-boundary values. It is not gain divided by remaining cost
or net capital. **Annualized return** is separately money-weighted
(XIRR), reflecting contribution amounts and timing. Neither percentage is inferred
from a partial monetary subtotal. Position details retain separately labelled
unrealized gain and cost-based return.

**Partial** means supported known components are shown, with missing facts in
metric help. Summary-only holdings contribute their known unrealized gain;
snapshot histories carry known opening unrealized gain but cannot establish earlier
realized gains or income. Unreviewed records can support a recorded subtotal, never
complete returns. Unknown amounts, unsupported actions and missing FX are not zero.

Cash balances without a recorded cost/conversion basis show **N/A — cash balance**
for purchase-based performance. They remain in portfolio value and allocation,
but do not produce missing-buy-in warnings or reduce coverage of eligible
positions. Balance increases from deposits or futures settlements are not inferred
as cash investment gains. Interest is separate. Foreign cash still changes
reporting value with current FX; calculating its FX gain needs a recorded opening
or conversion basis. Explicit cash costs, including incomplete conversions, retain
the existing cost-based calculation and coverage checks.

Each purchase, including fees, is converted separately. A supplied converted cost
takes precedence over a supplied rate, which takes precedence over historical
market FX. Historical lookup uses the purchase date or the most recent available
observation within the preceding seven days. It never uses a future observation.
Market rates can differ from actual broker settlement rates. Same-currency costs
need no purchase date or conversion.

A position with incomplete costs has no complete cost-based gain, while its
available current value still counts toward portfolio value and allocation.
Independently supported recorded income or realized gains can still contribute
to an explicitly partial monetary subtotal. **Complete**,
**Partial** and **Unavailable** describe coverage. A partial return is not the
return of the entire category. Zero cost permits an absolute gain but no cost-based
percentage. TWR/XIRR have their own flow and valuation requirements.

For old costs without dates, supply a converted total or exchange rate, or
explicitly confirm a **latest FX estimate**. **Portfolio settings** lets you select affected
positions and review their rates before confirming. Estimates may hide currency
gains or losses, remain labelled, and keep their confirmed rates across refreshes
and restarts. An undated conversion or estimate applies only to its saved target
currency; switching again may create new coverage gaps.

Separately labelled ordinary-holding unrealized figures exclude dividends and realized gains. Purchase fees affect results
only when included in recorded cost. Reconcile remaining-position costs after
sales or corporate actions. Expanded ETF constituents have unavailable performance:
current weights cannot reconstruct their historical costs. Whole-fund performance
remains usable in instruments mode. Labels follow the same path splits as allocation.

Futures investment details retain a separate recorded-performance basis: cumulative net
P&L across open and closed contracts, including fees and manually entered taxes,
divided by fixed initial allocated capital. Settlement does not erase gains.
Allocation changes affect capital value, not profit. Current reporting FX converts
native amounts; historical FX performance of contributed capital is not inferred.
See [Investment capital and contract history](futures.md#investment-capital-and-contract-history).

## Market-price history {#history}

Position details provide 1M, 6M, 1Y, 5Y and Max views of closing prices in the
portfolio reporting currency, without dividend reinvestment. When the instrument's
quote currency differs, the currency selector beside the period offers both
currency codes (for example, EUR and USD). This is instrument history,
**not personal return history**. It does not know your purchase dates or cash flows.

History loads in the background and caches for one hour. A failed request waits
60 seconds before an automatic retry; **Retry history** bypasses the wait.
Manual/unsupported instruments can have no chart. Normal demo mode uses public
history; explicit offline demo history is invented. The separate
[risk calculation](analytics.md#risk) uses adjusted prices and historical FX.

## Personal history {#personal-history}

The **History** position tab and **Overview → Performance** show personal
history separately from market-price charts and current unrealized gains.
History is optional; charts stay hidden until at least two supported values exist.
When useful history exists, positions open on **History** and Overview defaults
to **Performance**. A single summary row sits below the view tabs and above the
chart. The category comparison stays visible below history, without repeating the
category or position tables. Choosing another view retains its usual current-portfolio summary. Use **1M, 3M, 6M, YTD, 1Y, 3Y, 5Y or All** to look back from today.
The chart axis ends today; period gain uses the first and latest supported
valuations in the selected window. Annualized return uses the full recorded interval. Weekend or holiday endpoints are not assigned invented prices.
The latest valuation date remains visible. Lines connect neighboring known values
across missing observations; this does not supply prices for calculation purposes.

History follows the main **Category** selector: Portfolio includes the full
portfolio; a category includes its current source-position membership, including
retained closed positions. This does not reconstruct past category assignments.
Removed closed positions with no current assignment remain in **Unassigned** and
the portfolio total, including the category comparison.
Accounts include cash, debt, pending claims and actual futures. An account whose
components span categories needs dated allocations before category history can
be shown; it is never silently counted in full or omitted. Allocated futures
strategy capital is also unavailable at category level without that support.
Unadopted legacy liabilities and simulations cannot silently disappear from a
whole-portfolio result.

**All** means total known gain, not necessarily the full ownership period. Missing
opening prices never silently substitute a shorter interval. Complete histories
start empty before the first purchase, include that payment and fees, and retain
gains after liquidation. A confirmed empty start needs no positive snapshot.

The solid **Value** line is the selected holding or portfolio's market value.
The thinner dashed **Invested capital** line starts with known opening acquisition
cost for securities, and recorded opening equity for accounts, then adds external
contributions less withdrawals. Value minus capital equals cumulative known gain
at the same date. After disposals this is not remaining acquisition cost and can
be zero or negative. Unsupported capital remains unavailable. Changing the chart
period preserves the basis. Nothing before an opening snapshot is represented as
zero value or gain.

For example, an invented snapshot with cost 8,000 and market value 10,000 starts
with capital 8,000 and known gain 2,000. Later value 11,000 means known gain 3,000,
but post-snapshot return is 10% before annualization. The earlier gain has unknown
timing and does not enter returns. Backfill replaces this bridge rather than
adding reconstructed gains to it a second time. If cost is unknown, measured
post-snapshot gain may be shown as partial while capital stays unavailable.
Move anywhere across the plot to inspect a date and formatted values together.
The vertical guide snaps to observations; it does not invent interpolated prices.
If series have different observation dates, the tooltip shows each differing
date beside its value.
The value and invested capital boxes follow the hovered observations and show
their dates. Click the gain badge to toggle between an amount and cumulative
TWR. The metric name and period appear only in a concise tooltip. Gain beside the value shows the selected-period gain at rest and
cumulative recorded gain to the hovered date during inspection. Ranges covering
the entire holding period use total known gain, just like **All**. Leaving the chart
restores the latest value and selected-period gain. **Annualized return** stays fixed across both period selection and hover; its tooltip gives
the calculation dates. Missing opening data or incomplete full-history coverage
withholds this return rather than substituting a shorter interval. A quiet note below
the chart indicates missing valuations; no values are interpolated for the boxes
or returns. A missing daily value need not prevent endpoint-based returns.

**Correct market data** provides focused forms for missing or incorrect dated
prices and exchange rates. Known dates, holdings and currencies are preselected;
only the selected holdings' observations are shown. **Advanced corrections** retains
whole-selection valuations, exact cash-flow boundary values and cash adjustment
classification. Available prices and FX load automatically; **Retry prices** is
shown after an unsuccessful refresh. Downloads include accepted exact-contract
settlement marks. It does not create
trades, distributions or splits. Downloaded closes are converted back to the
units traded on each date using the provider's split evidence, without dividend
reinvestment or automatic price repair. Manual observations take precedence.
Prices and FX must cover the requested dates; gaps are not interpolated or filled
with current quotes. Missing data can be entered manually. Corrections retain
original observations.

**Confirm complete history**, beside the position's transaction reconciliation,
records your review of the opening inventory and all events within the displayed
interval. Apply pending transaction changes before confirming. Portfolio history
has a separate **Review history completeness** action for its combined scope. Journal changes invalidate that confirmation.
Applying transaction history to current balances does not confirm performance
coverage. A review covers its named members and their subsets for the reviewed
dates; it does not cover new members or a changed journal.
Replacement balances interrupt the interval; unresolved cash adjustments must be
classified as external flows, income or expenses, or retained as boundaries.
Coverage is specific to the selected members, dates and metric.

Account cash openings precede separately recorded same-day account trades, fees
and external funding; these events must not disappear into the first closing
valuation. Security starting balances remain end-of-day snapshots, and derived
security payments already included in an account opening are not posted again.
Same-day flow order must be explicit for exact TWR.

| Metric | Required evidence |
| --- | --- |
| Historical value | Dated holdings, cash, debt, claims, prices and FX, or an explicitly supplied total valuation |
| Invested capital | Known opening cost/account equity plus supported classified net external flows |
| Total known gain | Ending value minus capital, including known opening gains; partial components stay labelled |
| Period gain | Ending value minus opening market value minus net external contributions |
| Annualized money-weighted return | Endpoint values and complete dated external flows with a numerically verified unique XIRR |
| Time-weighted return | Funded intervals and reconciled exact before/after valuations at every external flow |

Purchases paid from included cash, transfers between included accounts, borrowing
with its matching debt, and futures settlement are internal movements. Crossing
the selected scope changes that treatment. Dividends and interest are income;
withdrawals are separate flows. Fees reduce results once. Remaining acquisition
cost and cumulative purchases are not net contributions. Futures notional and
allocated principal are not extra wealth; settled profit already resides in cash.

Money-weighted returns use actual elapsed days divided by 365 and can work without
daily valuations. The annualized-return tooltip identifies short recorded intervals. Same-day
periods, known negative-equity intervals and unverified solutions have no
annualized return. Negative equity is checked at recorded event-day closes and
valuation dates, including values reconstructed from prices, FX, cash and debt.
Negative cash covered by other assets, and a negative invested-capital line after
withdrawals, do not themselves make portfolio equity negative. Missing valuations
remain unknown; intraday losses without recorded valuation evidence are not inferred. Empty inception and terminal liquidation are supported when
dated flows establish a unique result. TWR can link explicitly valued funded
intervals separated by an empty period; money appearing without funding is invalid.
Nonconventional cash flows are conservatively withheld when
uniqueness cannot be established. Exact time-weighted returns remain unavailable
without reconciled boundary valuations; no end-of-day timing assumption is made.
Historical value points and monetary gains can remain useful when returns cannot
be calculated. The affected metric’s help explains its own missing requirements;
other metrics do not inherit that restriction.

Original acquisition costs remain separate from opening market value, so gains
before the tracking boundary are not attributed to the tracked period. FIFO is
analytical cost accounting, not a tax report. Complex unsupported corporate actions
must remain unresolved until sufficient records or a new opening boundary exist.

Position **Details** separates recorded realized gain, net income, other expenses,
purchase payments and net sale proceeds. Remaining acquisition cost belongs to
the unsold FIFO lots; cumulative purchase payments include all recorded purchases
and their charges. Sale proceeds include returned principal, whereas realized
gain deducts the sold lots' costs. Buy fees enter lot cost, sell and distribution
charges reduce their proceeds, and other expenses are deducted separately. These
components reconcile to total gain for an ordinary complete trade history.
Transfers preserve lot costs but cross performance scopes at market value, so
their acquisition-cost breakdown and scope gain have different boundaries.
