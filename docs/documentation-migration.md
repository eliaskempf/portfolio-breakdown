# In-app prose migration inventory

Reviewed against source commit `9f1877d4e2a3222529f979782e0e90c6ee2134dc`.
This is a migration proposal, not an instruction to remove all captions. No app
prose is removed on this branch. Integration owns help controls and text edits.
Source locations use module/function names so line shifts do not invalidate them;
all modules below are under `src/portfolio_app/`. Destinations are relative to the
versioned documentation root. Short contextual qualifications must stay beside
the result even when a longer explanation moves to help.

| Source location / substantial explanation | Guide / anchor | Keep in context |
| --- | --- | --- |
| onboarding_ui.py: render_welcome, demo_guide — first-use choices and four-step tour | getting-started/#first-portfolio; #demo | Active demo vs persistent workspace; invented costs; reset on restart; live vs offline data status |
| workspace_ui.py: workspace_info — background lifetime and backup advice | install/#launch-stop; storage/#backup | Version, active folder, stop action, stopping result; closing tab does not stop app |
| instrument_ui.py: render_instrument_search — search/provider privacy | positions/#edit; storage/#privacy | Search failure/fallback, listing exchange/ISIN/currency, offline status |
| position_ui.py: position form and save area — persistent CSV, identity and manual pricing explanation | positions/#edit; #buy-ins | Quantity units, cost/quote currencies, manual-price override/date, zero vs blank, rename scope, stale-form errors and save/delete confirmation |
| purchase_ui.py: purchase entry, preview, saved batches — additive vs historical entry and weighted cost | positions/#purchases | Increases shares vs cost-only, batch totals, currency/unit requirements, unknown cost, duplicate acknowledgment, no complete ledger |
| allocation_ui.py: render_balances — replacing balance summaries | positions/#balances | Replace, not add; quantity confirmation date vs quote date; cost currency and save confirmation |
| allocation_ui.py: render_allocation_editor, _bucket_editor — migration and target maintenance | allocation/#targets | Parent/category denominators; blank unknown vs zero; sibling totals; reviewed migration confirmation and category deletion constraints |
| allocation_ui.py: render_bulk_bucket_assignment — move semantics | allocation/#targets | Within-category targets retained; review new sibling total; save action |
| strategic_ui.py and performance_ui.py — Overview navigation and performance coverage | allocation/#overview; performance/#performance | Selected scope, denominators, partial/unknown values, missing EUR cost and target gap signs |
| import_ui.py: render_import, Export and privacy help — report types, mappings and local processing | import/#review; storage/#privacy | Experimental/provisional status, empty-portfolio requirement, quantity convention, dated manual price, required mappings, row exclusions, warnings and accept/cancel actions |
| import_ui.py: listing setup — linking vs switching pricing | import/#live-listings | Verified identity; changes across all accounts; explicit switch clears manual prices; missing live quote not a zero |
| exposure_ui.py: filters/chart controls — pre-expansion scope and search | exposure/#filters; allocation/#classifications | Scope/denominator, missing valuations, unsupported funds whole, parent includes descendants |
| label_ui.py: render_label_comparison — overlapping membership and chart choice | exposure/#labels | Explicit overlap choice; over-100% possibility; unmatched value/coverage; detail denominator |
| geography_ui.py — company-country coverage and region breakdowns | exposure/#filters | Country is not revenue; unknown/partial geography and missing-value denominator |
| stock_ui.py — stock universe and exclusions | exposure/#filters | Excluded non-equity sources, residual unresolved coverage, selected-stock vs portfolio denominator |
| etf_ui.py: render_fund_details — constituents, dates, proxies and Other | exposure/#look-through; #other; #sources | Fund ISIN, provider date, age, coverage, source link, proxy label and Other not scaled |
| etf_ui.py: render_fund_summary — bonds and overnight-rate representation | exposure/#bonds | Economic allocation vs signed substitute basket, dated provider aggregates, unknown metadata, denomination not hedged FX risk |
| etf_setup_ui.py: render_setup — official source/upload schema | exposure/#sources | Exact identity, physical-only scope, weight units, date/coverage, source interpretation, manual refresh disabled, save confirmation |
| etf_refresh_ui.py: render_refresh_controls — background update cadence | exposure/#other | Attempt vs successful check vs holdings date, stale/failure state, saved data retained, explicit refresh action |
| company_merge_ui.py: render_company_merges — estimated/reviewed merge behavior | exposure/#merges | `*` estimate marker, match basis, original identities, undo/restore status |
| group_ui.py — SMH display group and selected members | exposure/#merges | Whole-fund display grouping, selected sources, unchanged saved positions and scope |
| position_detail.py — market history and performance reasons | performance/#history; #performance | Chart currency, excludes dividend reinvestment, not personal return history, retrieval date, stale/unavailable status |
| analytics_ui.py: risk_settings/render_sources, portfolio_analytics_ui.py — risk and aggregate metrics | analytics/#risk; #metrics | Benchmark/window, EUR weekly returns, common observation count, exclusions, coverage, not personal historical returns |
| position_metrics_ui.py and fundamentals.py: DEFINITIONS — metrics and fee maintenance | analytics/#metrics; #sources | Units, nonpositive P/E not meaningful, source/period, stale/fallback labels, fee verification and exact share class |
| rebalance_ui.py/scoped_ui.py/planning_ui.py — planning help, distributions, constraints | rebalance/#modes; #constraints | Read-only/no orders, scope, fractional quantities, excluded costs, eligibility, cap denominator, percentage points vs percent |
| rebalance_results_ui.py/rebalance_tables.py — suggested trades, budgets and final weights | rebalance/#results | Unallocated included in denominator, no saved cash holding, reserved vs invested budget, infeasible/no proven optimum |
| target_ui.py: target_caption/target_column_config — target/current comparisons | allocation/#targets; performance/#valuation | Missing-price suppression and current-minus-target sign/units |

