# Futures and leverage {#futures-and-leverage-development}

Version 0.2.0 supports manually recorded futures and account balances in the
normal portfolio workspace. EUR, USD and GBP are supported as account,
contract-settlement and reporting currencies. No broker connection or order execution is involved.
Futures transactions are entered manually; transaction CSV imports are deferred.

## Add an actual futures position

Open **Positions → Add position → Future**. Choose **New opening trade** or
**Already-open position snapshot**, then enter the date, exact contract maturity,
signed whole contracts and price. Positive quantities are long; negative
quantities are short. Select a listed product or supply a manual contract's
currency, point multiplier and tick. Verify the exact maturity rather than
assuming that a product code identifies one contract. Listed products offer the
actual maturities published by Eurex, with no preselected contract. Rolls offer
only later listed maturities. **Historical / manual maturity…** permits an expired
or otherwise unlisted exact contract; verify that date from your trade records.
If the listing source is unavailable, only the explicit manual option is offered.

Select an account or create one. If its cash already appears in Holdings, choose
**Adopt existing cash**: the account balance replaces that holding's contribution
instead of creating another asset. Otherwise enter cash that is not already
recorded elsewhere. For a **New opening trade**, **Account start** defaults to **Existing balance**, a dated
snapshot that leaves earlier account history unknown. Choose **Newly funded account**
only when funding began from a confirmed zero account balance. Enter **Funding date**
and a positive **Initial contribution** in the account currency; the funding date
must be no later than the trade date. This records zero inception
and the dated contribution separately. The contribution must come from outside
the tracked scope; use **Transfer** for cash moving between included accounts.
Enter cash only, not a broker's account net liquidation value. Use zero when there
is no cash to record in an existing-balance snapshot. An account can subsequently have separate
EUR, USD and GBP balances under **Futures → Cash & financing**.
An **Already-open position snapshot** creates an existing-balance account;
it cannot establish a new account's complete opening trade history. For an account
funded before a later contract snapshot, equity and returns before that snapshot
remain unknown even when the earlier cash balance is known.

For an existing position, enter its **last settled price / clearing basis** and
current cash at the snapshot date. That cash already includes earlier settlements;
do not enter those gains again. Earlier transaction performance remains unknown.
For a new trade, enter the actual fill and any opening fees. Optional category
and index-holdings proxy choices control classification and approximate exposure.
Choose **Review changes**, inspect the proposal, then **Save reviewed changes**.
The record persists in the existing workspace without a sandbox marker.

## Investment capital and contract history

The normal entry form also asks for an **Investment name** and **Initial allocated
capital** in the contract's settlement currency. This declares how much existing
account cash belongs to the investment. It neither creates a deposit nor buys the
contract's notional. Zero is allowed; monetary P&L remains available, while a
percentage return requires positive initial capital. Collateral remains an
independent account reservation and is not added to investment value.

Overview and Positions show one persistent investment row across contract rolls:

```text
investment capital value = allocated principal + recorded cumulative net P&L
recorded net P&L = realized trading P&L + unrealized trading P&L − fees − entered taxes + net attributed interest
reference return = recorded net P&L / fixed initial allocated capital
```

Explicitly attributed interest is included in net P&L. Account-level expenses
without a contract attribution remain outside investment performance. The gain
includes closed contracts and pending payments; posting a settlement or receiving
pending cash changes its location, not the gain. Market exposure remains separate.
For example, an invented €20,000 allocation and €1,000 net gain display as a
€21,000 investment with €1,000 gain and 5% reference return, regardless of how much
of that gain has already settled into cash. If the account also contains €3,000
unallocated cash, that appears separately. Attributed cash is counted only once.

Select the investment in Overview or Positions to open **Position details**,
using the same popup as ordinary holdings. The summary shows capital value, net
P&L and market exposure. **Chart**, **Settlements** and **Contract history** switch
the content, while **Edit position**, **Record transaction** and **Roll contracts**
open separate action forms. **Back to position** returns to the selected view.
Closed expiries remain in contract history. The holdings list keeps
its capital-value columns; futures exposure is available in the popup and the
Overview **Market exposure** view. Switching between market exposure and capital
allocation changes the chart and category summary; the positions list always
keeps actual investment values and capital percentages. A roll retains the investment's name,
initial capital and cumulative performance. Separate investments can use the same
account; each has its own capital attribution. Shared capital pools across
investments are not currently modeled.

