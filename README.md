# Portfolio breakdown

A local CSV/YAML-backed portfolio explorer built with Python, Streamlit, and
Plotly. It supports current EUR valuation, hierarchical classifications,
filtering, allocation charts, optional position targets, and ETF look-through
for configured fund snapshots.

## Position metrics and portfolio analytics

Under **Manage positions → Positions**, select a **Position metrics** preset:
**Valuation**, **Income & fees**, or **Risk**. Choose the metric columns to show;
numeric columns sort numerically and unavailable values sort last. Enable
**Show instrument metrics** for definitions, source links, units, retrieval dates,
and fund-fee maintenance. The default list does not fetch fundamentals or history.

Stock fundamentals include trailing/forward P/E, price/book, price/sales, market
capitalization, trailing cash dividend yield, payout ratio, growth, profit margin,
and return on equity. ETF metrics include annual fees, cash distribution yield,
assets, and separately labeled fund valuation ratios when the provider supplies
them. Unavailable values are blank; nonpositive P/E is not meaningful. Monetary
fundamentals retain their reported currency rather than being silently added or
converted across listings.

In **Overview**, enable **Show portfolio analytics**. Its account/category scope
is independent of exposure filters and position-list search. It shows instrument
concentration (including repeated instruments across accounts), effective number
of holdings, underlying company concentration using existing ETF snapshots and
company mappings, estimated annual fund costs, and recorded-cost gain contributors.
**Load portfolio fundamentals** adds valuation and cash-yield aggregates.

Aggregate P/E uses `sum(value) / sum(value / PE)` for covered profitable direct
equities. Fund ratios are not blended into this statistic. Trailing distribution
estimates use current value times reported cash yield; these are neither a forecast
nor dividends actually received. Known accumulating share classes have zero cash
distributions. Fund costs use current value times the annual fee rate; these costs
are already reflected in fund prices and are not deducted again from gains.
Every aggregate reports coverage; missing values are never assumed to be zero.

**Load historical risk** adds beta, annualized volatility, benchmark correlation,
holding correlations, and contributions to volatility. Defaults are three years
and `IUSQ.DE`, a EUR-listed MSCI ACWI ETF benchmark proxy. Both are configurable;
one- and five-year windows are also available. Risk describes today's weights
held constant across historical weekly returns, **not personal historical returns**.
It does not require transaction history and does not reconstruct purchases or sales.

Risk uses dividend/split-adjusted daily prices and historical FX to EUR, then the
last available observation in each completed Friday-ending week. Missing weeks
are not forward-filled, and returns never bridge missing weekly endpoints. All
included holdings and the benchmark share one sample of at least 52 weekly returns.
Volatility is annualized with `sqrt(52)`; beta is covariance with the benchmark
divided by benchmark variance. Explicit EUR cash has zero return. Manual/unlisted
assets without suitable histories are excluded. Partial estimates renormalize
covered holdings and show exclusions; missing valuations prevent reporting
whole-portfolio coverage. Negative risk contributions indicate diversification.
These estimates do not include Sharpe ratios, forecasts, or an actual portfolio
return history.

Fundamentals and adjusted histories use separate private caches under the selected
workspace's `.cache/`, with a 24-hour lifetime and a 15-minute failed-request
cooldown. Explicit refresh retries immediately. Failures retain visibly stale
cached data and never block ordinary portfolio calculations. Unadjusted chart
histories, where available, are separate from risk histories. Demo analytics are
explicitly invented and never fetch live market data.

Issuer fee observations in the public metadata catalog are dated, not automatically
refreshed. They match exact ISINs; holdings proxies never supply another fund's fee.
Provider annual expense ratios are a labeled fallback. **Maintain fund fee** saves
optional overrides to private `fund-fees.json` with exact share-class identity,
annual rate, source, verification date, and verified accumulation policy. Rates
are entered as percentages in the UI and stored as fractions. Verification older
than 90 days is flagged. Removing an override restores the issuer/provider value.
Holdings, classifications, and targets require no migration.

## Run

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), then:

```bash
uv sync
uv run portfolio-app
```

`uv` manages Python 3.12 and the project environment. Open the local URL printed
by Streamlit. The default launch uses live market data and your private local
data directory, `data/portfolio/`. A fresh portfolio starts empty; create positions
under **Manage positions**. They remain saved when you stop and restart the app.

The sidebar's **Portfolio workspace** switch opens **My portfolio** or the
editable **Demo portfolio**. Switching clears position forms and filters so an
unfinished edit cannot be applied to the other portfolio.

For a deterministic offline demonstration:

```bash
uv run portfolio-app --demo
```

Each app start generates deliberately invented holdings, classifications, ETF
weights, prices, and FX in a fresh temporary demo directory. You can add and edit
dummy positions with the same controls as your real portfolio. Demo edits survive
page refreshes and workspace switches while the server runs. Stopping and
starting `portfolio-app` resets them. The temporary directory is removed on a
normal shutdown; a new launch always creates fresh data even after a crash.
`--demo` starts with the demo workspace selected; it does not reset personal data.
Demo prices and FX are explicitly labeled in the UI and never used
as a fallback for live prices. The example portfolio has €744 in valued
positions, including €520 in the AI sleeve, plus one intentionally unvalued
example to demonstrate missing-data handling.

The persistent data directory is `./data/portfolio` relative to your launch directory. You
can use an independent directory with your own files:

```bash
uv run portfolio-app --data-dir /absolute/path/to/my-data
```

Earlier working files in `data/` or `data/demo/` are preserved. To open the older
root-level portfolio explicitly, run `uv run portfolio-app --data-dir data`.
`--data-dir` always identifies the persistent portfolio, even with `--demo`.

