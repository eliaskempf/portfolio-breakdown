# Source development

This page is for contributors working in a source checkout. Installed-app users
can use their shortcut and the in-app controls; Python and uv are not needed.
Start with [source installation](install.md#source) before running these commands.

## Architecture and verification

Business logic is independent of Streamlit:

```text
holdings → reporting-currency valuation → normalized exposures → optional ETF expansion → classifications
         → hierarchical aggregation → charts and tables
```

Position filters run after valuation and before exposure normalization. The
exposure table retains asset IDs and source-position metadata through ETF
expansion. Provider parsing and refresh code are separate from the generic ETF
transformation and aggregation engine. All chart types share the same aggregation engine.
`exposure_analysis.py` owns source selection and normalized ETF expansion without
Streamlit; the UI supplies selections and renders the resulting tables.
`currencies.py` shares quote-subunit normalization across spot and historical prices.

`application.py` selects price/FX services, checks leverage eligibility and prepares
valuation diagnostics without Streamlit. `input_cache.py` returns named
`PortfolioInputs` and copies cached inputs before handing them to calculations.
`ui.py` owns workspace recovery, widget interactions, explicit refresh requests
and lazy view dispatch. Optional fundamentals and risk remain in
`analytics_service.py`; opening an unrelated view must not fetch them.

Actual accounts join the same pipeline at the reporting-currency valuation
boundary. `account_journal.py` replays native Decimal cash, explicit loans, futures
lots and pending claims. `actual_portfolio.py` projects these into valued rows,
replacing adopted cash holdings and retaining position/account provenance.
`portfolio_exposure.py` then uses separate capital and signed-exposure measures;
provider parsing and journal persistence do not belong in the aggregation engine.

### Calculation contracts

- Saved positions retain their instrument ID and stable position ID. A
  contribution has an analytical asset ID plus its source position/instrument
  identity; company grouping must not overwrite the original security identity.
- `exposures.project_contributions` accepts an explicitly aligned measure and
  copies source dimensions. Current value, target fractions and performance
  amounts can use the same transformation without pretending to be the same
  financial measure. Reserved calculation columns are rejected as custom CSV
  metadata. New calculated fields must extend that reservation.
- `constituents.py` interprets normalized constituent facts. Each consumer owns
  its coverage policy: an equity fund's residual can be equity exposure while
  remaining unresolved company exposure. Futures proxy coverage also retains
  its stricter treatment of unidentified and non-equity weights.
- Actual contract facts use `derivative_types.py`; `account_journal.py` owns
  signed lot arithmetic, FIFO reductions, settlements and native postings.
  Settled P&L is already cash; only unsettled P&L and pending claims add derivative
  wealth. Notional is exposure, never wealth. Security collateral is a quantity
  reservation against an existing holding, not another asset.
- `account_observations.py` and `derivative_market.py` keep dated marks and free
  exact-contract observations separate from cash postings. `account_margin.py`
  evaluates entered requirements and haircuts without inferring broker rules.
  `account_planning.py` previews integer trades without changing account facts.
- `FuturePosition` and `future_projections.py` retain the legacy EUR sandbox's
  funded-strategy arithmetic and named capital, exposure and risk adapters.
  Legacy simulation value must not enter actual account projections.
- `generalized_studies.py` consumes copied scenario inputs, native price paths
  and explicit FX, margin, rate and transfer assumptions. Its results never write
  the account journal. `study_market.py` validates history identity, currency and
  return convention before a proxy can drive a reconstruction.
- Missing amounts are not zero. Callers retain completeness information when
  projecting known contributions. `prepare_market_analysis` returns separate
  known-value chart nodes and completeness-preserving table nodes. Composition
  percentages use selected exposure; portfolio impact uses whole-portfolio equity.

Preparation results own their output frames; frozen result containers do not make
pandas frames deeply immutable. Treat inputs as borrowed, copy before mutation,
and verify conservation, source provenance and input nonmutation when changing
these boundaries. `company_analysis.py` prepares company grouping and coverage;
renderers consume the prepared results.

### Session lifetimes

`state_lifecycle.py` is the single catalog of feature-owned navigation,
workspace, currency and tour policies. Add an ordinary widget input to its
feature's declaration, with the correct mount boundary. Inputs may survive lazy
unmounts and are restored once on remount so the browser receives the retained
value. Remount bookkeeping is consumed only after view dispatch, preserving it
across early-return dialogs. Buttons, upload handles and editor patches must not
be reassigned as ordinary input state. Imports and persistent editors keep their specialized
adapters. Calculation fingerprints stay with the calculators that understand
their assumptions.

Currency changes clear monetary drafts and prior monetary plans while retaining
scope and percentage preferences. Workspace changes clear owned drafts/results;
workspace-keyed analytics preferences remain isolated by their keys. Tour capture
copies only declared restorable state. Exercise real navigation as well as pure
transition tests, including date controls and nested lazy views.

Document writes retain their workspace locking, revision checks and backup
policies. Commands validating multiple documents must hold the workspace lock
from validation through publication. Cache and document writers deliberately
retain different failure/recovery policies; sharing serialization mechanics must
not erase those differences. `atomic_file.staged_text` only creates and cleans up
a same-directory text stage, optionally flushing and syncing its content. Callers
choose serialization, locks, backups and revision checks, then explicitly replace
the destination. Document writers sync before backing up and rechecking revisions;
cache writers preserve their own validation and failure fallback. ETF snapshot
publication still writes the manifest last; it is a separate multi-file protocol.

`money.py` formats scalar amounts with explicit currency and no Streamlit state.
`currency_display.py` supplies session reporting units and numeric formats for
native grids. Actual account entries retain EUR/USD/GBP units; projections perform
explicit conversion before display. Only legacy fixed-EUR sandbox views use
`money.euros`; formatting never converts amounts. Missing values remain missing. Shared list
columns retain numeric sorting and their browser formatter. `charts.chart_config`
provides compact allocation controls and explicit zoom/pan/reset controls for
scenario studies; each chart receives independent configuration containers.

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

Independent target overrides on analytical-taxonomy nodes, ordinary-security
sales/realized P&L and transaction-based personal return history remain
unsupported. The development account journal records actual futures settlements
and closes; an opening snapshot does not reconstruct pre-snapshot gains.
Strategic category targets, instrument price history and historical risk analysis
are supported.


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
Opening an asset shows each direct or ETF position's reporting-currency
contribution and its
percentage of that asset's exposure, with separate account positions preserved.
Missing source valuations leave contribution percentages unavailable.

## Parallel development

Use a separate Git worktree and branch for each coding session, with one session
responsible for integrating shared UI changes. Run each demo on a different port
and use generated synthetic data. Personal data and the private handoff remain
outside Git. In a source checkout, use `docs/release-plan.md` for the reusable
release procedure, `docs/release-checklist.md` for acceptance criteria, and these
behavior/reference guides for product context. Tests and demos must not read
private working data.
Run `uv run pytest` after integration. Browser checks are optional locally:
`uv run --with playwright pytest tests/test_ux_browser.py tests/test_dashboard_browser.py`.
Set `PORTFOLIO_TEST_CHROMIUM` to an available Chromium binary if needed.

## Analytics development notes

These implementation notes are also published with the guide. See the task
guides for application usage and formulas, and [contributor instructions](https://github.com/eliaskempf/portfolio-breakdown/blob/main/AGENTS.md) for privacy and
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

- Snapshot weights use current reporting-currency market values and combine repeated instrument
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
  the reporting currency. The latest available price within each completed Friday-ending week is
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
- Cash in the reporting currency has zero FX returns. In ordinary asset analysis,
  manual/unlisted positions without supported history are excluded, covered
  weights are renormalized, and partial coverage is shown. Missing valuations
  prevent claiming whole-portfolio coverage. Integrated account leverage instead
  withholds unsupported aggregate risk when the required coverage is incomplete.
- ETFs use their own adjusted price histories for risk, not constituent expansion.
  Category beta is computed in exactly the same way within the selected category;
  its benchmark remains the selected benchmark, even for a non-equity category.
- The released app uses asset-normalized beta. The development branch adds
  [actual-account leverage](futures.md) in EUR/USD/GBP, with a separate net-equity
  denominator. Asset-normalized beta must not be presented as levered equity beta;
  legacy sandbox risk remains EUR-only.
- Actual futures risk uses signed notional with compatible contract-currency
  histories and converts native changes at dated FX. It must not apply the FX
  return on the whole futures notional. Foreign cash, claims and debt carry their
  own FX risk. A documented leveraged ETF factor changes current exposure; risk
  still uses that fund's own price returns without multiplying leverage twice.

### Code map

| Module | Responsibility |
| --- | --- |
| `fundamentals.py` | Metric definitions, provider normalization, dated public fees, private overrides |
| `analytics_cache.py` | Atomic optional-analytics cache, expiry, failed-request cooldown |
| `analytics.py` | Pure snapshot aggregation and concentration |
| `risk_data.py` | Adjusted price histories and historical reporting-currency conversion |
| `risk.py` | Pure common-sample weekly risk calculations |
| `analytics_service.py` | Provider/cache orchestration for fundamentals and risk, independent of Streamlit |
| `company_analysis.py` | Pure company grouping and exposure preparation, plus workspace mapping loading |
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
test screenshots, diagnostics, or real portfolio examples. Only explicitly
reviewed synthetic guide images may enter the public documentation.

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
4. **Joint funded-asset and futures planning.** The development branch already
   provides actual-account futures plans alongside the capital planner. Joint
   routing between funded holdings and derivatives would need explicit funding,
   transfer and financing assumptions. A leveraged ETF's advertised daily
   multiple is neither a guaranteed multiyear return multiple nor its beta against
   an arbitrary benchmark.
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
live market-data access. Browser-test screenshots remain in temporary directories;
the separate guide-image capture/review procedure is in
`docs/documentation-maintenance.md` in the source checkout.

Run checks against the exact intended source commit. Previous test counts and
another checkout's running preview do not establish that the current app works.
Use a separate linked worktree per task and preserve other sessions' changes.

For a separate preview:

```bash
uv run portfolio-app --data-dir /path/to/synthetic-workspace --server.port 8502 --server.runOnSave true
```

Use a generated synthetic workspace explicitly when launching from a
worktree. Restart that preview if imported modules remain stale; do not restart another session's app. Check the
port before assuming which version a browser tab displays.


## Before committing

Keep the pre-commit hook enabled, run `uv run portfolio-check-private --staged`,
and inspect the staged diff. Never commit private holdings or portfolio exports.