Under **Futures → Positions & transactions**, **Edit investment allocation**
changes total allocated principal, excluding gains and losses. Its difference
from initial capital is net funding, not profit. The initial reference stays fixed;
this percentage is neither time-weighted nor annualized. A cash deposit/withdrawal
must still be recorded separately when actual account cash changes. An account
withdrawal can leave a negative unallocated remainder: this is an allocation
shortfall to reconcile, not an additional loan.

Existing journals retain their original account and contract records without
automatic allocation. Choose **Group an existing contract history as an
investment** to supply a name and capital at the recorded start. Explicitly linked
rolls join the same investment. For an already-open snapshot, its opening cash
already includes earlier settlements; performance before the snapshot stays
unknown. EUR, USD and GBP amounts are converted at current reporting FX. These
investment gains do not reconstruct historical FX gains on allocated capital.

## Account history

Recorded opening balances, contracts, trades, rolls, cash movements, income, fees
and taxes determine account history, including closed contracts. There is no
separate account-history confirmation. Earlier activity before an opening
snapshot remains unknown. Missing settlement prices and transaction amounts are
checked separately; downloading prices does not settle cash or change transactions.

## Roll from position details

Open a futures investment and click **Roll contracts**. Select the current
contract, roll date and number of contracts. Under **Close the current contract**,
enter its executed closing price. Under **Open the new maturity**, enter the exact
later maturity and its executed opening price. Enter the total roll fees once.

**Review roll** shows both contract legs and resulting account cash. **Save roll**
records both legs together. The same signed quantity opens in the new maturity;
a partial roll leaves the remaining contracts in the old one. The price difference
between expiries establishes the new basis, not an immediate profit. Investment
identity, capital allocation and cumulative performance are retained. This records
executed transactions; it does not send orders to a broker.

Use **Record transaction** in the popup for settlements, increases/reductions,
closes, fees and optional manually recorded tax payments. The **Edit position**
button at the top of the popup opens **Edit allocation**, which changes the investment name or attributed principal. Account-wide cash, financing,
collateral and corrections remain available under **Futures**.

## P&L and settlement history

The headline **Net P&L** remains the investment's cumulative gain across open
and closed contracts, after attributed fees, interest and manually entered taxes.
The gain shown in Overview and Positions uses this same figure. **Return** divides
it by fixed initial allocated capital; it is not a return on contract notional or
current margin. With zero initial capital, gain is available but return is not.

The compact breakdown beneath Net P&L and the expanded **Position details** use:

- **Unrealized P&L**: entry-to-current-mark trading P&L for quantities still held,
  including their daily settlements. Recording a settlement does not reset it.
- **Realized P&L**: entry-to-exit trading P&L for closed quantities, matched FIFO.
  Partial closes realize only the closed lots. Final settlement closes all lots.
  A roll realizes the old contract and starts the new one at its execution price.

Both figures exclude costs. Position details show their net fees, interest and
entered-tax adjustment to reconcile to Net P&L. These are performance labels, not
tax calculations. Daily cash settlement is a separate accounting dimension:
settlement and later receipt of pending cash never add to total gain a second time.
Reporting amounts use current FX; snapshots measure from the recorded snapshot
basis, with earlier performance unknown.

Choose **Settlements** to see every effective daily/final settlement across the
investment's contract history: date, expiry, signed quantity at settlement, price,
**Settlement P&L**, currency and cash status. Expand **Posting details** for the
value date, actual posting date, source and notes.
Corrections update this effective view while the original journal facts remain
preserved. Closing trades contribute to realized P&L but are not daily settlements.
Available daily settlement prices automatically create dated settlement entries
for eligible recorded positions. Their source and automatic origin remain visible
in **Posting details**. Other valuation marks do not create settlement transactions.

### Automatic history archive and comparisons

