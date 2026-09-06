# Portfolio breakdown

A local CSV/YAML-backed portfolio explorer built with Python, Streamlit, and
Plotly. It supports current EUR valuation, hierarchical classifications,
filtering, allocation charts, optional position targets, and ETF look-through
for the VanEck Semiconductor UCITS ETF.

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

The main tabs separate **Overview** from **Manage positions**. Overview provides
value cards, allocation charts, and a compact holdings table. Open **Filter
positions** or **Market data** in the sidebar for their controls. **Show price
details** exposes quote timestamps, FX status, and valuation notes when needed.

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
average buy-in, buy-in currency, or target. Instrument identity stays fixed;
edit the CSV for instrument-wide ticker/name corrections. The app prevents
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
- Chart clicks navigate within the chart. The sidebar root control updates
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

## VanEck Semiconductor UCITS breakdown

The supported fund is **VanEck Semiconductor UCITS ETF**, ISIN
**IE00BMC38736**. Local snapshots can be imported from
[VanEck's complete holdings download](https://www.vaneck.com/no/en/investments/semiconductor-etf/downloads/holdings/)
and remain private in your data directory. The offline demo includes explicitly
synthetic weights, not a provider snapshot. For configured snapshots, the
expandable **ETF breakdown** table is available even without a position in the
fund. No ETF position or share count is added automatically.

Use **Portfolio representation → ETF look-through** to replace selected,
valued UCITS positions with their effective constituent exposures. A position
matches by ISIN or, when ISIN is blank, a supported qualified ticker:
`VVSM.DE`, `SMH.L`, `SMGB.L`, `SMH.MI`, or `SMH.PA`. The London USD listing is
`SMH.L`; the Xetra EUR listing is `VVSM.DE`.
These are listings of the same fund; see
[VanEck's trading information](https://www.vaneck.com/uk/en/library/fact-sheets/smh-fact-sheet.pdf).
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

Fund snapshots live in the active portfolio's `etfs/` directory: a YAML manifest identifies the fund, date,
source, listing aliases, and constituent CSV. Weights are fractions of the
entire ETF. For partial snapshots, the remainder is explicitly retained as
`Other`; named holdings are never scaled to 100%.

### Recency and updates

The sidebar displays the snapshot date and calendar-day age on every rerun.
Snapshots **older than seven days** are flagged. Click **Update from VanEck** to
download the provider's latest XLSX and refresh the snapshot. This control is
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

Tests use generated synthetic fixtures, injected prices, FX, search responses,
and clocks, with no live network required.
They cover valuation, persistent cache fallback, parent/child conservation,
multi-path classifications, filtering, denominators, targets, ETF residuals,
company exposure merging, snapshot refresh failure recovery, position saves,
backups, stale-edit rejection, bulk purchase averaging and historical cost entry,
listing search, Git privacy guards, and Streamlit controls. The server health
endpoint is `/_stcore/health`.

Hierarchy-node targets, rebalance calculations, P&L, and historical analysis
remain deferred.
