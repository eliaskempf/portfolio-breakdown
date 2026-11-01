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
using daily valuations and the daily cash-flow convention described below. It is not gain divided by remaining cost
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
Transaction CSV can retain supplied acquisition-cost and monetary-valuation
estimates, with their sources. Affected gains show an approximate status.
Applying transaction history does not certify estimated amounts as exact;
the label remains while the calculation depends on those estimates.
Costed units can contribute a partial unrealized gain while other units retain
unknown cost; the unknown units are never treated as free.
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

In position details, **History → Price** provides 1M, 6M, 1Y, 5Y and Max views of closing prices in the
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

CSV, PDF and manually entered transactions use the same history and controls.
Source details are available with each transaction; they do not change its
calculations or editability. Exchanges and transfers are single economic events
whose connected holdings are reviewed together.

Explicitly supplied estimates can support daily TWR and annualized XIRR. Affected
results retain the estimate qualification in gain/TWR and annualized-return
tooltips, without separate estimate captions. Missing transaction details and market
observations remain separate limitations. An estimated acquisition cost does not by itself
make a return estimated when that return uses independently known cash flows and
market values. Unknown amounts stay unknown; a matching ending quantity does not
certify complete history. The former separate daily-Dietz diagnostic is retired.
Existing diagnostic settings are preserved but no longer affect the application.