While the normal app is open, eligible contracts collect available daily settlement
history in the background. Startup fills missing dates within the previous 31
calendar days; a daily refresh rechecks the most recent week for source revisions.
Collection begins checking today at the published settlement reference time
(17:30 Europe/Berlin for the curated products). This is not a promised publication
time: the source must actually publish the requested daily settlement. Pending
publication retries every 15 minutes during the first two hours, then hourly for
recent dates; older gaps retry daily. History checks distinguish awaiting
publication, unavailable history and failed requests. Active contracts and recently closed or
rolled legs are eligible. Weekends and final-settlement dates are excluded;
source holidays or unavailable historical records remain visible gaps. Accepted
daily prices can update dated valuation immediately. Cash posting waits until
the business date has ended in Europe/Berlin, without another 24-hour delay.
Eligible archived prices are also applied at startup. The journal dates cash to
the settlement business date, after that day’s recorded trades, and updates the
clearing basis once. The same ledger
calculation updates investment P&L and value without counting the cash gain twice.
No fees, taxes or final-settlement transactions are inferred.

Saved observations survive cache clearing and closing or rolling the position.
Existing exact-contract settlement caches migrate into the archive. Source
revisions preserve the accepted price and both versions; they never rewrite
existing transactions. Manually corrected or voided settlement dates are not
recreated. Snapshot dates and periods already covered by a later cash balance or
reconciliation are skipped to avoid duplicating included gains. The notification bell
flags source changes, recorded-price differences and unavailable downloads.

Open **Settlements → History checks** in position details, or **Futures → Settlement
history** for all contract histories. **Check source history** compares any selected
period of up to 31 days against newly retrieved observations. The table compares
archived, retrieved and recorded prices. **Use retrieved price in archive** accepts
a source revision into the archive only; correcting an account transaction remains
a separate reviewed action. Observation versions retain source and retrieval time.
Free historical coverage is incomplete, so unavailable dates still require verified
manual records. Collection does not run while the app is closed.

### Retrieve missed daily settlements

Open the investment’s **Settlements → Retrieve settlements**. Select the exact
contract and a range of up to 31 calendar days ending before today, then retrieve.
The app uses free Eurex prices and displays each weekday as available, unavailable,
already recorded or ineligible. Weekend dates are skipped; missing weekdays are
not assumed to be exchange holidays. Availability varies by contract and date;
this is not a complete long-term historical archive.

Select available dates to record, inspect each day’s quantity and settlement P&L,
then choose **Review settlements → Save settlements** if they have not already
been applied automatically. Existing entries are skipped. This manual backfill
posts selected daily settlements after that day’s recorded trades, with cash
posted on the business date. For different cash value
dates, use **Record transaction** instead. Final/expiry-day settlements stay manual.

Backfill uses historical quantities, including partial closes and contract rolls,
and skips existing daily/final settlements. An opening snapshot already includes
its clearing date and earlier settlements; those dates cannot be posted again.
Gaps before a later opening cash balance, or on/before a cash reconciliation,
require manual reconciliation because the recorded cash may already include them.
The original journal entries and sources remain preserved.

To reconstruct a trade opened last week, record its actual opening date and fill,
with account cash from that date before the trade’s fees and later settlements.
Then retrieve and review the missing days. If using today’s cash and clearing
basis instead, record an **Already-open position snapshot**; earlier performance
cannot be recovered merely by downloading past market prices.

## Cash, settlement and exposure

The account journal distinguishes wealth from contractual exposure:

```text
open-contract unsettled P&L = sum of signed lot quantities × multiplier × (mark − clearing basis)
signed futures exposure = signed open contracts × multiplier × mark
net equity = ordinary assets + account cash + unsettled claims − explicit loans
```

Amounts are recorded in their native currencies and translated for reporting.
Futures notional is never added to wealth or deducted as a purchase price.
Changing reporting currency does not rewrite native transactions. Foreign cash,
claims and debt can change reporting value with FX; FX does not create a gain
on the full futures notional by itself. Missing valuations or FX remain unknown.

Under **Futures → Positions & transactions**, record settlements, signed increases
or reductions, full closes, rolls, final settlement, fees and optional tax
payments/withholding. Reductions match recorded lots FIFO for application P&L
attribution; this is not tax-lot accounting. A roll closes one expiry and opens
another. Its price gap establishes the new clearing basis rather than generating
an immediate gain or loss. Closing a contract retains the account's cash.