The package does not rely on the repository being on `PYTHONPATH`. When running
from another directory, use the installed `portfolio-app` command or
`uv run --project /path/to/portfolio-breakdown portfolio-app --data-dir /path/to/my-data`.
Streamlit options are forwarded, for example `--server.headless=true` or
`--server.port=8502`. The app is intended for local use.

## Private data and Git

The entire `data/` directory, personal handoff, imports, exports, reports,
screenshots, backups, caches, credentials, and local editor/agent settings must
stay off Git. `.gitignore` excludes them, including CSV/YAML/JSON data files
outside the default data directory. Tests generate synthetic fixtures in
temporary directories and never read your working portfolio. Package builds
include only approved source and documentation paths.

Enable the repository's additional commit guard after cloning:

```bash
uv sync
git config --local core.hooksPath .githooks
uv run portfolio-check-private --tracked
```

The hook checks the Git index, rejecting private paths even if force-staged,
unapproved file types, symlinks, and recognizable credentials. It fails closed
if the check cannot run. Keep `uv` on your PATH, or configure its absolute path
using `git config --local portfolio.uvPath /path/to/uv`.

Before committing, run `uv run portfolio-check-private --staged` and review the
staged diff. Automated checks cannot recognize every personal detail pasted
into permitted source or documentation files. Never copy private data into
those files or bypass the hook. Local data persists independently of Git;
back it up separately if needed.

## Strategic allocation and balance maintenance

Open **Manage positions → Strategic allocation** to preview enabling the
versioned bucket model. Review source-position assignments and within-bucket
targets before saving. Existing whole-portfolio target cells remain preserved;
no global percentages are automatically chosen. The migration adds persistent
position keys and activates a private `allocation.yaml` only after the additive
holdings update succeeds. Previous files are retained in private backups.

Buckets form a non-overlapping parent tree. Each position belongs to one leaf;
blank assignments remain visible as Unassigned. Bucket targets use their parent
as denominator, while position targets use their owning bucket. Global position
targets multiply these fractions. Missing targets remain unknown. Only sibling
allocations needed for a calculation must be complete and total 100%.

With strategic allocation enabled, **Overview** shows the source-position budget
tree as a sunburst. Click a category to update the chart, allocation table, and
positions together; use **Back** or **Category** to navigate. Current and target
percentages use the selected category as denominator. Empty categories remain
selectable and keep their targets without gaining artificial chart area. Missing
prices leave full percentages blank and the chart shows only known value.

**Exposure** contains the existing sector/label and ETF look-through analysis.
Its filters do not change strategic ownership or the whole-portfolio overview.
**Manage positions → Strategic allocation → Assign positions in bulk** offers
**Select all**, a destination bucket, and an assignment button. Assignments keep
quantities, buy-ins, classification labels, and within-bucket target percentages;
review the destination's target total after moving positions.

Use the top-right menu's **Light**, **Dark**, or **System** option for appearance.
The browser remembers the choice. Charts, tables, editors and search use the same
theme.

Ignore empty positions redistributes
position targets within each bucket only. An empty bucket retains its strategic
target and appears as planned capacity. Saved targets do not change.

**Rebalance → Planning scope** offers portfolio contributions and within-bucket
planning. Portfolio contributions first minimize deviations from bucket ranges,
then bucket target gaps, before allocating each budget internally. Position
constraints and the shared trade limit can leave some reserved money unallocated;
that cash stays visible in the final portfolio denominator. Within-bucket plans
use that bucket's post-contribution value and display the macro impact. They do
not require unrelated buckets' position targets. Protecting a bucket from selling
also protects its descendants while allowing contributions. Temporary caps state
whether their denominator is the whole portfolio or the selected bucket.

**Manage positions → Update balances** replaces several quantities and optional
buy-ins in one atomic save. Choose **Average per unit** or **Total buy-in** above
the table. Confirm quantities separately from
market-price timestamps. The operation preserves targets and classifications;
repeating a snapshot does not add units. After replacement, retained purchase
batches are historical context rather than a complete ledger. Additional
purchase batches must have dates after the balance-confirmation date.

Instrument type is independent of allocation ownership. The optional Underlying
exposure field distinguishes an equity fund from a known non-equity vehicle;
leave mixed or uncertain exposure unknown. Crypto search preserves
exact provider identifiers and allows missing ISINs. Quantity inputs support
fractions, and crypto prices older than 24 hours carry a continuous-market
freshness note. Buy-in currency remains separate from quote currency. For assets
without provider pricing, an optional manual unit price requires its currency,
date, and explicit quantity unit; clearing it restores provider pricing.

The optional **Show stock-only company exposure** view excludes selected source
buckets before fund expansion. It reports direct and fund-derived contributions,
selected-stock and whole-portfolio percentages, and unresolved coverage. A fund
manifest may declare `equity_fund: true` when its snapshot represents an equity
universe; constituent rows can additionally specify `instrument_type` to identify
known non-equity components. Residual weights are retained as unresolved exposure.
Unknown composition leaves the stock-universe percentage blank.

Company merging uses exact security identifiers. Optional private
`company-identities.yaml` maps `security:<ISIN>` or `instrument:<asset ID>` keys to
explicit company IDs when separate share classes or ADRs should be combined.
Names are never fuzzy-matched. Core fund snapshots use the existing manifest and
CSV format; fund-specific adapters require the actual fund and listing identity.

The legacy target and rebalancing instructions below apply to workspaces that
have not enabled strategic allocation.

## Edit your data

`holdings.csv` has one row per position. Required columns are `id`, `name`, and
`shares`; optional columns are `ticker`, `isin`, `acquisition_price`, `portfolio`,
`account`, `acquisition_currency`, and `target_allocation`. The app-managed
`purchase_history` column stores bulk-entry records and is excluded from filters
and allocations. Other additional text columns become metadata filters and allocation
dimensions. Shares must be finite and nonnegative; zero-share positions are
allowed. Acquisition price is optional and is displayed as entered.