The **History → Holdings** position view and **Overview → Performance** show personal
history separately from market-price charts and current unrealized gains.
History is optional; charts stay hidden until at least two supported values exist.
Positions open on **History**, with **Holdings** selected when useful transaction
history exists and **Price** otherwise. Positions without recorded history evidence
show the price chart directly, without the selector. The Holdings / Price selector
remembers your explicit choice for each position during the session. Without applied history,
Holdings offers a route to add and review transactions. The other position tabs
stay in the same order. Overview remembers your selected view for each portfolio,
including after a browser refresh or app restart. With no saved choice it opens
**Performance** when useful history exists, otherwise **Allocation**.
Allocation's percentage gain uses full-history TWR; when unavailable, its tooltip
explains the missing evidence. Position details share one row above the chart selector: **Position value** with
personal gain, **Invested capital**, and **Annualized return**. Switching chart type,
range or hover does not change these headlines. Dated figures stay in the chart
tooltip. **Details** contains return on cost and unrealized gain. Without applied
history, capital is the recorded acquisition cost, gain is unrealized only, and
annualized return is unavailable. Overview keeps its own history summary above the
chart. The category comparison stays visible below history, without repeating the
category or position tables. Choosing another view retains its usual current-portfolio summary. Use **1M, 3M, 6M, YTD, 1Y, 3Y, 5Y or All** to look back from today.
The chart axis ends today; period gain uses the first and latest supported
valuations in the selected window. Annualized return uses the full recorded interval. Verified closed-market endpoints can use the last eligible closing quote, retaining its actual as-of date.
Stuttgart listings are recognized from the provider's exchange and timezone
metadata and use the Stuttgart calendar, including its
[published trading holidays](https://www.boerse-stuttgart.de/en/investing/market-hours/).
A metadata refresh updates previously downloaded evidence without inventing
weekend quotes. A missing price on a scheduled trading day still blocks the
affected valuation.
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
Returns for both **All** and a selected period remain unavailable if a later
recorded event lacks an ending valuation. The timeline includes linked-security
cash movements, incoming transfers and separately dated fees. Those fee dates
also count when checking for known negative equity, even if the balance later
recovers.

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
For prices, optional **Quote provenance** contains **Source**, **Quote listing**,
**Exchange calendar** and **Exchange timezone**. Corrections initially retain
the saved quote's details; missing dates can receive explicitly supplied details.
These entries are your evidence, not automatic verification. The historical quote
listing does not change the holding's current listing. Saving preserves the
original observation and records a manual correction, so later downloads do not
overwrite it. **Saved market data** shows actual source and listing/calendar/timezone
details; manual source descriptions are marked `manual:`.

Prices must cover required valuation boundaries; only verified closed sessions can use the preceding eligible close.
Exact-contract futures marks use the supported Eurex session calendar too, so
weekend funding can use the preceding session's settlement without inventing a
weekend mark. Missing trading-day marks and unsupported calendars remain gaps.
For Saturday/Sunday valuations, an absent closing FX rate uses the immediately preceding Friday's closing rate,
or the prior scheduled close when Friday itself is a documented FX holiday.
Thus a Monday contribution can use Friday's supported closing price and FX for its opening valuation,
without inventing Sunday observations. The same rule covers explicitly documented FX closing-session holidays
for EUR, USD, GBP, CHF, JPY, CAD, AUD and NZD pairs, including New Year's Day 2025.
The historical date list uses [OANDA's holiday schedule](https://www.oanda.com/us-en/trading/holiday-trading-hours/)
and [LEAN's Forex-oanda calendar](https://github.com/QuantConnect/Lean/blob/master/Data/market-hours/market-hours-database.json).
Evening holiday reopenings start the next closing session. Other bank/equity holidays and undocumented future
holidays do not establish an FX closure; maintainers must verify additional dates before adding them.
Actual dated closing FX takes precedence. Missing scheduled closes are not filled from older or later rates.
Quote and FX as-of dates remain visible.
Transaction payments, fees and acquisition-cost conversions still require their own dated conversion evidence.
Gaps are not interpolated or filled with current quotes. Missing data can be entered manually. Corrections retain
original observations.

Applied history is the authoritative ledger for the resulting holdings and
accounts. **Apply history** accepts the balances shown in reconciliation; there
is no separate completeness confirmation. Gains and returns remain available as
new prices arrive, provided their required inputs are supported. A newer
valuation date does not invalidate the applied ledger.

Replacement balances interrupt an interval, and unresolved cash adjustments must
be classified as external flows, income or expenses, or retained as boundaries.
An edit to remaining acquisition cost alone does not interrupt period gain,
TWR or XIRR: those use market values and cash flows. It does prevent carrying
the old cost basis through the edit as complete lifetime capital or gain;
independently supported gain components remain available.
Unknown amounts, missing market data and unsupported earlier periods remain
explicit limitations. Applying records does not prove that unrecorded real-world
activity never occurred; calculations describe the records supplied.
After a dated replacement opening, later periods can use that opening even if
an older checkpoint lacked an observation date. Returns across the replacement
remain unsupported.

Account cash openings precede separately recorded same-day account trades, fees
and external funding; these events must not disappear into the first closing
valuation. Security starting balances remain end-of-day snapshots, and derived
security payments already included in an account opening are not posted again.
An explicitly newly funded account instead starts at zero before its first-day
transactions, including linked securities purchases and fees. A confirmed empty
security opening dated the preceding day is consistent with that declaration.
If an internal transfer on the selected start date crosses a new account's
opening boundary and an existing account or holding's closing snapshot, period
gain and returns remain unavailable. Choose a start date before or after that
day; the supported ending value remains available.
Same-day flow order must be explicit for exact TWR.

| Metric | Required evidence |
| --- | --- |
| Historical value | Dated holdings, cash, debt, claims, prices and FX, or an explicitly supplied total valuation |
| Invested capital | Known opening cost/account equity plus supported classified net external flows |
| Total known gain | Ending value minus capital, including known opening gains; partial components stay labelled |
| Period gain | Ending value minus opening market value minus net external contributions |
| Annualized money-weighted return | Endpoint values and complete dated external flows with a numerically verified unique XIRR |
| Time-weighted return | Supported daily flow boundaries and complete dated external flows; exact event valuations when explicitly requesting the exact method |

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
Mixed-sign cash flows are eligible only when the solver establishes a unique,
numerically verified XIRR. Multiple roots, no solution and uncertain numerical
conditioning are different from missing transactions. Supported long schedules use
a verified uniqueness certificate. An XIRR **calculation limit** means the bounded
solver could not verify the full schedule within its work limit; unusually complex
schedules can still remain unavailable. The app never shortens the period,
guesses a root or substitutes return on cost. Conventional schedules retain a
separate fast path.

Ordinary TWR uses a daily convention: incoming capital is available at the start
of the day and outgoing capital leaves at its end. Chain daily factors
`(ending equity + withdrawals) / (beginning equity + contributions)` and subtract
one. This convention does not reconstruct intraday prices. Flow-free intervals
can telescope without inventing intermediate observations; required flow
boundaries must still be supported. Negative equity remains unsupported.

Same-date charges belong to their economic transaction: purchase fees increase
contributions, while sale/distribution charges reduce proceeds. Separately dated
charges keep their dates. Recorded fees and taxes reduce returns once. Included
cash and securities trade internally; external fee funding enters once. Scope
membership determines which payments cross the boundary. Embedded fund expenses
are already in market prices and are not deducted again.

Exact event TWR remains an explicit calculation option. It requires genuine
ordering and supported before/after event valuations, and includes the event's
fees and income. Execution prices may support standalone security boundaries,
but do not value other assets in a portfolio. Neither method changes saved trade dates.
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

Imported swaps and rewards distinguish recorded asset units from supplied monetary
valuations. A swap's analytical proceeds and incoming basis do not imply a cash
payment. In-kind rewards add income and basis when their value is supplied, without
an external contribution. Own-custody transfers preserve original lots; staking
and unstaking do not recognize the same reward twice.

Asset fees remove units once and expense FIFO carrying cost under the import's
versioned portfolio policy. That expense is not an inferred market sale. Internal
transfers and swaps cancel before valuing whole-portfolio flows. A selected subset
may still need a transfer-date price or swap valuation. Thus portfolio gain can
be supported while a particular lot cost, gain component or subset return remains
unknown. See [transaction import](import.md#transactions) for evidence and review.