A settlement posting moves the price change since the clearing basis into cash
and resets that basis. A later mark therefore adds only the subsequent unsettled
change. When the cash value date is later, the journal retains a receivable or
payable until **Receive pending cash** records its payment. Transfers can also
remain in transit. Passing the value date alone does not post a receipt.

Fees and manually entered taxes reduce wealth once, either through cash or an
outstanding payable. Tax records are optional amounts entered by the user: the
app does not calculate taxes, infer a jurisdiction or give tax advice. Recorded
transactions can be reversed while retaining their original facts; dependent
entries may need correction first.

Under **Positions & transactions → Recorded transactions**, filter by account,
investment/contract, action or date. The journal shows readable names, applicable
amounts, quantities, prices and correction status. A dated claim is distinct from
a received cash payment. Internal identifiers and source details are under
**Entry provenance**.

Select **Transaction details → Correct entry** to edit prefilled facts, compare
the original and replacement, and choose **Review correction → Save correction**.
The replacement cancels the original's accounting effect and supplies corrected
facts in one revision-checked write; both records remain in history. Changed
inputs or a concurrently updated journal require another review. **Cancel
correction** discards the draft. **Reverse only** explicitly removes an entry's
effect without a replacement. Invalid dependent histories block saving and show
later entries to review. Linked rolls and collateral entries direct you to their
source workflows rather than allowing partial edits.

For example, with invented values: €20,000 cash, two contracts at 1,000 points,
€10 per point and €8 fees leave €19,992 cash. A mark of 1,010 creates €200
unsettled P&L, so equity is €20,192. Posting that settlement leaves €20,192 cash
and zero unsettled P&L at 1,010. Reserving €4,000 as collateral does not change
any of those wealth totals.

## Marks and free data

While the normal app is open, held curated contracts refresh from the free,
15-minute-delayed quote service behind Eurex's public product pages. Refreshes
run every five minutes during the weekday trading window and hourly outside it.
Failures back off for 5, 15 and then 60 minutes. **Refresh prices** retries now.
Collection does not run while the app is closed.

A timestamped exact-contract trade changes unsettled P&L, investment capital
value and signed exposure. It never posts cash or changes the clearing basis.
When the corresponding daily settlement posts, settled and unsettled amounts
change location without creating a second gain. A newer market quote remains
selected when an earlier settlement arrives late.

The supported lookup covers FMWN (EUR), FMWO (USD), FMWB (GBP), FESX, FDAX,
FDXM, FDXS (EUR) and FTUK (GBP). Each request verifies the product, monthly series,
canonical expiry, currency and multiplier. MSCI quote-feed maturity labels use a
different date from the statistics listing; the adapter verifies their unique
monthly series without altering the recorded contract expiry. It never substitutes
an index, ETF, daily contract or nearby maturity.

Availability depends on actual trading in that maturity. The website sometimes
returns settlement-like last prices without trade volume; those do not qualify
as intraday quotes. Deferred maturities and thinly traded products can therefore
remain at a dated settlement or recorded price. This public website is not a
guaranteed API or a promise of real-time data.

Routine delayed quotes show no age label; their source time and delay remain in
the value/P&L help. Settlement and market-closed details use a small **ⓘ** beside
the position name; click it or use the keyboard to see the date and market status.
Technical source details remain in the position view. Other affected
values retain **Stale**, **Manual**, **Recorded**, **Revised**, or **No price** labels. Conflicting prices at the
same quote time retain the first observation with neutral styling until a newer
quote or manual mark resolves valuation. Quotes become stale after 30 minutes
of scheduled trading time: closed overnight hours, weekends and published exchange
holidays do not age a recent closing quote. While the market is closed, the latest
available trade from its latest completed session uses compact **Market closed**
details instead of a stale warning, even if the last trade preceded the close by
more than 30 minutes. Its timestamp remains available; it is not presented as an
official closing price. Quotes from an earlier session, conflicting prices and
failed refreshes retain their qualifications. The trading-age rule resumes at reopening.
The latest session's official settlement also retains normal gain coloring while
the market is closed; it is qualified again when trading reopens. Manual marks
and execution-price fallbacks remain qualified.