```csv
id,name,ticker,isin,shares,acquisition_price,portfolio,account
example,Example Company,DEMO,,1,10.00,Demo portfolio,Demo account
```

The stable `id` identifies an instrument. You may repeat it across account or
portfolio rows; its name, ticker, and ISIN must be consistent. Holdings remain
separate positions in the table and merge by asset ID for holding allocation.
Different exchange listings need distinct IDs. Blank tickers are allowed and
leave the position unvalued. Prices are looked up using tickers, not ISINs.
Tickers and ISINs are trimmed and normalized to uppercase on loading, including
tickers displayed in holding selectors and allocation charts. Stable IDs remain
unchanged so classification links are preserved.

To display targets, add an optional `target_allocation` column. Enter a fraction
such as `0.15` or a percentage such as `15%`; leave unavailable values blank.
Targets apply to each position as a share of the whole portfolio. The holdings
table shows the column only when at least one selected position has a target,
keeps missing targets blank, and does not renormalize targets when filtering.
Targets are not metadata filters or inferred from current weights. Split a
holding's target across its account rows if you maintain multiple positions.

Allocation tables derive label and hierarchy targets by summing the underlying
position targets, using the same membership splits as current allocations.
**Target (% of portfolio)** and **Current (% of portfolio)** share the
whole-portfolio basis. **Current − target (pp)** is positive above target and
negative below. Filtering narrows the contributing positions without rescaling
their targets. Parent targets include their descendants; do not sum parent and
child rows together.

ETF look-through distributes an ETF target using the snapshot's constituent
weights, including residual Other. Targets from direct positions and ETF constituents combine by
asset identity. The optional SMH display group sums its fund and stock targets.
If any contributing position lacks a target, the target and gap stay
blank and a **Known target subtotal (%)** shows the available portion. An
explicit zero target is complete. Missing portfolio prices leave the whole-
portfolio current comparison and gap blank while preserving known targets.

You can save a position with **zero shares and a nonzero target** to plan a new
investment. Its current value is zero without requiring a quote, and its target
contributes to its labels immediately. Add classification labels for new assets
in the private YAML file; otherwise they appear under Unclassified.

The sidebar’s **Ignore empty positions** hides zero-share rows from analysis
and the rebalancing calculator. Their combined target is divided equally among
remaining unique asset IDs, then equally among each asset’s held account rows.
This is an equal percentage-point increment, not a proportional scaling. For
example, removing an invented 20% planned target adds 10 pp to each of two
remaining assets. Saved positions and targets remain unchanged and editable in
**Manage positions**. Switching the option resets position filters. Redistribution
requires complete targets if any targets have been entered; it never treats an
unknown target as zero. With no targets, the option simply hides empty positions.

The **Rebalance** tab offers three read-only calculation modes:

- **Fewest trades (buys and sells)** reaches every target range with no new money,
  minimizing changed position/account rows first and total turnover second.
- **Minimum new money (no sells)** finds the smallest fully invested contribution
  that reaches every range, then minimizes trades at that contribution.
- **Allocate new money** fully invests a given amount using at most the chosen
  number of trades. It minimizes the sum of distances outside target ranges,
  then trade count, then distance from exact targets. A comparison table and
  plan selector show whether additional trades offer enough improvement.

In **Allocate new money**, enable **Limit buys to selected positions** and
choose the instrument/account rows in **Positions eligible for buying**. Then
choose **Distribution**:

- **Rebalance selected positions** (default for a selected subset): account for
  current holdings and final portfolio value, prioritizing larger shortfalls.
  Minimize the sum of squared percentage-point gaps to exact targets under your
  purchase constraints. Tolerance ranges affect the reported status, not this
  split. Configure **Selection intent** and **Minimum purchase (EUR)**:
  - **Buy every selected position** (default): buy at least the minimum amount
    for every selected row, including overweight and zero-target rows. The
    initial minimum is €25 and can be changed. Insufficient budgets show the
    required contribution and shortfall; positions are never silently skipped.
  - **Allow skipping positions**: choose the best allocation, with every
    suggested buy meeting the minimum. **Prefer fewer trades** is off by default.
    Enable it to set **Maximum trades** and compare allocation quality across
    trade counts. **Allowed extra target error (pp)** defaults to 0.1 pp: choose
    the fewest trades whose root mean squared (RMS) target gap is within that
    amount of the best plan under the cap. Zero allows no additional error.
    The suggested plan is selected initially; any comparison plan can be inspected.
- **Limit allocations for this rebalance** (within Rebalance selected positions):
  enter an optional **Max allocation after rebalance (%)** for each selected
  instrument/account row. Blank means no cap; zero prevents further buys.
  The cap uses final portfolio value including the entire contribution and
  any unallocated cash. Saved targets keep guiding the allocation and are never
  overwritten by temporary caps. A position already above its cap receives no
  buys; this buy-only calculation does not force a sale. A cap that conflicts
  with Buy every selected position or Minimum purchase produces an explanation.
  Caps reset when the selection changes and are not saved to portfolio files.
  The calculator invests as much as the caps, minimum buys and trade limit allow,
  then minimizes squared target gaps. Remaining money appears as **Unallocated
  cash**, included in the percentage denominator; displayed position percentages
  may total less than 100%. Fewer-trade suggestions preserve the maximum amount
  that can be invested; comparison rows also show each plan's unallocated cash.
- **Spread by target weights**: divide the contribution in proportion to the
  selected eligible targets. For example, targets of 20% and 10% receive two
  thirds and one third of the new money. Zero targets receive nothing.
