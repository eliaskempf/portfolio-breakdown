# Source development

This page is for contributors working in a source checkout. Installed-app users
can use their shortcut and the in-app controls; Python and uv are not needed.
Start with [source installation](install.md#source) before running these commands.

## Interface design

The main interface prioritizes quantitative results, exploration and actions.
Labels, grouping and consistent controls should make the view understandable
without introductory paragraphs or repeated explanations.

- Keep visible prose minimal. Prefer concise labels and short, contextual status
  text. Put definitions in tooltips or help popovers, and methodology, assumptions,
  provenance and extended explanations in expandable details or the user guide.
- Preserve essential meaning in the main view: selected scope, units, the metric's
  basis and whether a result is approximate, partial or unavailable. Explanations
  may be disclosed on demand; material limitations must remain discoverable from
  a visible status beside the affected result.
- Use compact, local indicators for routine partial coverage, excluded holdings
  and cached data. Reserve prominent warning banners for blocking problems or
  situations requiring timely action. Match color and visual prominence to the
  importance of the state; ordinary analytical limitations should not dominate
  the page.
- Put secondary figures near their primary metric. Consolidate repeated status
  messages and avoid stacks of captions above or below a result. Keep controls
  aligned with the section they affect and use space to communicate hierarchy.
- Preserve established styling and shared components. Improve density, placement
  and consistency before adding decorative elements, new panels or new navigation.
- Import review must identify the affected statement, show the specific unresolved
  source rows locally, and offer a decision for that statement. Do not ask users
  to certify a batch with a blanket checkbox or infer a problem from an absent
  tax/fee row. Routine fully reconciled statements should not need manual approval.
- Keep help and status details accessible by keyboard and touch, with meaningful
  labels. Color and hover alone must not carry essential information.

Apply these principles across the application, including new features and error
states. They govern presentation without weakening calculation or coverage rules.

Apply `chart_hover` at each Plotly rendering boundary. Curves share a vertical
date/value guide and formatted tooltips across the plot area; category and
allocation charts retain their meaningful category tooltips. Preserve explicit
units, missing values and purpose-built hover content. Pointer movement must not
trigger Streamlit reruns. Overview history's value/capital boxes follow the hovered
observations and include their dates. The inline gain switches from selected-period
gain to total known gain during hover. Any range covering the full holding history
uses the shared total known gain calculation, just like All. Personal and price
histories use currency symbols consistently in summaries, axes and tooltips.
Leaving restores the latest value and period gain. Clicking the gain badge toggles between amount and cumulative TWR; its metric name and period
appear only in a concise tooltip. Return percentages use ↑/↓; monetary gains
keep +/− signs. Zero and unavailable returns have no direction arrow. TWR requires exact boundaries; no remaining-cost denominator is substituted.
Annualized money-weighted return always uses full recorded history, independently
of the chart range or pointer. Keep one summary row below the view tabs.
The allocation measure switch belongs above the allocation table, aligned with
its left edge and the top of the chart. Performance keeps category comparison
visible and leaves detailed category and position lists to their existing views.

### View changes and progressive disclosure

Related views must share card widths, minimum heights and alignment. Keep their
navigation and outer container stable. Position detail dialogs keep a viewport-bounded
height across views and loading states; long content scrolls inside the window.
Test the outer bounds throughout transitions, not just card dimensions. Replace
the outgoing content before doing expensive work for the next view. Do not
calculate a complete history merely to
choose an already-selected tab, or repeat calculations during the same render.
Reuse chart results only while journal facts, observation revisions, scope, currency,
period and calendar date remain unchanged. Within each chart, share dated ledger
states across overlapping intervals without relaxing coverage checks. The Overview
history chart, current-gain summary and category comparison share one calculation
context for the same journal and observation snapshot. Current quotes remain
separate from dated historical valuations. Recorded cost and cash components may
be reused between transaction/fee dates; daily prices and reporting FX still
resolve at the requested date. A new observation revision creates a new context,
including when it arrives in a background fragment. Account
history derives securities cash postings once per journal snapshot and reuses
account states between cash/account event dates. A settlement value date alone
does not release a pending receipt; explicit receipt events remain authoritative.
Background data refreshes must remain local to the affected result. Verify actual
transitions with delayed synthetic data: settled screenshots alone miss old
controls and charts briefly appearing during a switch.

Position opening and clean dismissal rerun only the dialog host. Child workflows
share a local refresh boundary, including evidence saves, reconciliation, market
evidence repairs and completeness review. Persisted changes refresh current
position values locally and refresh the parent page once after dismissal. Pending
evidence alone does not repeat portfolio valuation. Streamlit 1.65 or newer is
required for the keyed host callbacks; the runtime adapter is isolated in
`dialog_runtime.py` and covered by browser lifecycle tests. Transaction list, import, edit and reconciliation screens use distinct keyed containers so their action rows are replaced together. Never leave list actions alongside review/apply controls. Evidence saves retain revision guards and leave balances unchanged until
explicitly applied. Position detail tabs always use History, Key metrics,
Transactions and Details. Without recorded history evidence, History shows the
price chart directly. Otherwise it contains a Holdings / Price selector, defaulting
to Holdings when useful holdings history exists and Price otherwise. Remember an
explicit choice per position for the session, separately from the editor draft;
clear these preferences when switching workspaces. Switching history type keeps
the dialog and selector stable and replaces only the result beneath them.
Position details keep one shared summary row above the chart selector: Position
value with supported personal gain, Invested capital, and Annualized return.
These figures retain their current/all-history basis across tabs, chart types,
periods and pointer movement. Dated value, capital, total gain and TWR remain in
chart tooltips; position charts do not rewrite headline cards. Return on cost and
unrealized gain are listed in Details. Without applied history, capital uses the
recorded acquisition cost and gain is explicitly unrealized only; annualized
return remains unavailable. Complete-history gates are unchanged.

Missing-data interfaces must identify the affected result and provide a relevant
action. Do not expose a combined list of every metric's requirements as mandatory
setup. Keep ordinary gaps subtle. Retrieve supported market data automatically;
manual repair forms should preselect the position, date and currencies when known.
Internal identities and fingerprints are storage details, not user-facing labels.
Show overwrite confirmation only when a value will actually be replaced.

Offer **Apply and confirm history** as the normal reconciliation action, with a
visible interval and an **Apply incomplete history** alternative. Reconciliation
and completeness remain distinct facts; the combined action explicitly records
both in one journal write. Matching today's holding must never silently certify
omitted sales or income. Keep confirmation inside the existing history review;
do not add a separate toolbar shortcut for this catch-up workflow. Position attestations bind retained
facts and transfer-connected holdings, so unrelated position edits do not undo
review. Legacy whole-journal attestations keep their original validation rules.
Manual market-data correction is separate from this review. Broader changes to
established interfaces require discussion rather than opportunistic redesign.

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

`monitoring.py` owns indicator and account-rule calculations; `monitoring_market.py`
normalizes provider observations, `monitoring_service.py` schedules checks while
a session is open, and `monitoring_store.py` persists rule revisions and alert
episodes under the workspace lock. Monitoring does not change holdings or orders.
`tour_monitoring.py` creates its example only after verifying the active tour-owned
synthetic directory; it reuses the normal alert explanation and acknowledgment UI.

### Calculation contracts

`asset_history.py` calculates approximate daily performance over the existing
forward securities replay and scope-aware external flows. Its calculation takes
an explicit opening, never an ending quantity. Selected-period capital starts at
market value. Daily Dietz uses midpoint flows and does not alter exact-return
coverage. Inventory with unresolved flow classification or a balance reset within
the interval leaves dependent gains and returns unavailable.

`transaction_evidence.py` provides signed per-location and per-asset diagnostics
independent of activation, plus arithmetic reference comparisons. Holdings are not
inputs to movement normalization. Openings, checkpoints and unknown charges remain
separate. Explicit source/event deduction links preserve raw evidence and ordinary
correction lineage; the linked inventory record owns the known quantity without
asserting it is a fee. Do not infer such links from matching amounts or dates.

Aggregate import maps reviewed locations to one position per asset before normal
forward replay. The book's `custody_id` records a stable `aggregate:` scope digest.
Transfers within that position preserve lots and principal; charges still apply.
Existing custody books, cash binding and ordering checks remain supported. Active
constituents cannot coexist with the new authoritative aggregate holding.

`asset-performance.json` version 2 uses `forward-daily-dietz-v1`, source namespace,
owned locations and asset/position/listing mapping; it stores no opening or ending
quantities. Version 1 remains readable for backup/inspection but cannot route a
chart or be newly saved. Explicit mapping review replaces it under normal document
backup, locking and revision checks. Applied history uses active journal events;
retained-only revisions affect evidence diagnostics until separately activated.

Generic transaction CSV v2 adds inventory receipts/removals, balance checkpoints
and sourced monetary estimates to the v1 contract. Journal v4 persists these
facts; serializers retain v1–v3 wire shapes and receipt identities. A checkpoint
is an end-of-day reporting boundary with a separate optional observation
timestamp. It resets authoritative units/cost without synthesizing cash,
income, an acquisition date or earlier holdings. The import command requires
explicit checkpoint acceptance under the same revision/lock checks as ordinary
activation. Prior records and corrections remain retained.

Acquisition cost components may carry `quality` and `source`. Estimate flags
survive FIFO consumption and custody transfers. `cost_quantity` partitions
costed and unknown units instead of treating a known subtotal as full cost.
`transaction_evidence.py` computes signed movement diagnostics independently
of activated balances and monetary completeness. It must never project those
changes as balances from an unconfirmed zero. Source-specific archive conversion
does not belong in these modules.

- `gain_accounting.py` supplies shared current and historical known-gain results.
  Security-only and account-only cash flows cancel across their common boundary;
  account equity includes actual futures once, not their display allocation again.
  Result coverage and valuation provenance are separate from amounts. Historical
  acquisition capital and return-opening market value are distinct. Summary-only
  and snapshot fallbacks remain explicit, and missing return evidence never
  shortens All. Historical renderers consume calculated gains/TWR in customdata;
  browser code formats them but does not reconstruct financial calculations.

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
  `eurex_quotes.py` isolates the anonymous public quote protocol and exact monthly
  contract validation. `futures_quotes.py` retains automatic observations and
  refresh status separately from manual marks and the journal;
  `futures_valuation.py` selects absolute prices and derives local freshness.
  Quote timestamps and retrieval timestamps must remain distinct. Settlement
  reference time orders valuations; it is not a fabricated quote/publication time.
  Daily cash eligibility uses Europe/Berlin business dates, independent of whether
  the archive changed since the last worker run.
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
Importer coverage uses independently authored positioned-cell and text-stream
PDF layouts in `tests/consors_audit_fixtures.py`, alongside the original fixtures.
Expected quantities, charges, FIFO costs and proceeds are explicit constants,
not calculated by parser helpers. Public format references inform labels and
layout only; all fixture identities, dates and financial values are invented.
Equivalent layout variants must preserve event economics, while unsupported
or ambiguous fields must remain blocked even when totals happen to balance.
The importer audit tests also cover retained evidence versus activation,
revision/duplicate guards, page/row provenance and masked-output sentinels.

They cover valuation, persistent cache fallback, parent/child conservation,
multi-path classifications, filtering, denominators, targets, ETF residuals,
company exposure merging, snapshot refresh failure recovery, position saves,
backups, stale-edit rejection, bulk purchase averaging and historical cost entry,
listing search, minimum-trade and minimum-cash rebalancing, trade-count tradeoffs,
empty-position redistribution, Git privacy guards, and Streamlit controls. The server health
endpoint is `/_stcore/health`.

Independent target overrides on analytical-taxonomy nodes, ordinary-security
tax reporting and automatic broker transaction imports remain
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
- Default benchmark is the direct MSCI ACWI net-return index (892400, NETR),
  requested in reporting currency and validated against returned metadata.
  Usable cached index levels precede a disclosed IUSQ.DE fallback. Custom tickers
  do not substitute automatically; neither index nor equity histories are stitched.
  Windows are 1, 3, or 5 years, default 3. All included holdings and the benchmark share the same
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
  prevent claiming whole-portfolio coverage. Integrated account leverage shows a
  labelled covered-holdings estimate with exclusions and fixed whole-portfolio
  net-equity weights; missing histories do not suppress otherwise usable metrics.
  Missing/nonpositive net equity or an insufficient common sample still prevents
  an estimate. Reviewed same-share equity history fallbacks are currency-converted
  independently and disclosed; valuation tickers and futures proxies stay separate.
- ETFs use their own adjusted price histories for risk, not constituent expansion.
  Category/account subsets show standalone beta and volatility on selected
  capital, with portfolio beta contribution secondary. Ordinary subsets normalize
  covered net funded values; derivative subsets require positive, fully valued
  selected backing capital. Count unsettled claims once before adding risk-only
  FX rows; never use margin or bare-contract gains as capital. A missing subset
  base does not suppress an otherwise supported portfolio beta contribution.
  The benchmark remains selected even for non-equity categories. Subset and
  contribution metrics use the same sample, which can differ across categories.
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

Background market, monitoring and settlement collection continues while a position
dialog is open. Defer automatic full-page refreshes until it closes; chart-history
completion renders inside its local fragment. User-requested saves still revalidate
revisions and refresh portfolio balances.

Settlement polling compares selected valuation inputs and journal changes rather
than cache modification times. Starting collection, downloading the same quote,
and crossing a wall-clock minute do not rerun the application. Source-check
notices update within Notifications. Freshness regions age the displayed snapshot
without rereading prices or recalculating holdings/history: metrics, position rows
and category-comparison qualification stay current while navigation and editors
remain mounted. The list component patches freshness metadata in place to retain
focus, sorting, scroll and expanded details. Material data changes still revalue
the portfolio together, deferred while a position dialog is open.

### Optional transaction history

`security_history.py` owns fractional Decimal holdings, FIFO cost components,
opening boundaries and retained evidence. `history_commands.py` coordinates
reviewed activation with workspace locks and revision guards. Activated holdings
are materialized before valuation; their CSV source balances are not independently
updated. Securities cash postings are generated into account replay and are never
stored as duplicate account events. Futures arithmetic remains in its existing
replay branches. Incomplete cash amounts carry an explicit unknown-balance mask.

FIFO lot partitions must conserve component shares, original costs and supplied
converted costs. Calculate the disposed portion once and derive its remainder;
do not independently scale both sides with a rounded ratio. Quantity validation
remains exact, including when a derived lot is reloaded at another Decimal precision.

`history_observations.py` stores dated observations and scope-specific coverage
reviews. `personal_performance.py` computes equity, external flows, money-weighted
returns and exact time-weighted returns without Streamlit or network calls.
`personal_market.py` supplies provider observations with explicit adjustment
semantics. `history_ui.py` renders the optional workflows. Keep source-position
history separate from present-day ETF constituent expansion and hypothetical risk
backtests. New evidence must never silently certify historical completeness.


## History playground

Create a separate, persistent example with invented holdings, buys, a sale, a
distribution, fees and cash contributions and withdrawals. `--market-prices`
downloads actual Yahoo Finance closing prices; omit it for deterministic invented
prices in offline tests. Invented trade dates use observed market sessions. This reads no user portfolio and refuses to overwrite a nonempty
directory:

```bash
uv run python -m portfolio_app.demo_history /tmp/portfolio-history-demo --market-prices
uv run streamlit run src/portfolio_app/ui.py --server.port=8527 -- --data-dir /tmp/portfolio-history-demo --demo --ignore-workspace-selection --skip-intro
```

This direct Streamlit launch uses the prepared history directory; the normal
`portfolio-app --demo` launcher creates its own fresh demonstration data.
Choose a free port and keep the playground separate from other sessions.
Restarting this command retains edits; generating another example requires a new,
empty directory.

Open **Overview → Performance** for total
value, opening capital plus net contributions, investment gain, annualized
money-weighted return across all recorded history. Positions with useful history
open directly on the **History** tab. The playground
opens portfolio **Performance** by default. Both views use periods ending today.
**Edit transaction** opens the individual transaction form.

The demo includes exact flow-boundary observations for the portfolio and the
fee-free money-market holding. The equity holding has a money-weighted return;
its exact time-weighted return remains unavailable because separate same-day fee
timing and flow-boundary values are not supplied. Editing financial history
invalidates the prior coverage confirmation, as it does for any workspace.

### Transaction CSV boundary

`transaction_csv.py` parses textual CSV facts and retains original evidence without
market access. `transaction_imports.py` prepares a pure batch reconstruction;
`transaction_import_commands.py` revalidates revisions under the workspace lock
and publishes all financial changes in one journal replacement. The interface
renders the same preview that the save command recomputes.

Journal version 3 retains import provenance and correction lineage. Versions 1
and 2 retain their semantics and remain readable; mutation must not downgrade a
version 3 journal. Position preparation writes only empty identity rows and is
separate from financial activation. Failed publication may leave those identities,
never partially applied swap or transfer legs. Derived cash postings remain
unpersisted, and custody identity is separate from cash ownership.

Security replay owns non-cash swaps, in-kind income and asset fees alongside
existing FIFO history. Lot splits multiply before dividing to avoid avoidable
intermediate rounding; imported lot ordering retains execution time and explicit
ties. Monetary gain components distinguish analytical swap proceeds from purchase
payments and sale cash receipts. Scope calculations cancel internal movements
before requesting cross-boundary monetary evidence.
