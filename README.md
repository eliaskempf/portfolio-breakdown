# Portfolio breakdown

A local CSV/YAML-backed portfolio explorer built with Python, Streamlit, and
Plotly. It supports current EUR valuation, hierarchical classifications,
filtering, allocation charts, optional position targets, and ETF look-through
for configured fund snapshots.

## Installation and release preparation

See [installation, desktop launch, migration and recovery](docs/install.md),
[release milestones](docs/release-plan.md), and the
[candidate acceptance checklist](docs/release-checklist.md).
The application is licensed under [GPL-3.0-only](LICENSE).


## Position metrics and portfolio analytics

For future development, see [analytics state and open design choices](#analytics-development-notes).

Under **Positions**, switch between **Holdings**, **Valuation**, **Income & fees**,
and **Risk** using the compact view selector. **Options** contains additional
columns, risk settings, and refresh. Metric views keep the instrument and
current value beside the selected metrics; numbers sort numerically and
unavailable values stay last. Selecting a row opens the existing position dialog:
choose **Key metrics** for summary cards, definitions, sources, and fund-fee
maintenance. Search, sort, scroll, and the chosen view survive opening and closing
a position. The default Holdings view does not fetch fundamentals or history.

Stock fundamentals include trailing/forward P/E, price/book, price/sales, market
capitalization, trailing cash dividend yield, payout ratio, growth, profit margin,
and return on equity. ETF metrics include annual fees, cash distribution yield,
assets, and separately labeled fund valuation ratios when the provider supplies
them. Unavailable values are blank; nonpositive P/E is not meaningful. Monetary
fundamentals retain their reported currency rather than being silently added or
converted across listings.

In **Overview**, choose **Analytics** alongside **Allocation** and **Performance**.
Analytics follows the existing Overview category selector; **Options** provides
an optional account filter. This scope is independent of Exposure and position-list
search. It shows instrument
concentration (including repeated instruments across accounts), effective number
of holdings, underlying company concentration using existing ETF snapshots and
company mappings, estimated annual fund costs, and trailing cash yield.
Recorded-cost performance remains in the adjacent **Performance** view.
Fundamentals load when Analytics is selected. Sources, coverage tables, and
advanced settings stay behind secondary controls; the summary uses the same
metric cards and restrained styling as the rest of the app.

Aggregate P/E uses `sum(value) / sum(value / PE)` for covered profitable direct
equities. Fund ratios are not blended into this statistic. Trailing distribution
estimates use current value times reported cash yield; these are neither a forecast
nor dividends actually received. Known accumulating share classes have zero cash
distributions. Fund costs use current value times the annual fee rate; these costs
are already reflected in fund prices and are not deducted again from gains.
Every aggregate reports coverage; missing values are never assumed to be zero.

Analytics leads with **Historical risk**, followed by valuation, income and
concentration. **Calculate risk** adds beta, annualized volatility, benchmark correlation,
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
data directory in the platform user-data location (see the installation guide).
After a short intro, a fresh portfolio opens a welcome dialog with **Explore demo**
and **Start my portfolio**. A skippable two-step guide explains categories and
optional target allocations, then opens the first position with category assignment.
Add categories one at a time with a name and optional portfolio target; examples
are in the field help. Enter adds the row and focuses the next empty name. When
complete targets reach 100%, choose to continue or keep editing. Blank or partial
targets can be saved for later; nothing is automatically redistributed.
Saving categories writes only the allocation; saving a position is a separate action.
**Skip setup** opens empty Positions, where manual entry and experimental import
remain available. **Finish later** leaves saved categories intact without adding a
position. Existing portfolios open normally.

The header's **Portfolio workspace** dropdown opens **My portfolio** or the
editable **Demo portfolio**. Switching clears position forms and filters so an
unfinished edit cannot be applied to the other portfolio. The **?** button at the
upper right opens help and the demo guide; **Settings → App & workspace** contains
folder, version and stop controls.

For startup/animation development without rebuilding the executable, see
[Startup development](docs/startup-development.md).

The demo uses public quotes, genuine instrument price history and issuer ETF
holdings downloads by default:

```bash
uv run portfolio-app --demo
```

Quantities, buy-ins and allocation targets are invented. At first use, the demo
waits for all six quotes (and any required FX), then sizes the positions once to
roughly €93,184 with deliberately uneven allocations. Buy-ins are invented
relative to those prices to show both gains and losses; they are not historical
transactions. Later price refreshes never resize quantities or reset your edits.
Quotes are the latest available daily closes, not real-time ticks. History charts
show the actual instrument, not reconstructed portfolio returns.

| Category | Target | Approximate initial allocation |
| --- | ---: | ---: |
| Equities | 60% | 62% |
| Money market | 25% | 24% |
| Gold | 10% | 10% |
| Crypto | 5% | 4% |

Within Equities, the targets are 70% Xtrackers MSCI World 1C (IE00BJ0KDQ92)
and 30% iShares Core MSCI EM IMI (IE00BKM4GZ66). Money market uses Xtrackers
II EUR Overnight Rate Swap 1C (LU0290358497), gold uses EUWAX Gold II
(DE000EWG2LD7), and Crypto targets 60% Bitcoin / 40% Ethereum.

**Exposure → Break down ETFs** uses the supported issuer download paths for
World and EM IMI. Downloaded weights and dates appear in the normal snapshot
controls. Missing downloads leave the fund whole; there is no invented
three-stock fallback. Small genuine residuals remain Other. Overnight exposure
uses the existing provider support when available; its substitute basket never
becomes equity exposure.

All demo edits and downloaded data stay in a fresh temporary demo directory,
separate from **My portfolio**. Edits survive refreshes and workspace switches,
and reset when the app restarts. The temporary directory is removed on normal
shutdown; a new launch always creates fresh demo data even after a crash.
`--demo` selects this workspace; `--data-dir` still names the persistent portfolio.
No personal portfolio is read to construct the example.

If quotes are unavailable on first use, the app offers a retry and keeps
**My portfolio** accessible. Later failures retain dated cached observations
with their normal status. Synthetic prices and sine-wave history are never
substituted for failed live requests. For a deterministic, explicitly labelled
offline example (also used by packaging smoke tests):

```bash
uv run portfolio-app --demo --offline-demo
```

Offline mode has fixed €100,000 values, invented prices/history and partial
three-constituent equity snapshots. Its large Other remainder is intentional
and is not representative of actual issuer coverage. Both modes retain the
same category and within-category targets and reset on restart.

The persistent data directory defaults to the platform user-data location, independent
of your launch directory. Existing `data/portfolio` folders are preserved; open them
with `--data-dir data/portfolio` or follow the explicit migration guide. You can
use an independent directory with your own files:

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

## Experimental holdings import

In an empty portfolio, choose **Positions → Import portfolio — experimental**.
Upload one or more CSV, delimited TXT/TSV, or Excel XLS/XLSX files, review the
suggested column mappings, and correct cells or exclude non-position rows before
choosing **Import reviewed positions**. Encoding, delimiter, worksheet, header row
and German/English number formatting can be adjusted per file. Quantity columns
containing nominal amounts or prices quoted as percentages need conversion to
ordinary units before import; the importer does not calculate bond valuations.

FinanzManager support is provisional: its
[manual documents report export](https://www.lexware.de/fileadmin/support/handbuecher/2023/handbuch_finanzmanager_2023.pdf),
but no tester export/version has been verified. Export a current holdings report
using **Bericht exportieren**, then use column mapping if suggestions do not
match. If the report contains both Bank and FinanzManager quantities, choose the
intended source explicitly. QIF, PDF and transaction histories are not supported.
The importer does not reconstruct balances from purchases or synchronize existing
portfolios. Multiple depot files can be reviewed together in the initial import.

Only instrument name and quantity are required, together with confirmation of
the quantity convention. ISIN/WKN, account, costs, prices, targets and
classifications can be omitted. Internal position identities are generated;
names alone never merge instruments. Duplicate positions must be resolved in
the preview. No target allocation or strategic bucket is inferred from the export.
After import, **Set up allocation** opens the existing bucket/assignment tools.

Exported unit prices can provide an immediate allocation view when their date,
currency and unit convention are known. They remain dated manual prices and do
not refresh automatically. Foreign prices use the app's available FX conversion,
not historical export-date FX; missing FX leaves valuation unknown. Acquisition
cost remains optional and has its own currency. Select either average cost per
unit or total cost of the remaining position, not both.

Use **Positions → Connect live prices** later to select a listing. The explicit
switch to live pricing clears manual prices for that instrument across accounts,
preserving quantities, costs, identities and allocation assignments. Otherwise,
linking a listing retains snapshot pricing. Existing ISINs must match the listing.

Uploaded files and review drafts stay in session memory and survive navigation
between tabs. Canceling, completing the import, or switching workspaces clears
them. Accepted position data is saved in the selected private portfolio directory
through a revision-checked atomic write; prior files receive private backups. Canceling writes no holdings,
workspace changes clear import drafts, and repeated submissions cannot append
the same holdings again. Keep real exports out of Git. The format-specific
column suggestions are separate from readers and normalized position handling
so another source can reuse the same review and save flow.

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

## Physical holdings, including gold

Choose **Add position → Physical asset** to record gold or another physical holding.
Choose **troy oz**, **grams**, or **units**, enter the quantity and assign a category
if categories are enabled. For gold, use the amount of fine gold in that unit.
Supply a current price **per the same unit**, its currency and date; without a
price the quantity is retained and valuation is unknown. This is a manual valuation,
not an automatic gold spot feed or a proxy using an ETF/futures price.

Buy-in is optional and can be entered per unit or as a total. Changing units in a
new form clears amounts for re-entry; it does not convert them. The unit is fixed
when editing or adding another position of an existing physical instrument. Use a
separate instrument to track a different unit. Categories and targets work as for
other holdings. Position details show the unit with the quantity; manual valuations
have no instrument price-history chart.

## Strategic allocation and balance maintenance

Open **Rebalance → Targets** to preview enabling the
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
**Rebalance → Targets → Assign positions** offers
**Select all**, a destination bucket, and an assignment button. Assignments keep
quantities, buy-ins, classification labels, and within-bucket target percentages;
review the destination's target total after moving positions.

Use the top-right menu's **Light**, **Dark**, or **System** option for appearance.
The browser remembers the choice. Charts, tables, editors and search use the same
theme.

The Rebalance option **Exclude empty positions and redistribute targets** redistributes
position targets within each bucket only. An empty bucket retains its strategic
target and appears as planned capacity. Saved targets do not change.

**Rebalance → Plan → Planning scope** offers portfolio contributions and within-bucket
planning. Portfolio contributions first minimize deviations from bucket ranges,
then bucket target gaps, before allocating each budget internally. Position
constraints and the shared trade limit can leave some reserved money unallocated;
that cash stays visible in the final portfolio denominator. Within-bucket plans
use that bucket's post-contribution value and display the macro impact. They do
not require unrelated buckets' position targets. Protecting a bucket from selling
also protects its descendants while allowing contributions. Temporary caps state
whether their denominator is the whole portfolio or the selected bucket.

**Plan** keeps scope, mode, contribution, and **Calculate plan** together. **Options**
contains position eligibility, empty-position target redistribution, purchase rules,
tolerances, and temporary caps. Scope uses categories; source instruments remain the
trade universe independently of Overview and Exposure filters.

Results lead with **Suggested trades**, showing only buys and sells with readable
investment/category names and account distinctions. **Portfolio impact** compares
current, planned, and target allocations among sibling categories. **Plan details**
contains the full allocation (including Hold rows), optional trade-count comparisons,
and reserved versus invested category budgets. Unallocated cash remains in the
planning denominator; the preview never creates a cash holding or executes orders.
Unassigned appears only when source positions actually lack a category. Empty
configured categories retain their targets and planned capacity.

**Targets** has fixed-row category and position editors. **Add category** and
**Delete categories** modify a draft; **Save categories** persists it. Positions and
child categories must be moved before deleting their category. Position target
edits survive category filters and tab changes and are saved together with
**Save position targets**. Each editor has **Discard changes**. Blank targets remain
unspecified, while zero is explicit; totals are reported without automatic
normalization. Save a newly added category before choosing it as a parent.

**Positions → Update balances** replaces several quantities and optional
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

The header’s **Settings → Hide empty positions** changes only visibility in Positions and Exposure.
The separate Rebalance option **Exclude empty positions and redistribute targets**
removes zero-share rows from the planning calculation. Their combined target is divided equally among
remaining unique asset IDs, then equally among each asset’s held account rows.
This is an equal percentage-point increment, not a proportional scaling. For
example, removing an invented 20% planned target adds 10 pp to each of two
remaining assets. Saved positions and targets remain unchanged and editable in
**Positions**. Visibility never changes targets. Redistribution
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
**Only buy existing positions** excludes selected rows with zero shares. If this conflicts
with **Buy every selected position**, deselect those rows, allow skipping, or
turn off Only buy existing positions; the calculator explains the conflict.
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
**Only buy existing positions** forbids buying zero-share account rows while retaining
their targets; **Exclude empty positions and redistribute targets** instead removes and redistributes
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

Use **Positions** in the app to create and edit positions, or edit the
CSV outside the app and reload the page. Classification trees remain editable
in YAML. File data is read on each Streamlit rerun.

## Create and maintain positions in the app

Open **Positions → Add position**, enter the instrument, total shares,
portfolio, and account, and click **Save position**. Buy-in and target allocation
are optional. New instrument IDs are generated automatically. For an instrument
already present, select it under **Existing instrument** to reuse its identity
and classifications in another account or sleeve. New instruments without
classification metadata appear as `Unclassified`.

The main tabs separate analysis, rebalancing and position maintenance.
**Exposure** has source filters in **Filters**, with ETF controls and snapshots in **Data & settings**;
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

**Positions** opens a searchable, sortable list of quantities, values, allocations and gains.
Click a row (or press Enter) for position details and market-price history. Use its
pencil button to edit directly. **Add position** opens a dialog above the list.
Save and Cancel return to the same filter and sort order. Closing an unfinished
edit with Escape or the close button retains a resumable draft in that workspace;
Cancel, Save and switching portfolio workspaces clear it. Bulk purchases and
balance updates remain full-width workflows under **Position tools**.
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

Open **Positions → Bulk add purchases**. Each batch belongs to one
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

**Overview** shows value and unrealized performance for the selected category.
Its **Allocation / Performance** switch changes between the allocation sunburst
and a signed bar chart with a category comparison table. Category clicks,
breadcrumbs and Back update the chart, percentages and positions together.
Clicking the sunburst center returns to its parent category. Labels below 1% of
the selected view are hidden; their proportional slices and hover details remain.
Unrealized performance appears beside the value inside its card, green for gains
and red for losses. Position details retain separate value, gain and return cards.
The sunburst and allocation table sit side by side on wide screens and stack on
narrow screens.
Performance is aggregated from owned source positions, independent of ETF
look-through or overlapping exposure labels. Empty categories remain selectable.

The header’s **Settings → Performance display** switches **€ / %** across Overview,
Positions and Exposure, defaulting to EUR gains. The choice is remembered per
workspace. EUR totals include only held positions with recorded EUR costs and
available valuations. Category return is total gain divided by those same
positions’ total cost, not an average of percentages. Coverage is explicit.
Missing costs, currencies or prices remain unknown; zero-cost positions can
have absolute gains but no percentage return. Historical foreign-currency costs
are never converted using today’s FX. Native-currency returns remain available
in position details when they can be calculated.

Position details show full instrument identity, quantity, average and total buy-in,
value and gains, plus a separate **Market-price history** chart. Its 1M, 6M, 1Y,
5Y and Max ranges show closing prices in the listing’s quote currency without
dividend reinvestment. This is instrument history, not personal return history.
History loads automatically in the background when a position is opened, uses a
private one-hour cache, and labels saved results with their retrieval time.
Details and both close controls remain usable during loading. Failed attempts
wait 60 seconds before an automatic retry; **Retry history** bypasses that wait.
Manual or unsupported instruments show an unavailable state. Demo
history is explicitly synthetic and never requests live prices.

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
and average cost of the remaining position in Positions.

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

Quote and FX retrieval runs in the background. Navigation and saves use available
cached values immediately while an update is pending; new results appear
automatically. Only the selected main tab and Rebalance subtab are computed.
Filters, planning controls, and category/target editor drafts survive tab changes.
Parsed files are reused until their file revisions change, including externally
edited inputs and refreshed ETF holdings. Calculation results still use current
holdings, targets, and available prices.

The cache survives restarts; deleting `.cache` forces retrieval again. Provider
cookie/timezone cache files also live there. These runtime files are ignored
by Git. Cache read/write problems are reported without stopping valuation.

## Explore allocations

**Exposure** opens on **Assets**, with ETF breakdown enabled and all source
positions included. The main table combines direct and ETF-derived exposure,
includes unlabeled assets and ETF residuals, and sorts by total EUR exposure.
Select a row for contributing positions, accounts, and ETF snapshot details.
Unpriced positions remain visible; full selection percentages stay blank when
some source values are unknown. Search narrows visible rows without changing
the percentage denominator.

**Asset classifications** switches the table badges between **Themes**, **Sector**,
and **Geography**. Asset details show all available classifications and the source
of geographical assignments. These choices do not change curated AI labels.

**Exposure → Geography** shows company-country exposure as **Regions** or
**Countries**, with a bar chart and value table. Use **Geography detail** to drill
from a region into its countries and then individual assets. Search retains the
selected portfolio denominator. Regions separate the United States from Other
North America, alongside Latin America & Caribbean, Europe (including the UK),
Asia, Africa, and Oceania. The bundled public reference mapping follows
[UN M49](https://unstats.un.org/unsd/methodology/m49/), with Taiwan retained as a
separate country/area entry under Asia.

Geography uses explicit local `geography` paths first, then country metadata in
local ETF snapshots, including linked direct holdings. Country-only paths such as
`[Germany]` and hierarchical paths such as `[Europe, Germany]` are supported;
region-only paths retain a **Country unspecified** bucket. Equivalent paths are
deduplicated, while distinct manual memberships split value equally. Conflicting
provider countries remain **Unknown geography** until a local override resolves
them. Country means provider-assigned company location, not revenue exposure.
Trading venue, currency, ISIN prefix, and fund domicile are not country proxies.

**Gold**, **Crypto**, and **Cash** remain separate at both granularities. Crypto
and cash use instrument types; gold requires an explicit `geography: [[Gold]]`
or `asset_class: [[Commodities, Gold]]` classification, or a verified gold
instrument identifier. Physical assets, ETCs, and mining companies are not
automatically gold. Unexpanded funds and ETF residuals stay unknown unless
explicitly classified. Coverage reports country-assigned, region-only,
non-geographic, and unknown value; missing valuations suppress full-portfolio
percentages. No new country lookups run online, and saved classifications are
not rewritten. Coverage depends on saved metadata: Xtrackers and newly discovered
iShares snapshots retain provider country information where available; older
snapshots and VanEck exports may lack it.

The toolbar contains **Source scope**, search, **Break down ETFs**, **Filters**,
and **Data & settings**. Scope and source filters apply before ETF expansion.
**Data & settings** contains individual ETF choices, refresh preferences,
company-match review, optional charts, display groups, and source-price details.
**Themes & sectors** is an explicit alternative view, with its chart beside its
table. All Exposure views share the selected source scope.

- **Group by → Selected labels** compares any labels you choose. Assets assigned
  beneath a selected label are included automatically. Each row shows the
  combined EUR value, its share within the selected labels, and its share of the
  filtered portfolio. The coverage caption shows value outside those labels.
  A local taxonomy named `labels` supplies the default comparison choices;
  **Choose labels → Label set** switches between available taxonomies and
  remembers selected labels separately for each set. Unclassified is included by
  default when the selection contains unlabeled assets. Other hierarchy views
  remain available through Group by in that view.
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
  table for any branch; **Back to overview** restores the comparison. Chart
  clicks synchronize the selected branch and table. **Show individual assets directly** skips
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
- Exposure chart clicks and the root control update both chart and table.
  Parent rows in the hierarchical table already include
  their children; do not sum every table row as though they were disjoint.
- The displayed root total stays above the sortable allocation rows. Enable
  **Show classification paths** for taxonomy breadcrumbs; flat views omit this
  redundant column. Allocation, holdings and ETF tables expand with their rows.
- Allocation and holdings tables default to largest percentage first. Hierarchy
  siblings are ordered by allocation while descendants remain under their
  parents. **Show tickers** restores symbols in overview charts and tables;
  symbols remain visible when choosing or editing an exchange listing.
- Display names tidy capitalization, whitespace and trailing legal suffixes.
  Compact fund labels omit regulatory “UCITS ETF” text, trailing share-class codes,
  listing currencies and Acc/Dist suffixes, and repeated issuer wrappers. Hedging
  and leverage qualifiers remain visible; full names and share-class information
  remain in position details. An optional `short_name` overrides the
  display label across every account for that instrument. Full names remain in details.
  `short_name` is not an allocation dimension.
  Stored names, tickers, ISINs and the identities used for calculations are
  unchanged; the position editor continues to show the original name.
- Common company aliases shorten long provider labels, for example TSMC and
  Amazon. Aliases change presentation only, never instrument matching.
- The ETF breakdown table shows locally configured classifications, using the
  same constituent-to-instrument matching as look-through calculations. Local
  labels remain separate from downloaded snapshots; new constituents without
  labels appear as Unclassified.

## ETF breakdowns

Automatic discovery supports physical equity and bond ETFs in the official
iShares and Xtrackers product listings, plus the verified economic interpretation
of Xtrackers EUR Overnight Rate Swap 1C (XEON). Existing VanEck Semiconductor,
Xtrackers MSCI World 1C and iShares EM IMI integrations remain available. Amundi MSCI Europe Momentum uses an
explicitly labeled **same-index iShares proxy**, not its synthetic substitute
basket. Proxy data approximates company allocation; it is not the actual
Amundi portfolio or an exact index constituent file.

In **Exposure**, **Break down ETFs** starts enabled. Every supported fund in
the portfolio starts enabled. Under **Data & settings → Individual ETFs**, turn off any fund to
keep it as a single instrument. These session choices survive switching the
master control off and back on. Newly supported funds start enabled. Unsupported
funds remain visible with a missing-breakdown notice. The same selections apply
to allocation, targets, performance coverage, and the stock-only exposure view;
strategic ownership and tradable source positions stay intact.

Select an asset to open its contributing positions and expandable **ETF breakdown**
tables, with dates, source links, coverage, proxy status, and import notes.
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

### Newly added and imported funds

After positions load, the background worker discovers missing breakdowns and
installs validated snapshots. This includes positions created by the experimental
FinanzManager/Lexware importer with an ISIN but no ticker or instrument type.
Import review itself stays offline. Discovery does not change quantities,
acquisition costs, saved identities, targets, classifications or snapshot pricing.

ISIN is authoritative. WKN and qualified listing searches supply candidates;
only a unique issuer-confirmed identity is accepted. These searches can be
unavailable or incomplete. In **Exposure → Data & settings → ETF refresh &
snapshots**, inspect per-position status or use **Set up a breakdown** to supply
an official iShares/Xtrackers product page. A WKN-only position can use this route
when the issuer confirms its WKN. **Positions → Connect live prices** also
connects a position to a verified ISIN; retain manual pricing by leaving the
switch-to-live-prices option off. Names and bare tickers are never identity keys.

The setup panel also accepts a normalized UTF-8 constituent CSV for physical
funds from other providers. Supply the exact fund ISIN, holdings date and asset
class. Required columns are `constituent_id,name,ticker,isin,weight`; weights are
fractions of the whole fund. Bond/money-market files also require an explicit
`instrument_type` per row. Optional metadata includes `issuer`, `country`,
`market_currency`, ISO `maturity` and `credit_rating`. Review identity, date,
interpretation and coverage before **Save breakdown**. Source and upload drafts
remain in session memory until saved. Partial data retains `Other`.
Saving a manual CSV disables provider refresh for that snapshot, including funds
with built-in integrations. Preview and save an official source to re-enable it.

### Bonds and overnight-rate funds

Open an asset's contributing positions and its **ETF breakdown** panel, or inspect
saved breakdowns in **ETF refresh & snapshots** even before connecting prices. Physical
bond funds default to **Summary**, with issuer, country, denomination currency,
maturity and credit-quality views. **Holdings** shows the separate securities,
including ISINs where supplied. Different bonds from one issuer remain distinct;
issuer summaries do not merge them with the issuer's shares.

Maturity bands use the holdings date: under 1, 1–3, 3–5, 5–10 and 10+ years.
Missing metadata remains Unknown. Published aggregate ratings are shown separately
when individual ratings are unavailable; they are never assigned to securities.
Provider duration, maturity, coupon and yield metrics carry their own source dates.
Denomination currency is not a measure of net currency risk after hedging.
Xtrackers cash liabilities offset cash assets in a net cash pool without an assigned
country or currency; equity weights and the total remain unchanged. Signed
substitute baskets retain their original rows. iShares cash, money-market
holdings, collateral and FX form a net liquidity pool;
net borrowing that cannot fit the unsigned allocation model is rejected.

XEON's economic view represents its EUR overnight-rate benchmark, the Solactive
€STR +8.5 Daily Total Return Index. Its actual substitute basket is visible under
**Holdings**, with signed basket weights preserved, but never contributes to
portfolio company, country or bond allocations. Economic representation and basket
coverage are distinct. Other unverified synthetic or unsupported compositions
remain whole fund positions; there is no automatic same-index proxy substitution.

Public provider checks cover iShares Core MSCI World (IE00B4L5Y983), MSCI World
ex-USA (IE000R4ZNTN3), Core DAX accumulating (DE0005933931), Core Euro Government
Bond (IE00B4WXJJ64), and XEON (LU0290358497). These are coverage examples, not a
hard-coded discovery list. Unit tests and browser demos use invented data.

### Combine the ETF with direct stocks

Enable **Group SMH with related stocks** in Exposure → Data & settings to show the held UCITS
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

### Snapshot installation

To install or refresh one supported snapshot in a private workspace:

```bash
uv run python -m portfolio_app.etf_sources <ISIN> --data-dir /path/to/private/workspace
```

The command resolves an ISIN through the same provider discovery used by the app.
Use `--product-url <official-product-page>` when needed. Existing explicit
integrations remain supported. It writes snapshots under `etfs/` and a workspace
writer lock; it never changes holdings, targets or local labels.
Xtrackers uses full-precision JSON percentage weights and ISINs. New iShares
discoveries use complete named holdings arrays with ISINs, market values and published weights. Legacy integrations retain their XML Spreadsheet
**Holdings / All** export. Both derive weights from all market values and check
against published percentages. This preserves small holdings lost to two-decimal percentage rounding. Net cash includes cash
liabilities, money-market holdings, collateral, and FX. Futures notional
exposure is not assigned to companies. Unsupported signed equity or net
borrowing fails validation instead of silently dropping liabilities.

Provider sectors and countries supply fallback taxonomy paths, leaving local
classifications in charge. Where iShares supplies only local tickers, they are
retained as source metadata rather than treated as exchange-qualified symbols.
Provider parsers do not infer missing ISINs. Company-level overlap is handled
separately by the reviewable matching described below.

**Exposure → Data & settings → Company merges** groups the same company across ETFs,
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

**Exposure → Data & settings → ETF refresh & snapshots** displays provider dates,
last attempts and successful checks. The app automatically checks held, supported
funds and discovers missing snapshots when a portfolio is opened and on subsequent interactions. By default,
snapshots at least **one day old** qualify, with at most one attempt per fund per
24 hours. The age threshold is configurable from 1–30 days; automatic updates can
be disabled. Attempts are stored privately so restarting does not trigger repeated
downloads. A manual **Refresh ETF holdings now** bypasses the daily throttle.

Downloads run in a background thread while the UI uses saved snapshots. A small
status polls for completion and reloads the analysis automatically. A workspace
lock prevents concurrent refresh writers, including across app instances. Failed
updates keep the prior snapshot and can be retried manually. Snapshots older than
seven days still get a recency notice; a fresh check need not mean fresh holdings.
Refresh is separate from market-price refresh and is disabled only in the explicit offline demo mode.
It runs inside the portfolio app, with no Codex session required. The app must be
running; this is not an operating-system scheduler.

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
`exposure_analysis.py` owns source selection and normalized ETF expansion without
Streamlit; the UI supplies selections and renders the resulting tables.
`currencies.py` shares quote-subunit normalization across spot and historical prices.

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


## Shared list presentation

`src/portfolio_app/list_ui.py` defines the shared read-only list component,
column formatting, taxonomy badges, sorting, row actions and scrolling policy.
Overview allocations/performance, position lists, Exposure assets and source
contributions, ETF constituent lists, and Suggested trades use it. Keep
presentation changes here instead of copying table CSS or row-selection controls
into individual pages.
Use `ListColumn` for column labels, numeric precision and signed values.

Lists grow with the page by default. Exposure enables the shared
`BOUNDED_LIST_HEIGHT` only when ETF breakdown is enabled; ETF constituent details
also use that limit. Narrow screens retain horizontal scrolling. Editable
spreadsheet forms and analytical diagnostic tables retain their native grid
controls. Search within a displayed list does not recalculate allocation weights.

Exposure's main list shows how many source positions contribute to each asset.
Opening an asset shows each direct or ETF position's euro contribution and its
percentage of that asset's exposure, with separate account positions preserved.
Missing source valuations leave contribution percentages unavailable.

## Parallel development

Use a separate Git worktree and branch for each coding session, with one session
responsible for integrating shared UI changes. Run each demo on a different port
and use generated synthetic data. Personal data and the private handoff remain
outside Git; consult the original handoff read-only when working in a worktree.
Run `uv run pytest` after integration. Browser checks are optional locally:
`uv run --with playwright pytest tests/test_ux_browser.py tests/test_dashboard_browser.py`.
Set `PORTFOLIO_TEST_CHROMIUM` to an available Chromium binary if needed.

## Analytics development notes

Source-only continuation notes, updated 2026-09-27. See the sections above
for application usage and formulas, and [AGENTS.md](AGENTS.md) for privacy and
development requirements. These notes contain no working portfolio data.

### Current product state

- Navigation follows the UI refactor: Overview, Exposure, Positions, Rebalance.
- Overview → Analytics shares the existing category selection, including its
  descendants. Accounts can be narrowed in Options. Exposure filters and position
  search do not change this scope.
- Historical risk comes first. Calculate risk loads the estimates; subsequent
  refreshes are explicit. Benchmark and history window are in Options.
- Risk shows beta, annualized volatility, benchmark correlation, per-holding
  beta and volatility contributions, and data coverage. The expandable correlation
  matrix uses display names without appended identifiers, a purple–teal diverging
  scale fixed at −1 to +1, a nearby color bar, and a larger responsive plot.
- Valuation and income follow risk. Direct-stock P/E excludes ETFs and
  nonpositive ratios. Fund fees and cash distribution yields do include eligible
  ETFs. Concentration and underlying company exposure are separate views of risk.
- Positions has Holdings, Valuation, Income & fees, and Risk views. Optional
  columns and settings live in Options. Selecting a row opens its existing
  details dialog; Key metrics contains fundamentals, definitions, provenance,
  and a private fund-fee editor. Holdings does not request analytics data.
- Preserve the refactor's shared table interactions, short names, search, sort,
  keyboard access, row details, and separate pencil editing when extending views.

### Calculation contracts

- Snapshot weights use current EUR market values and combine repeated instrument
  IDs across accounts. Buy-in performance belongs to Overview → Performance.
- P/E is the inverse of value-weighted earnings yield among covered profitable
  direct equities. It is not an arithmetic average, and fund-reported P/E is not
  blended into it. Coverage is essential to interpreting this subset statistic.
- Fees are current fund values × annual fee rates. They are already reflected
  in prices and must not be deducted from gains a second time. Public fee metadata
  is matched by exact share class, not an ETF look-through proxy.
- Cash yield is a trailing distribution estimate, not a forecast or actual
  dividends received. Verified accumulating share classes have zero cash yield.
- Risk uses adjusted market-price histories, converted with historical FX to
  EUR. The latest available price within each completed Friday-ending week is
  used. Empty weeks are not filled, and returns do not bridge missing endpoints.
- Default benchmark is IUSQ.DE, a global equity ETF proxy; windows are 1, 3, or
  5 years, default 3. All included holdings and the benchmark share the same
  sample of at least 52 weekly returns. Provider-supplied betas are not used.
- Portfolio weekly return is the sum of holding weekly returns × today's
  weights. Beta is covariance with the benchmark / benchmark variance.
  Annualized covariance is weekly covariance × 52. Contributions to volatility
  sum to portfolio volatility; negative contributions are possible.
- This is a hypothetical constant-weight allocation, not the user's historical
  return series. It requires no transaction history and does not use buy-in prices.
- EUR cash has zero returns. Manual/unlisted positions without supported history
  are excluded. Covered weights are renormalized, and partial coverage is shown.
  Missing valuations prevent claiming whole-portfolio coverage.
- ETFs use their own adjusted price histories for risk, not constituent expansion.
  Category beta is computed in exactly the same way within the selected category;
  its benchmark remains the selected benchmark, even for a non-equity category.
- Borrowing, liabilities, derivative notionals, and net-equity leverage are not
  modeled. Asset-normalized beta must not be presented as levered equity beta.

### Code map

| Module | Responsibility |
| --- | --- |
| `fundamentals.py` | Metric definitions, provider normalization, dated public fees, private overrides |
| `analytics_cache.py` | Atomic optional-analytics cache, expiry, failed-request cooldown |
| `analytics.py` | Pure snapshot aggregation and concentration |
| `risk_data.py` | Adjusted price histories and historical EUR conversion |
| `risk.py` | Pure common-sample weekly risk calculations |
| `analytics_service.py` | Provider/cache orchestration for fundamentals and risk, independent of Streamlit |
| `analytics_ui.py` | Shared formatting, sources, benchmark settings |
| `portfolio_analytics_ui.py` | Overview analytics presentation |
| `position_metrics_ui.py` | Position metric presets, details, fee maintenance |
| `charts.py` → `correlation_chart` | Correlation matrix rendering |
| `position_list.py`, `position_detail.py`, `strategic_ui.py` | Integration with the existing UX |

Keep analytics outside the holdings → valuation → optional ETF expansion →
normalized exposures → classifications → aggregation pipeline. Reuse the valued
positions already prepared by the app instead of fetching valuations again.
Company concentration reuses the existing company mapping and ETF exposure logic.

Analytics caches are private and separate from the ordinary price/chart caches.
Their normal lifetime is 24 hours; failed requests have a 15-minute cooldown.
Failures can retain visibly stale data. Explicit refresh retries immediately.
Fee overrides are private `fund-fees.json` data. Do not commit overrides, caches,
generated screenshots, diagnostics, or real portfolio examples.

### Potential extensions and decisions to make first

These are ideas, not promises or already implemented features.

1. **Compare categories side by side.** Show beta, volatility, value share, and
   coverage for sibling categories. Decide whether all categories must share a
   single common sample; independently estimated betas are less comparable.
   Distinguish category beta from its contribution to whole-portfolio beta.
2. **Current versus proposed allocation.** Preview hypothetical target weights
   with beta, volatility, concentration, and coverage. Reuse a single historical
   sample and covariance matrix for both scenarios. Decide how this connects to
   Rebalance and how infeasible targets or missing histories are displayed.
3. **Beta stability.** Add rolling beta or comparisons across windows, confidence
   intervals, and benchmark explanatory power. A point estimate alone should not
   imply a stable forecast. Do not confuse beta with total volatility.
4. **Leverage-aware planning.** Requires an explicit liabilities/net-equity model,
   financing costs, exposure definitions, and rebalancing assumptions. A leveraged
   ETF's advertised daily multiple is not a guaranteed multiyear return multiple
   or its beta against an arbitrary benchmark. This needs domain design before code.
5. **Downside scenarios.** Separate simple beta-based shocks from historical
   constant-weight scenarios. Neither reconstructs personal performance or sets
   a worst-case loss bound. Define treatment of missing assets and costs first.
6. **Fund valuation coverage.** Keep fund-reported P/E separate for now. Combining
   direct stocks and look-through fund earnings requires compatible earnings,
   dates, loss treatment, and coverage; do not silently average unlike ratios.
7. **Metric loading UX.** Risk currently needs Calculate risk; fundamentals load
   when Analytics is selected. Decide whether to load risk automatically, retain
   results across scope changes, or add progress/background loading. Preserve
   clear freshness and partial-data reporting and avoid repeated network requests.
8. **Benchmark presets and persistence.** Currently a ticker field and a shared
   benchmark per analytics view. Category-specific defaults, saved preferences,
   or named presets would require explicit scope and persistence choices.
9. **Large heatmaps.** Consider top-N selection, ordering/clustering, and duplicate
   display-name disambiguation without reintroducing noisy ticker suffixes.
   Numeric axis coordinates already preserve distinct cells for duplicate names.

### Validation and resuming work

Use `uv run pytest`. Focused analytics tests are `tests/test_analytics.py`,
`tests/test_risk.py`, and `tests/test_analytics_ui.py`. Browser coverage is in
`tests/test_analytics_browser.py`, with shared synthetic fixtures from
`tests/test_ux_browser.py`. Tests must never read the working portfolio or require
live market-data access. Browser screenshots must remain in temporary directories.

The final integration preserves the completed UI refactor and background
market-data implementation checkpointed in `83b3f11`. Analytics uses the shared
`ListColumn` renderer, including numeric sorting and optional display text, and
its presets and benchmark settings survive lazy tab changes. The earlier
in-progress integration snapshot and its failing lazy-view tests are obsolete.
Integration validation: 651 core tests passed (8 optional browser modules skipped),
and all 15 selected browser checks passed across analytics, shared position lists,
navigation, background market-data loading, and exposure. The background-loading
test waits for the synthetic worker to acknowledge startup before inspecting its
call log. Re-run validation against the current tree when continuing development.

Continue new work from `main` after this integration. The original feature branch
is `codex/portfolio-analytics`; the final reconciliation was prepared separately
on `codex/analytics-final-merge`. Earlier temporary integration worktrees may
contain obsolete attempts. Inspect `git status`, `git worktree list`, and the
actual diff before merging or cleaning up. Future sessions may have uncommitted
work: do not reset, stash, delete, or commit it just to make a tree clean.

For a separate preview:

```bash
uv run portfolio-app --data-dir /path/to/private/data --server.port 8502 --server.runOnSave true
```

Use the intended persistent data directory explicitly when launching from a
worktree. Restart that preview if imported modules remain stale; do not restart another session's app. Check the
port before assuming which version a browser tab displays.