The exemption uses the curated Eurex product schedules and published 2026–2030
common holidays (FTSE 100-specific closures are verified for 2026 only). Currency
payment holidays are not exchange closures. Unknown schedules and expiry days
fall back to the strict wall-clock rule. See the
[Eurex calendar](https://www.eurex.com/ex-en/trade/trading-calendar/holiday-regulations).
Date-only marks and recorded prices always disclose their business date. Stale/fallback-dependent P&L retains its value and sign with neutral styling.
Portfolio and category summaries indicate affected futures; closed contract legs
do not make an open investment stale. Expand a row's freshness label, or open its
help, for source time, retrieval time, declared delay and any refresh failure.
Downloading the same observation again never makes its price time newer.
Freshness labels and gain coloring update in place as time passes. Background
checks leave the selected view and open position dialog intact. New prices and
settlements update portfolio amounts after the dialog closes; its displayed
observation continues to age while it is open. Source failures and recovery
update in Notifications without refreshing the page.

**Futures → Marks & FX** keeps manual marks and FX separate from transactions.
Fetch an exact-contract settlement or save a dated manual mark and source.
**Save mark only** changes valuation; it does not post settlement cash. A verified
newer automatic quote may supersede a manual mark, while an ambiguous same-day
comparison keeps the valid manual mark. Manual records remain preserved.

Automatic quote history and collection status are private workspace files,
separate from manual observations, settlement archives and account transactions.
Failures retain earlier observations. Source revisions preserve history and never
silently correct account postings. Free settlement history is incomplete; verified
manual records remain available for gaps and unsupported contracts.

## Market history in position details

**Chart** is the default view in the futures popup. It uses the same
chart, period controls, background loading, cache and retry behavior as ordinary
holdings. This shows market context, separate from your recorded settlements,
contract valuation and personal P&L.

The curated references are:

| Contract | Chart reference | Convention / units |
| --- | --- | --- |
| FDAX, FDXM, FDXS | DAX (`^GDAXI`) | Gross total return; index points, EUR index |
| FESX | EURO STOXX 50 (`^STOXX50E`) | Price return; index points, EUR index |
| FTUK | FTSE 100 (`^FTSE`) | Price return; index points, GBP index |
| FMWN | MSCI World, directly from MSCI | Net total return; index points, EUR index |
| FMWO | MSCI World, directly from MSCI | Net total return; index points, USD index |
| FMWB | MSCI World, directly from MSCI | Net total return; index points, GBP index |

MSCI World daily index levels come directly from the public chart service behind
[MSCI’s index data tools](https://www.msci.com/end-of-day-history), requesting
index 990100, net total return (`NETR`) and the contract’s EUR/USD/GBP currency.
The response must confirm all three identifiers. This is the actual index series,
not an ETF approximation. Other configured index histories use Yahoo Finance.

MSCI **Max** requests daily history from 2000; earlier history is not included.
The chart shows the first and last returned observation dates. This public chart
endpoint is not a guaranteed data service. If it fails, verified cached history
remains visibly stale or the chart reports unavailable; it never silently falls
back to an ETF. The adapter does not populate settlement or risk data.

All these index charts retain native index points and display their currency and
return convention. Changing portfolio reporting currency does not convert their
levels into cash prices. Ordinary holdings retain their existing portfolio/native
currency controls and historical FX conversion.

Automatic mapping requires a recognized Eurex product, matching currency and
return convention. A custom or unmatched contract has no inferred reference.
Under **Chart details**, an explicit **Chart reference** selection provides a
comparison for the current session. The chart subtitle labels it **Comparison**.
The expanded details explain that its match to the contract is unverified.
This selection does not change the saved contract, look-through proxy, risk inputs
or account records. Rolls of the same
product share a reference; investments spanning different markets have a market
selector in **Chart details** rather than a spliced chart. Source, retrieval time,
coverage and interpretation notes also live there; stale or unavailable history
remains visible beside the chart.

Charts do not represent futures prices, carry, contract quantities, leverage,
roll costs or personal returns. Unavailable history stays unavailable; stale
cached history is labelled and can be retried. Unexpected source currencies
withhold the reference chart rather than relabelling prices.

## Margin accounts and collateral

For an account without a futures position, open **Positions → Position tools →
Accounts & futures**, then **Cash & financing → Create account**. This opens the
same account workflow without requiring a placeholder contract.
It offers the same **Existing balance** and **Newly funded account** choices.
The latter requires a **Funding date** no later than the balance date and a
positive external **Initial contribution**; a snapshot alone does not prove zero
before inception.

**Cash & financing** records opening balances, reconciliations, deposits,
withdrawals, transfers, actual currency-conversion legs, existing loans,
borrowing, repayment and interest. A reconciliation records the difference needed
to reach the entered balance on its selected date; later cash movements remain
intact. It is not classified as a futures gain. Negative
cash is a signed cash balance, not an automatically duplicated loan. Record a
separate loan only when it is a separate obligation. Borrowing increases cash and
debt equally; principal repayment decreases both. Interest is a separate expense.
Ordinary security purchases and sales do not acquire transaction bookkeeping
through this account workflow.

New deposits and withdrawals are explicit cash flows, so they do not prevent
retrieval of earlier missing settlements. Older untyped cash adjustments retain
their original records and are conservatively treated as reconciliations when
checking settlement eligibility.

Under **Collateral**, link an existing security holding to its owning account,
reserve shares/units or native cash, and enter a haircut. Security reservations
retain fractional quantities and cannot exceed the units owned. Their eligibility
uses current valuation after the haircut. The security itself remains an ordinary
asset counted once; a reservation is not another holding or an expense.

Enter dated initial and maintenance requirements in their currencies. Coverage
compares the explicitly pledged assets with those entered requirements; it is
not broker buying power or a prediction of a margin call. Foreign collateral can
support declared coverage after conversion and haircut, but does not automatically
supply settlement-currency cash. Pending transfers and payments are displayed
separately. Missing valuation/FX withholds unsupported coverage totals.

**Adopt existing liability** moves a legacy EUR liability into the account
journal as existing debt, with no cash deposit. Its original settings remain
preserved and are excluded from duplicate portfolio totals. Adopt remaining
legacy liabilities before switching their workspace to non-EUR reporting.

## Exposure, risk and planning

Capital targets retain their existing meaning. Exposure targets specify their
basis, category composition and overall multiple. Futures contribute signed
notional; gross exposure is calculated before long/short netting. An explicitly
linked ETF can approximate underlying index holdings, with unresolved weights
kept visible rather than renormalized away. Category filters do not replace the
whole-portfolio net-equity denominator for portfolio impact.
Risk analytics additionally shows standalone subset beta and volatility using
the selected investment's funded capital. Ordinary selected holdings normalize
their covered net values; futures use selected backing capital with unsettled
claims counted once. A bare contract without backing capital still has a
portfolio beta contribution, but no standalone capital-based risk estimate.

Documented fund factors describe approximate current leveraged/inverse ETF
exposure. They create neither shareholder debt nor additional wealth. Risk uses
the fund's own return history without applying its embedded leverage again.
Futures risk uses a compatible native-currency history and translates native
variation; cash and claims retain their separate FX risk. Available proxy coverage
is narrower than contract-entry coverage. Incomplete compatible history excludes
that position and shows a labelled estimate for covered holdings with the same
whole-portfolio net-equity denominator. Exclusions are shown beside the estimate;
another index is never silently substituted. Missing positions can increase or
decrease actual risk, so this is not a complete portfolio-risk estimate.

Actual-futures plans require permitted positions, integer limits, categories,
initial-margin assumptions and fees. Funding is checked separately by account and
currency. Unsettled losses and pending debits reserve cash; unposted gains and
pending receipts are unavailable. Recorded account totals remain a fixed
requirement floor; other open positions in a planned account need their own
declared margin coverage. The planner shows remaining gaps or infeasibility and does not
transfer funds, convert currencies, borrow or execute trades. Funded holdings
continue to use the capital planner.

## Studies in the normal workspace

Open **Futures → Studies** and select a recorded position or a manual scenario
contract. This copies inputs into a hypothetical experiment. Enter scenario bank
and broker cash by EUR/USD/GBP, opening FX, optional dedicated noncash collateral,
margin, credit/debit interest, execution costs and manually scheduled payments.
Do not include the same scenario asset in both cash and noncash collateral.
Running a study never changes actual holdings or account transactions.

Position, horizon, mode and **Run study** sit above the results. Detailed contract,
cash/collateral, funding/cost and history assumptions are grouped below them.
Policy components and path accounting are expandable. Changing inputs invalidates
the displayed generalized-study result until another run. Saved scenarios retain
all their explicit inputs and evidence requirements. Legacy sandbox funding
studies similarly keep reserve and candidate controls beside results, with policy
rules and history settings below; any retained prior result is marked stale.

Available modes are **Forward Monte Carlo**, **Rolling Monte Carlo**,
**Historical settlements**, **Historical proxy reconstruction** and
**Funding policy cohorts**. Rolling modes need an explicit roll calendar.
Historical modes require dated observations and identified sources. Free proxy
and dated FX downloads are available, but downloading an ETF does not establish
that it matches a contract's underlying index, currency or price/net/gross-return
convention. An incompatible proxy cannot silently substitute for the index.
Effective carry is an explicit reconstruction assumption, not a second cash
financing charge applied to observed futures prices.

Funding policies use finite reserves, ordered source currencies, target amounts
or maintenance buffers, optional notional scaling, resets, transfer delays and
re-entry. Conversion occurs at dispatch; converted funds acquire the destination
currency's FX exposure while in transit. The maintenance/liquidity check precedes
transfer arrival, so an arrival cannot retroactively prevent a close. The
normal-workspace model closes strictly below maintenance, or beyond the explicitly
allowed settlement-currency debit. Equality with maintenance alone does not close
the position. Foreign collateral does not automatically pay variation margin.
Endpoint positions close without re-entry; losses and deficits are not clipped
to zero. Optional excess withdrawals use the declared delay and costs.

Forward FX uses the supplied constant rates; no stochastic FX calibration is
implied. Historical funding cohorts translate observation dates to the scenario
start and rebase historical FX movements to opening FX. Credit/debit rates and
optional dated overrides remain explicit assumptions. Free bank-rate references
can populate bank yield; they do not determine broker financing charges.

Policy comparisons use matching paths and opening reserves, with event tables
and reconciliation of market P&L, FX, interest, costs and entered payments.
Cash/index benchmarks use the same starting reporting capital; the index stays
long and invested after futures exit. Those benchmarks exclude the futures fee
and manual-tax schedule. Settlement-only paths do not manufacture a total-return
index benchmark. Overlapping historical cohorts are dependent observations,
not independent forecast probabilities. These are close-to-close scenarios,
not intraday liquidation or broker-account reconstructions.

Save named study settings explicitly. Calculation results remain session data;
changing inputs invalidates the displayed result. Data provenance, proxy
qualifications and uncovered periods remain part of interpreting a study.

## Legacy EUR sandbox

Earlier FMWN experiments remain separate from actual accounts. Their saved
simulations are not automatically promoted into executed trades. To create an
independent legacy sandbox, stop the source portfolio and choose a new destination:

```sh
uv run portfolio-app --data-dir /path/to/portfolio --fork-futures /path/to/new-sandbox
```

The source is preserved; the copy contains a simulated FMWN position. Existing
sandboxes open with `--data-dir`. Review dated prices, funding and assumptions.
The legacy views retain **Funding policies**, **Stress & history** and
**Investment outcomes**, with the saved mock editor separate from study inputs.

The legacy funded-strategy formula remains reference capital plus net funding
plus closed/current contract P&L less fees. Reference capital stays fixed;
funding changes wealth rather than gain. Daily MTM shows observed settlements for
the current contract at constant quantity and cannot reconstruct all historical
fills or cash postings. Legacy comparisons retain their EUR proxy, quarterly roll
and ECB/manual-financing conventions. Their funding studies retain the historical
maintenance-before-receipt ordering and do not become actual account history.

Legacy sandbox integration remains EUR-only. When actual journal accounts are
present, the sandbox stays a separate simulation rather than an extra actual
portfolio position. Keep its simulation capital out of the real account journal.

All account records, observations, legacy settings and study settings are private
workspace data. See [Storage, privacy and recovery](storage.md) for backup and
revision protection.
