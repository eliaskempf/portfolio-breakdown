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