- **Optimize rebalancing**: choose buys that improve the whole portfolio's
  allocation, with a trade limit and trade-count comparison. This can put the
  entire contribution into one position.

RMS target gap is the square root of the mean squared percentage-point gap
across all portfolio position rows. It measures the squared-deviation objective
on a readable scale; it is separate from deviation outside tolerance ranges.
Without caps, for a fixed number of buys with a uniform minimum, buying the
largest target shortfalls is optimal. With caps, candidate choices also account
for the remaining capacity of each position. The calculator compares feasible
trade counts, including when all positions are in range.

Only **Spread by target weights** ignores existing holdings when splitting the
contribution. Minimum-purchase and selection-intent controls apply to
**Rebalance selected positions**. Both distributions allocate whole EUR cents
so buys plus any unallocated cash sum to the budget. Target-weight splitting rejects an amount too small
to fund every positive-weight recipient with at least one cent.
**No new positions** excludes selected rows with zero shares. If this conflicts
with **Buy every selected position**, deselect those rows, allow skipping, or
turn off No new positions; the calculator explains the conflict.
Unselected positions receive no trades. Final weights and deviation still use
whole-portfolio targets, so spreading can leave positions outside their ranges.
Changing the selection or distribution hides stale plans. Changing position
identities clears the selection for you to choose again. These controls apply
only to Allocate new money and never change saved holdings or targets.

Choose an absolute tolerance (default ±0.5 percentage points) or a percentage
relative to each target. For a 10% target, ±0.5 pp and ±5% relative both allow
9.5–10.5%. Relative tolerance leaves a zero target at zero; absolute tolerance
can allow a small holding. Bounds are clipped to 0–100%.

Rebalancing uses whole-portfolio position targets totaling 100%, complete EUR
valuations, and final weights including the contribution. Overview filters,
label selections and ETF display groups do not alter the tradable positions.
**No new positions** forbids buying zero-share account rows while retaining
their targets; **Ignore empty positions** instead removes and redistributes
them before calculation. Infeasible restrictions produce an explanation.

Amounts assume fractional shares and exclude fees, taxes, spreads and lot-size
constraints. New money is fully invested except when temporary allocation caps
and purchase restrictions leave an unallocated remainder. This is a calculation
result, not a saved cash account.
EUR amounts are rounded only for display. Plans never place orders or write
holdings. SciPy’s HiGHS linear/mixed-integer optimizer must report a proven
optimum; a solver limit or failed constraint check produces no trade plan.
Each solve has a ten-second limit; interactive calculations support up to 100
position rows. With zero invested capital, choose a budget in **Allocate new
money** instead of asking for a minimum contribution.

`classifications.yaml` maps asset IDs to any number of named taxonomies:

```yaml
nvda:
  classifications:
    ai:
      - [AI, Compute, GPUs]
    sector:
      - [Technology, Semiconductors]
    geography:
      - [North America, United States]
```

Taxonomy names and tree depths are unrestricted by application logic. Add asset
class, tags, sleeves, or other custom classifications using the same format.
Keep the file present; `{}` is valid when you have no classifications yet.
Missing membership in a selected taxonomy appears as `Unclassified`.

An asset with several distinct paths splits its value equally across them.
Identical repeated paths are deduplicated. Explicit path weights are deferred.
Full paths identify categories, so repeated labels under different parents
stay separate. When an internal category also has its own allocation, an
`Assigned here` leaf preserves that value in the visualization. At a truncated
depth, short paths remain visible instead of disappearing.

Use **Manage positions** in the app to create and edit positions, or edit the
CSV outside the app and reload the page. Classification trees remain editable
in YAML. File data is read on each Streamlit rerun.

## Create and maintain positions in the app

Open **Manage positions → Add position**, enter the instrument, total shares,
portfolio, and account, and click **Save position**. Buy-in and target allocation
are optional. New instrument IDs are generated automatically. For an instrument
already present, select it under **Existing instrument** to reuse its identity
and classifications in another account or sleeve. New instruments without
classification metadata appear as `Unclassified`.

The main tabs separate analysis, rebalancing and position maintenance.
**Exposure settings** contains position filters, ETF breakdown switches and snapshots;
**Chart settings** contains grouping and hierarchy controls. Before strategic
allocation is enabled, these analysis controls remain in **Overview**.
**Show price details** exposes quote timestamps, FX status and valuation notes.

For a new instrument, type a company name, ETF theme, ticker, or ISIN in **Find
an investment**. Suggestions update after a 400 ms typing pause; Enter searches
immediately. Use the All / Equities / ETFs filter to narrow the results. Matching
investments appear as cards, with listings grouped by known ISIN. Click the
exchange/ticker button directly to fill the form. Arrow Down from the input
focuses a listing; arrow keys navigate choices, Enter selects, and Escape clears
the input. Responses for older text are hidden while typing a new query.

