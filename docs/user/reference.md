# Data formats and implementation reference

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

Source-only continuation notes, updated 2026-09-27. See the task guides
for application usage and formulas, and [contributor instructions](https://github.com/eliaskempf/portfolio-breakdown/blob/main/AGENTS.md) for privacy and
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