Validation errors, destructive-action confirmations, unknown/stale/partial-data
labels, units and result qualifications are not candidates for wholesale removal.
A question-mark link can replace a tutorial paragraph; it cannot replace the
information needed to interpret the number currently on screen. Check actual
source after integration because shared controls may centralize several rows.

## Integration reconciliation checklist

- Welcome is currently a three-choice view. Reconcile the guide against the planned
  two-choice dialog (**Explore demo**, **Start my portfolio**); keep import in
  Positions and do not document an import welcome card as the final UI.
- Confirm the compact header portfolio dropdown replaces the sidebar. The guide
  uses “portfolio/workspace selector” and task names to avoid promising placement.
- Add upper-right question-mark help using the stable topic map in
  documentation-maintenance.md; retain contextual qualifiers above.
- Reconcile final Position tools, ETF controls, Targets and stop/open-folder labels
  against the integrated candidate. The source guide currently describes base
  commit destinations, not future controls.
- Do not describe a dedicated desktop window, new console-free behavior or animation
  flow until the window session's implementation and acceptance are available.
- Update README's usage sections to link to version-matched docs, retaining concise
  source development/architecture instructions. Migrate duplicated user prose only
  after reviewing the destination guides; preserve maintainer calculation contracts.
- Correct docs/install.md: normal `--demo` uses public quotes/history/issuer data;
  only `--demo --offline-demo` is synthetic offline. Its offline-first welcome and
  partial-snapshot wording is stale. README also has stale unconditional claims
  that demo history/analytics never use live data. Align these after integration.
- Integrate docs dependency group/lock additions with the window session's changes.
  Keep MkDocs outside application runtime/package dependencies.
- Connect candidate tooling to one exact source SHA and build-info checksum; archive
  candidate docs beside binaries. Freeze the app help route in candidate metadata.
  Promote the approved docs bytes with the approved binary bytes. No release
  tooling was changed here; this hook remains an integration prerequisite.
- The source-distribution include list currently omits root mkdocs.yml. Add that
  one file when integration decides whether source release docs must build from
  an extracted sdist. Until then build docs from a Git checkout, as documented.
- Decide the repository's real Pages URL, protected deployment environment and
  durable archive retention before enabling manual publication. Never redirect
  released help silently to dev. No settings, tag, release or deployment was made.

## Remaining factual boundaries

FinanzManager export variants are unverified; report recognition stays provisional.
Provider coverage can change and is deliberately described as bounded support,
not a promise of universal downloads. Hosted OS acceptance and final startup/UI
wording belong to the integrated candidate. Locally verified docs correspond to
this branch's source, not to a separately downloaded executable. Technical build
validation does not replace that final human walkthrough.
