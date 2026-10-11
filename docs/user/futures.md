# Futures and leverage (development)

This development branch supports manually recorded futures and account balances
in the normal portfolio workspace. It is not part of the published v0.1.0
installers. EUR, USD and GBP are supported as account, contract-settlement and
reporting currencies. No broker connection or order execution is involved.
Futures transactions are entered manually; transaction CSV imports are deferred.

## Add an actual futures position

Open **Positions → Add position → Future**. Choose **New opening trade** or
**Already-open position snapshot**, then enter the date, exact contract maturity,
signed whole contracts and price. Positive quantities are long; negative
quantities are short. Select a listed product or supply a manual contract's
currency, point multiplier and tick. Verify the exact maturity rather than
assuming that a product code identifies one contract.

Select an account or create one. If its cash already appears in Holdings, choose
**Adopt existing cash**: the account balance replaces that holding's contribution
instead of creating another asset. Otherwise enter cash that is not already
recorded elsewhere. Enter cash only, not a broker's account net liquidation value.
Use zero when there is no cash to record. An account can subsequently have separate
EUR, USD and GBP balances under **Futures → Cash & financing**.

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
recorded net P&L = settled trading P&L + open P&L − fees − entered taxes
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
using the same popup as ordinary holdings. It shows capital, net P&L, notional
exposure and contract history, including closed expiries. The holdings list keeps
its capital-value columns; futures exposure is available in the popup and the
Overview **Market exposure** view. A roll retains the investment's name,
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

## Roll from position details

Open a futures investment and choose **Roll contracts**. Select the current
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

The popup separates **Realized P&L** (recorded daily/final settlements and closing
trades) from **Unrealized P&L** (open-contract movement since the clearing basis).
Both are trading amounts before costs. Daily settlement realizes P&L even when
contracts remain open. A settlement awaiting payment is already realized; its
later receipt must not realize it again. **Net P&L since recorded start** also
includes attributed fees, interest and manually entered taxes. Reporting amounts
use current FX; pre-snapshot performance remains unknown.

Choose **Settlements** to see every effective daily/final settlement across the
investment's contract history: date, expiry, signed quantity at settlement, price,
realized amount, currency, value date, cash status, actual posting date and source.
Corrections update this effective view while the original journal facts remain
preserved. Closing trades contribute to realized P&L but are not daily settlements.
Downloaded market observations do not create entries in this recorded history.

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

For example, with invented values: €20,000 cash, two contracts at 1,000 points,
€10 per point and €8 fees leave €19,992 cash. A mark of 1,010 creates €200
unsettled P&L, so equity is €20,192. Posting that settlement leaves €20,192 cash
and zero unsettled P&L at 1,010. Reserving €4,000 as collateral does not change
any of those wealth totals.

## Marks and free data

**Futures → Marks & FX** keeps valuation observations separate from transactions.
Fetch an exact-contract settlement for the selected business date, or save a
manual dated mark and source. **Save mark only** changes valuation; it does not
post settlement cash. Record the corresponding settlement separately after
reviewing it. Dated manual FX observations are also supported.

The product catalog includes MSCI World EUR/USD/GBP, EURO STOXX 50, FTSE 100 and
DAX, Mini-DAX and Micro-DAX futures. Free public data availability varies by
product, maturity and date. A product appearing in the catalog does not guarantee
an available observation or complete historical archive. A failed fetch does not
invent a price or replace an exact expiry with a nearby contract. Manual entry
remains available. Sources and observation dates should be reviewed alongside
values; an old clearing basis is not a fresh market quote.

## Margin accounts and collateral

For an account without a futures position, open **Positions → Position tools →
Accounts & futures**, then **Cash & financing → Create account**. This opens the
same account workflow without requiring a placeholder contract.

**Cash & financing** records opening balances, reconciliations, deposits,
withdrawals, transfers, actual currency-conversion legs, existing loans,
borrowing, repayment and interest. A reconciliation records the difference needed
to reach the entered balance; it is not classified as a futures gain. Negative
cash is a signed cash balance, not an automatically duplicated loan. Record a
separate loan only when it is a separate obligation. Borrowing increases cash and
debt equally; principal repayment decreases both. Interest is a separate expense.
Ordinary security purchases and sales do not acquire transaction bookkeeping
through this account workflow.

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

Documented fund factors describe approximate current leveraged/inverse ETF
exposure. They create neither shareholder debt nor additional wealth. Risk uses
the fund's own return history without applying its embedded leverage again.
Futures risk uses a compatible native-currency history and translates native
variation; cash and claims retain their separate FX risk. Available proxy coverage
is narrower than contract-entry coverage. Incomplete compatible history withholds
unsupported integrated risk rather than silently substituting another index.

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