Yahoo results are combined with a small public listing catalog for the supported
VanEck Semiconductor UCITS ETF and a few common equities. Terms such as
**VanEck**, **Van Eck**, **semiconductors**, **chips**, **VVSM**, and the fund's
ISIN find its UCITS listings even when Yahoo's keyword results omit them. The
catalog uses [VanEck's published trading information](https://www.vaneck.com/uk/en/library/fact-sheets/smh-fact-sheet.pdf).
The UCITS badge, ISIN, exchange, and trading currency distinguish those listings
from the separate US ETF with bare ticker `SMH`. Exact ticker matches appear first.

Selection fills the name and uppercase ticker, plus ISIN when the
provider can associate it with that listing. Optional ISIN lookup uses an exact
reverse ticker match to avoid accepting a loose company-name match. Unknown
identifiers remain blank and all new-instrument fields stay editable. Check
the selected exchange against the instrument you own. An exact ticker already
in your holdings reuses its existing identity; a different exchange listing
can have a separate identity even when the ISIN is the same.

Quote currency is shown as context and never replaces your buy-in currency.
Search sends only the query and selected instrument metadata to Yahoo Finance
and its ISIN lookup provider; it does not send your holdings or purchase data.
Results are cached only in the Streamlit session for five minutes (up to 40
queries); provider caches use the ignored data directory. Live search requires
network access. Offline demo mode offers the local catalog without network
requests. If the live provider fails, matching catalog listings remain available
with a status message and retry control.
Manual entry remains available when search or optional metadata lookup fails.

Saves go to `holdings.csv` in the active data directory and survive app restarts.
The form also works with an empty data folder: it creates the CSV on the first
save, and a missing classifications file means no classifications yet. A
successful save resets allocation filters so the new or updated position is
visible. It does not require a live price lookup.

Use **Edit position** to replace a position's total shares, portfolio/account,
buy-in, buy-in currency, or target. **Manage positions** opens with a searchable,
sortable position list: double-click a row, press Enter on a focused row, or use
its **Edit** button. **Back to positions** returns to the list.
The instrument name is editable and a rename applies across its account rows;
instrument IDs, tickers, and ISINs stay fixed. The app prevents
adding a second summary row for the same instrument, account, and portfolio.
For repeated purchases in that position, select it as the destination under
**Bulk add purchases**, or update its total manually.

Before replacing an existing CSV, the app validates the full candidate data
and saves the previous file in a dated `.backups/` CSV next to it. The final
write is atomic. Concurrent app writes are serialized, and a form based on an
older file is rejected with a reload prompt. Existing custom columns and
unchanged cell values are retained. Keep an independent backup of your data
directory if you need protection against loss of the drive itself.

### Bulk purchases

Open **Manage positions → Bulk add purchases**. Each batch belongs to one
instrument, account, and portfolio. Select an existing position or create a new
one, using instrument search where useful, then choose the purchase currency.

Enter multiple rows in the editable table, paste spreadsheet cells with headers,
or upload a UTF-8 CSV/TSV. The following is a deliberately invented example:

```csv
date,shares,price,fees
2026-01-01,2,100,1
2026-02-01,3,120,2
```

`shares` must be positive and may be fractional. `price` is the price per share;
blank prices remain unknown. `date` is optional and uses YYYY-MM-DD. Blank
`fees` means zero. Prices and fees must all use the selected currency. Comma,
semicolon, and tab delimiters are supported; decimal commas require tabs,
semicolons, or CSV quoting. Thousands separators are not supported. Batches
are limited to 1,000 purchases and uploaded/pasted text to 1 MB.

**Add new purchases** increases the position's share count. When all costs are
known, the resulting average includes the existing share-weighted buy-in plus
each purchase's cost and fees. The example creates five shares costing €563,
with a €112.60 average. Costs are calculated with decimal arithmetic; they are
not rounded to the UI's six displayed decimal places when saved.

**Calculate buy-in for shares already held** records historical purchase inputs
without increasing the current share count. The batch must account for all
shares in the position (within 0.000000001 share and 0.0000001% of the holding).
Use this for purchases with no
intervening sales or splits. If purchases no longer describe the remaining
position, reconcile the summary manually under **Edit position**.

The preview shows shares before and after, fees, batch cost, and resulting
average. Unknown opening costs or purchase prices keep the resulting average
unknown. A known opening buy-in requires an explicit matching currency; mixed
currencies are rejected instead of converting historical costs at today's FX.

**Save purchase batch** validates and saves the whole batch once, then clears
the form. A repeat of an earlier addition batch is flagged and requires an
explicit acknowledgment before adding it again. Identical rows within a batch
are all counted; review the preview before saving. These checks do not replace
reconciling an import against your statements.

Purchase inputs and their resulting summary are stored together in the private
`holdings.csv`, with the same atomic write, backup, and stale-edit safeguards as
manual position saves. **Saved purchase batches** under the position editor
shows the retained records. Historical recalculations can overlap earlier
batches, and manual summary edits remain authoritative: these records are not
a tax-lot ledger or an automatically reconciled transaction history.

### Buy-in prices and savings plans

For low-maintenance portfolio tracking, copy the broker's **current share
quantity and average buy-in / Einstandskurs** for each instrument/account. You
can update them monthly after a savings-plan execution, or record the purchases
with **Bulk add purchases**. Buy-in is unnecessary
for allocation analysis, so leave it blank when unknown.

In **Add position**, **Edit position**, or **Update balances**, select
**Buy-in entry → Total buy-in** to enter the total cost and its currency alongside
the current quantity. The app calculates the average per unit, including for
fractional crypto quantities. Use the cost of the quantity currently held,
including purchase fees, rather than lifetime deposits after sales or withdrawals.
Leave the amount blank when unknown. A positive total requires a positive quantity.

For exact purchase details, use the broker's purchase confirmations: these
record quantities, execution prices, fees, and settlement currency. Displayed
buy-in figures can use broker-specific conventions. For example,
[Trade Republic explains](https://support.traderepublic.com/de-de/1619) that its
buy-in is indicative, includes cost adjustments, and can update overnight;
purchase confirmations contain the execution details.

With purchases in the **same currency**, and no intervening sales or corporate
actions, maintain a share-weighted average:

```text
Average buy-in = total purchase cost, including buy fees / total shares

10 shares at €100 + 5 shares at €120 = €1,600 / 15 = €106.67 per share
```

Fractional shares from savings plans use the same calculation. If calculating
an update yourself, add the new purchase cost to the previous total cost, then
divide by the new total shares. Avoid repeatedly rounding intermediate costs;
the broker's summary is usually easier to maintain.

The form records the amount as `acquisition_price` and its currency separately
as `acquisition_currency`. A EUR broker buy-in can accompany a USD quote ticker.
Currency defaults visibly to EUR for a new position, and new or changed buy-in
amounts require a currency. Existing CSV buy-ins without a currency remain
unspecified until you fill it in. Historical costs are never translated with
today's FX rate. After partial sales, transfers, or splits, reconcile against
the broker's remaining-position data instead of dividing all historical
deposits by the shares left. This app stores current summaries and optional
purchase-input batches; it does not account for sales, tax lots, or realized gains.

## Current performance

The **Performance** panel in the analysis view shows unrealized gain/loss,
return on cost, and cost basis. The EUR summary uses only held positions with
explicit EUR buy-ins and available EUR valuations, and reports its coverage.
The return is total gain divided by total cost for those same positions, not
an average of position returns. It always covers the whole portfolio; filters
narrow only the holdings table.

**Exposure settings → Performance display** switches the shared performance column
between **%** and **Amount** in holdings, label comparisons, label drill-downs,
and hierarchy/allocation tables. Holdings use each position’s recorded buy-in
currency; grouped amounts are EUR. The holdings table also shows cost basis. Cost basis is shares times average buy-in; unrealized
gain is current value in that currency minus cost basis. Foreign quotes are
converted into the buy-in currency at current FX. Historical costs are never
converted using current FX, and foreign-currency costs are excluded from the
EUR summary because purchase-date FX is unknown. Missing costs, currencies,
prices or required FX leave performance unavailable with an explanation.
Zero-share positions have no performance; zero-cost positions can have a gain
but no percentage return.

Label performance sums matching current values and costs, then calculates
(total current value − total cost) / total cost. It follows the same overlap
and hierarchy-path splits as allocation; it never averages individual returns.
**Performance coverage** marks Complete, Partial, or Unavailable. Partial
results cover only the eligible EUR-cost positions and must not be read as the
return of every asset in the label. Missing prices/costs and foreign-currency
costs reduce coverage; zero-share planned positions do not.

In ETF look-through, current constituent weights cannot establish historical
constituent costs. Expanded fund contributions therefore have unavailable
performance. In Instruments mode the fund’s own return contributes under its
labels. A display group that contains the whole fund also retains its return,
including when other instruments are shown in look-through mode.

These figures cover shares currently held and use the latest available prices.
They exclude dividends and realized gains. Fees count only if already included
in the recorded buy-in. After sales or corporate actions, update the shares
and average cost of the remaining position in Manage positions.

## Valuation and price status

Live prices come from yfinance's latest available **unadjusted daily close**;
the current daily bar may still be updating. They are not guaranteed real-time
quotes. Timestamps identify the provider's daily bars rather than exact trade
execution times.

```text
EUR value = shares × price in quote currency × EUR per unit of quote currency
```

EUR quotes use an exchange rate of 1. Other currencies use the corresponding
Yahoo Finance currency-to-EUR pair. Currency comes from provider metadata,
never a ticker-suffix guess. Recognized pence/cents quote units are converted
to their major currency before applying FX. Unknown currencies, missing FX,
missing tickers, and failed price requests leave positions visible as unvalued.
Acquisition price is never substituted for market price.

Successful quotes and FX are cached in `data/portfolio/.cache/prices.json` (or the chosen
data directory). Refresh attempts are limited to once per 15 minutes per key,
including failed attempts. **Refresh prices** bypasses that interval. Failed
refreshes retain last successful data as `cached fallback`, with an explanation;
old quotes are not silently presented as freshly downloaded data. Price and FX
timestamps and ages are shown separately. Cached data may be old, including
over market closures; consult its age when interpreting values. Without a
usable cached quote, the affected position remains unvalued.

The cache survives restarts; deleting `.cache` forces retrieval again. Provider
cookie/timezone cache files also live there. These runtime files are ignored
by Git. Cache read/write problems are reported without stopping valuation.

## Explore allocations

- **Group by → Selected labels** compares any labels you choose. Assets assigned
  beneath a selected label are included automatically. Each row shows the
  combined EUR value, its share within the selected labels, and its share of the
  filtered portfolio. The coverage caption shows value outside those labels.
  A local taxonomy named `labels` supplies the primary comparison choices; this
  view opens by default when that taxonomy is present. Other hierarchy views
  remain available through Group by.
- Selecting a label includes the **full value** of matching assets, independently
  of their memberships in unselected labels. If an asset matches multiple
  selected labels, explicitly choose equal splitting or full overlapping counts.
  Splitting sums to 100%; overlapping counts use the unique matched value as the
  denominator and can exceed 100%. Overlapping comparisons use bars and tables,
  never a pie or a value-conserving tree chart. Multiple paths beneath the same
  selected label count only once. Empty selections do not select everything.
- Label comparison runs after optional ETF expansion, so direct and indirect
  exposures aggregate under the same labels. Unknown constituents and residual
  Other buckets stay unclassified unless explicitly labeled; they are included
  in the coverage calculation and can be selected via Unclassified.
- Sunburst is the default allocation chart (overlapping comparisons use bars).
  Colored label badges in allocation tables match the comparison chart;
  category colors stay consistent when sorting or filtering the portfolio.
  In a label comparison, click a sunburst or treemap category to open its
  subcategories and individual assets. Click the sunburst centre or treemap
  breadcrumb to go back. **Detail view** updates both the chart and the asset
  table for any branch; **Back to overview** restores the comparison. Chart-only
  zoom leaves the table unchanged. **Show individual assets directly** skips
  intermediate subcategories. Drilling preserves the label's assigned value;
  multiple matching paths within that label share it equally. The detail table
  combines the same asset across paths and direct/ETF sources, and percentages
  use the detail's total assigned value.
- Select portfolios, accounts, other metadata, and individual holdings. Clear a
  metadata or holdings selector to select no positions in that dimension.
- Branch filters include whole positions with membership beneath any selected
  branch. An empty branch filter imposes no restriction. Selections within one
  filter use OR; different filters use AND.
- Holdings weights use the selected subset's total **valued** amount. Unvalued
  positions have blank amounts and weights and are counted separately. Zero
  totals have undefined weights and do not produce a chart.
- Group by holding, metadata, or any taxonomy. Choose a hierarchy root, depth,
  and treemap, sunburst, bar, or pie chart. Treemap and sunburst optionally show
  holdings under categories. Pies use disjoint terminal buckets, so parent
  categories and their children are never counted together.
- Hierarchy drill-down restricts *allocated value* to the displayed root and
  recalculates percentages against that root. For example, a €100 holding split
  across two paths contributes €50 to each; filtering by either branch selects
  the whole €100 holding, while drilling into one displays its €50 allocation.
- Depth is measured below the selected root. Select root `AI` and one level to
  compare `Compute` with `AI Infrastructure`. Flat bars show the terminal
  buckets at the chosen depth, including any shorter paths. Pies use the same
  depth and root selection.
- Exposure chart clicks navigate within the chart. The root control updates
  both chart and table. Parent rows in the hierarchical table already include
  their children; do not sum every table row as though they were disjoint.
- The displayed root total stays above the sortable allocation rows. Enable
  **Show classification paths** for taxonomy breadcrumbs; flat views omit this
  redundant column. Allocation, holdings and ETF tables expand with their rows.
- Allocation and holdings tables default to largest percentage first. Hierarchy
  siblings are ordered by allocation while descendants remain under their
  parents. **Show tickers** restores symbols in overview charts and tables;
  symbols remain visible when choosing or editing an exchange listing.
- Display names tidy capitalization, whitespace and trailing legal suffixes.
  Share classes, ADR/UCITS labels and fund currency qualifiers are retained.
  Stored names, tickers, ISINs and the identities used for calculations are
  unchanged; the position editor continues to show the original name.
- Common company aliases shorten long provider labels, for example TSMC and
  Amazon. Aliases change presentation only, never instrument matching.
- The ETF breakdown table shows locally configured classifications, using the
  same constituent-to-instrument matching as look-through calculations. Local
  labels remain separate from downloaded snapshots; new constituents without
  labels appear as Unclassified.

## ETF breakdowns

Supported provider integrations include VanEck Semiconductor UCITS, Xtrackers
MSCI World 1C, and iShares Core MSCI EM IMI. Amundi MSCI Europe Momentum uses an
explicitly labeled **same-index iShares proxy**, not its synthetic substitute
basket. Proxy data approximates company allocation; it is not the actual
Amundi portfolio or an exact index constituent file.

In **Exposure settings**, turn on **Break down ETFs**. Every supported fund in
the portfolio starts enabled. Under **Individual ETFs**, turn off any fund to
keep it as a single instrument. These session choices survive switching the
master control off and back on. Newly supported funds start enabled. Unsupported
funds remain visible with a missing-breakdown notice. The same selections apply
to allocation, targets, performance coverage, and the stock-only exposure view;
strategic ownership and tradable source positions stay intact.

The expandable **ETF breakdown** tables show dates, source links, coverage,
proxy status, and import notes. They are available even without a held position.
Large snapshots use a scrollable table. No position or quantity is created.

**Bare `SMH` is the US-listed ETF**, so it is never used as a UCITS ticker
fallback. A UCITS ISIN paired with bare `SMH` produces a correction message
before valuation, preventing use of the wrong fund's price.

Each indirect value equals the selected ETF position's EUR value multiplied
by its stored constituent weight. Direct and indirect company exposures merge
by matching the constituent ISIN, then its ticker, to the stable asset ID in
your holdings. The effective-exposure table shows direct, ETF-derived, total,
and selected-portfolio percentage amounts. Multiple ETF positions retain their
source account and portfolio metadata. If several distinct asset IDs match one
constituent, resolve them to one stable ID across positions before look-through.

Constituents use the same `classifications.yaml` as direct holdings. Without
metadata they remain `Unclassified`, including the residual `Other` bucket.
Matching asset classifications therefore work for both direct
and indirect exposure. Add constituent IDs from the snapshot CSV
to classify them. Instrument filters apply **before expansion**; hierarchy-root
selection drills into the resulting constituent allocations. The holdings
table continues to show original instrument positions. Unsupported ETFs remain
unexpanded instruments.

### Combine the ETF with direct stocks

Enable **Group SMH with related stocks** in Exposure settings to show the held UCITS
ETF and selected direct stocks as one **SMH + related stocks** allocation.
**Stocks in the SMH group** starts with Nvidia and TSMC when held and present
in the local snapshot; you can select other matching constituents or remove
stocks. ISIN identifies exchange listings; a missing ISIN permits exact ticker
matching. The separate US-listed SMH fund is excluded.

The group includes the full selected ETF value and each chosen stock's full
EUR value, counted once. Position/account filters still apply. In look-through
mode the allocation group includes all of SMH, including any residual Other,
while other ETFs keep their usual expansion. Exposure to the same company from
another ETF remains outside the group. The **Effective exposure** table continues
to show the original direct and ETF-derived company exposures.

The display group follows the ETF's classifications and counts as one displayed
asset. Open **SMH group members** to inspect its original positions and their
shares of the group value. Saved holdings, purchase costs, targets and
classifications remain unchanged. Turn the option off to restore the usual view.

Fund snapshots live in the active portfolio's `etfs/` directory: a YAML manifest identifies the fund, date,
source, listing aliases, and constituent CSV. Weights are fractions of the
entire ETF. For partial snapshots, the remainder is explicitly retained as
`Other`; named holdings are never scaled to 100%.

### World-fund imports

To install or refresh one supported snapshot in a private workspace:

```bash
uv run python -m portfolio_app.etf_sources <ISIN> --data-dir /path/to/private/workspace
```

The command accepts IE00BJ0KDQ92, IE00BKM4GZ66, and LU1681041460. It only writes
snapshot files under `etfs/`; it never changes holdings, targets, or local labels.
Xtrackers uses full-precision JSON percentage weights and ISINs. iShares uses
the complete XML Spreadsheet **Holdings / All** export and derives weights from
all market values, checking against published percentages. This preserves small
holdings lost to two-decimal percentage rounding. Net cash includes cash
liabilities, money-market holdings, collateral, and FX. Futures notional
exposure is not assigned to companies. Unsupported signed equity or net
borrowing fails validation instead of silently dropping liabilities.

Provider sectors and countries supply fallback taxonomy paths, leaving local
classifications in charge. Where iShares supplies only local tickers, they are
retained as source metadata rather than treated as exchange-qualified symbols.
Provider parsers do not infer missing ISINs. Company-level overlap is handled
separately by the reviewable matching described below.

**Exposure settings → Company merges** groups the same company across ETFs,
including companies without a direct position. Matching uses shared security
identities and reviewed `company-identities.yaml` equivalences first. Missing
identities can use an **estimated full-name match**, marked **`*`** in charts
and exposure tables. Case, punctuation and trailing legal suffixes are normalized;
share classes, country qualifiers and subsidiary names are retained. There is
no substring or fuzzy matching. Conflicting reviewed identities or ambiguous
multiple entries within one source block a name estimate.

Select a company in the review menu to see every original asset name, source
fund (including proxy status), identifier, ticker and snapshot date, plus the
match method. **Undo merge** keeps its source holdings separate; **Restore
merge** combines them again. Choices are saved privately in `company-merges.yaml`,
with revision checks and backups. Undo survives app restarts and refreshed
snapshots with the same source identities; adding a new group member does not
silently restore a split.

The same grouping reaches allocation, ETF targets, performance coverage, local
classifications, and stock-only exposure. Existing direct/local classifications
are preferred when choosing a shared display identity. ADRs and ordinary shares
retain their original security identifiers and source positions. Only EUR values
are combined; receipt ratios do not multiply exposure. Saved positions, strategic
bucket ownership, costs, and targets remain unchanged. An estimated grouping is
an analytical convenience, not a verified security equivalence.

Optional private `company-names.yaml` labels reviewed company IDs independently
of their source securities. This keeps a company combining ordinary and preferred
shares from being labelled as only one share class. Undo restores each original
security's display name. Reviewed nested equity funds in provider exports remain
unresolved fund exposure in the stock-only view, rather than being counted as
individual companies; their value remains in the equity denominator.

The momentum proxy uses the iShares Europe Momentum fund's official holdings.
MSCI's [constituent terms](https://www.msci.com/legal/index-constituents-disclaimer)
prohibit software extraction of the index lookup; this integration does not
retrieve that list. Proxy status is saved in the manifest and shown in the UI.

### Recency and updates

**Exposure settings → ETF snapshots** displays the snapshot date and age.
Snapshots **older than seven days** are flagged. Use the provider update button
to download and validate the latest holdings. This control is
separate from price refresh and is disabled in offline demo mode. There is no
background download schedule.

Updates validate the file structure, weights, and date before saving. Older or
future-dated downloads are rejected. Existing constituent IDs are preserved by
ISIN so custom classifications keep working. A new CSV is written first, then
the manifest switches to it atomically. Earlier CSVs remain available; failed
downloads or writes keep the previous working snapshot. A successful check can
still return the same holdings date over market closures; the age warning uses
the provider's date, not the download time.

You can also download the XLSX yourself and convert it offline:

```bash
uv run python -m portfolio_app.vaneck holdings.xlsx data/portfolio/etfs/smh_ucits.csv
```

For this manual route, update `holdings_file` and `as_of` in the manifest to
match the output file and date printed by the importer. It is intentionally
specific to VanEck's current UCITS export and uses no extra Excel dependency.

## Architecture and verification

Business logic is independent of Streamlit:

```text
holdings → EUR valuation → normalized exposures → optional ETF expansion → classifications
         → hierarchical aggregation → charts and tables
```

Position filters run after valuation and before exposure normalization. The
exposure table retains asset IDs and source-position metadata through ETF
expansion. Provider parsing and refresh code are separate from the generic ETF
transformation and aggregation engine. All chart types share the same aggregation engine.

```bash
uv run pytest
uv run portfolio-app --demo --server.headless=true
```

Table editors stay attached to their cells while scrolling. While editing,
Tab saves and opens the next editable field; Shift+Tab moves back. Escape cancels
the current edit. Enter edits a selected cell or toggles a selected checkbox.

Optional browser regressions exercise these interactions in an isolated,
synthetic Streamlit app. Install Chromium with
`uv run --with playwright playwright install chromium`, then run
`uv run --with playwright pytest tests/test_grid_browser.py`.

Tests use generated synthetic fixtures, injected prices, FX, search responses,
and clocks, with no live network required.
They cover valuation, persistent cache fallback, parent/child conservation,
multi-path classifications, filtering, denominators, targets, ETF residuals,
company exposure merging, snapshot refresh failure recovery, position saves,
backups, stale-edit rejection, bulk purchase averaging and historical cost entry,
listing search, minimum-trade and minimum-cash rebalancing, trade-count tradeoffs,
empty-position redistribution, Git privacy guards, and Streamlit controls. The server health
endpoint is `/_stcore/health`.

Manually overridden hierarchy-node targets, realized P&L, and historical analysis
remain deferred.
