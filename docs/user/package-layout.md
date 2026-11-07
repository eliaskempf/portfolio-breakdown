# Package layout and transferring feature work

The application is one installable Python package. Subpackages organize existing
responsibilities; they do not define separate services, distributions or plugins.
Use explicit imports from implementation modules. Package initializers do not
load feature implementations, register providers or start background services.

## Ownership

| Package | Responsibility |
| --- | --- |
| `portfolio` | Holdings, positions, valuation, allocation, targets and rebalancing |
| `exposure` | Normalized exposures, taxonomy, aggregation and classifications |
| `funds` / `funds.providers` | ETF snapshots, expansion, discovery, refresh and isolated provider parsing |
| `ledger` | Account/security facts, replay, corrections and recoverable publication |
| `performance` | Cost basis, gains, historical evidence, coverage, TWR and IRR |
| `analytics` | Fundamentals, concentration, risk and associated service/cache ownership |
| `derivatives` | Actual contracts, observations, margin, settlements and valuation |
| `studies` | Hypothetical scenarios, historical reconstruction and the legacy futures sandbox |
| `market` | Prices, FX, market-price history, listing lookup and request coordination |
| `ingestion` | CSV/PDF readers, normalization, matching and import reconstruction |
| `monitoring` | Rules, observations, scheduling, presentation models and alert persistence |
| `workspace` | Shared storage mechanics, locking, backups, selection and activation |
| `runtime` | Launch, server/desktop lifetime, documentation serving and OS integration |
| `demo_data` | Explicitly synthetic portfolio and history builders |
| `interface.shared` | Reusable controls, charts, formatting, notifications and session state |
| `interface.views` | Feature views, grouped by the domain presented |

`application.py` remains the portfolio preparation/composition boundary.
`settings.py` owns shared identity, settings and package resource paths.
`privacy.py` remains independently executable by the Git hook. Resources remain
in `assets/` and `intro_frontend/` at the package root.

Market-price history is distinct from account/security replay and portfolio
performance history. Actual derivative wealth remains separate from hypothetical
study results. Shared file-writing mechanics belong to `workspace`; feature-specific
persistence and publication stay with their feature. Pure exposure and performance
presentation transformations stay beside their calculations. Moving modules does
not move calculations into Streamlit or change the documented financial pipeline.

The directory name `ingestion` intentionally avoids `imports`, which the privacy
checker reserves for private data. Keep that protection enabled.

## Supported entrypoints

The installed commands `portfolio-app`, `portfolio-window`, `portfolio-desktop`
and `portfolio-check-private` retain their names and arguments. Root modules
`app`, `window`, `gui`, `ui`, `intro_preview`, `demo_history`, `etf_sources` and
`vaneck` retain executable adapters for existing commands and script paths.
`portfolio_app.ui.render_app` also remains available.

Internal code and test patches must use the canonical implementation paths below.
Adapters are not copies of their implementations and do not alias old modules in
`sys.modules`. Patching an adapter's exported function does not patch globals used
inside the implementation. For example, patch
`portfolio_app.runtime.app.run_server`, not `portfolio_app.app.run_server`.

Use `settings.package_path(...)` for resources or executable scripts rooted in the
package; a moved module's `__file__` no longer identifies that root. Preserve
function-local imports that defer expensive work or break dependency cycles.

## Transferring active work from the flat layout

This procedure supports active feature work based on the pre-migration `main`.
It does not require reconstructing historical branches or changing another
session's worktree. Identify the feature owner and source state first; a dirty or
divergent worktree alone does not establish that its work is active or belongs to
the current task.

1. Record the original feature branch, commit, common base and source changes in
   external working notes. Review which changes belong to the feature.
2. Create a separate integration branch/worktree from the feature tip. Keep the
   original branch/worktree intact. For committed work, a typical sequence is:

   ```sh
   git worktree add -b integrate/my-feature ../my-feature-integration feature/my-feature
   cd ../my-feature-integration
   git merge --no-commit --no-ff main
   ```

   Here `main` must already contain the complete package migration. A pending
   migration branch may instead be used for a rehearsal; record its exact commit.
   Merge both histories. Do not cherry-pick the migration onto a stale base.

3. If the source has uncommitted feature work, copy and verify the deliberately
   selected tracked edits and required new source files into the integration
   worktree **before merging**. Preserve file additions, deletions and executable
   bits. Review and checkpoint that work there with the normal privacy checks.
   Do not copy private data, local configuration or unrelated changes. Do not
   stash, reset or clean the source worktree.
4. Let Git apply recognized rename/edit combinations. Inspect
   `git diff --name-status --find-renames` and `git status`. Resolve unrecognized
   moves using the table below; retain one canonical implementation. Do not
   resurrect the old file to silence an import error.
   **Handle retained adapters explicitly:** the eight adapter paths still exist,
   so Git may treat their old implementations as edits to those adapters rather
   than as moves. If the feature touched one of them, compare its original diff
   against the common base and port its implementation changes to the canonical
   destination. Then restore the thin adapter from migrated `main` in the
   integration worktree. Verify both files even if Git reported a clean merge;
   feature code must not accidentally remain in an adapter.
5. Reconcile new imports, package-member imports, mock/monkeypatch strings,
   generated Streamlit code, subprocess arguments and resource/script paths.
   New files have no rename history: place them according to the ownership table
   and update their callers. A genuine overlapping edit requires reviewing both
   changes, even when Git resolves the path automatically.
6. Inspect the combined diff against migrated `main`. Run affected feature tests,
   then the required integration checks from the development and release guides.
   Use the integration worktree's own uv environment, an independent preview port
   and generated synthetic data. Check staged privacy and contents before the
   integration commit. Do not retire the original worktree until the owner has
   confirmed the transferred result.

### Common adjustments

An edit to `holdings.py` belongs in `portfolio/holdings.py`; its import becomes:

```python
from portfolio_app.portfolio.holdings import DataError
```

A feature adding a new market provider places it under `market/` and updates its
callers even though Git has no old file to match. A generated Streamlit script
must use the same canonical imports as ordinary source. The stable root `ui.py`
script can still be used by existing launch commands.

An existing test patch such as `portfolio_app.prices.PriceService` becomes
`portfolio_app.market.prices.PriceService`. Continue patching the name where the
consumer looks it up; if a consumer imported the class into its own module,
patch that consumer's new module path instead.

If both histories alter the same import or function, combine the feature behavior
with the new canonical path. Never select one complete side merely to clear the
conflict. Use the existing feature tests to establish that its behavior survived.

## Complete flat-layout move map

Old paths are relative to `src/portfolio_app/`; destinations are relative to that
same directory. Filenames and function/class names are retained. An adapter means
the old executable path is intentionally retained, while code lives at the listed
destination. Unchanged root modules are included so every original module is
accounted for. New feature modules should follow the ownership rules above rather
than extend this historical map.

| Old module | Canonical implementation | Old path retained |
| --- | --- | --- |
| `__init__.py` | `__init__.py` | Unchanged |
| `account_activity.py` | `ledger/account_activity.py` | No |
| `account_activity_ui.py` | `interface/views/history/account_activity_ui.py` | No |
| `account_inception.py` | `ledger/account_inception.py` | No |
| `account_journal.py` | `ledger/account_journal.py` | No |
| `account_margin.py` | `derivatives/account_margin.py` | No |
| `account_margin_ui.py` | `interface/views/derivatives/account_margin_ui.py` | No |
| `account_observations.py` | `ledger/account_observations.py` | No |
| `account_planning.py` | `derivatives/account_planning.py` | No |
| `account_timeline.py` | `ledger/account_timeline.py` | No |
| `accounts_ui.py` | `interface/views/derivatives/accounts_ui.py` | No |
| `actual_portfolio.py` | `portfolio/actual_portfolio.py` | No |
| `aggregation.py` | `exposure/aggregation.py` | No |
| `allocation.py` | `portfolio/allocation.py` | No |
| `allocation_ui.py` | `interface/views/portfolio/allocation_ui.py` | No |
| `amundi.py` | `funds/providers/amundi.py` | No |
| `analytics.py` | `analytics/analytics.py` | No |
| `analytics_cache.py` | `analytics/analytics_cache.py` | No |
| `analytics_service.py` | `analytics/analytics_service.py` | No |
| `analytics_ui.py` | `interface/views/analytics/analytics_ui.py` | No |
| `app.py` | `runtime/app.py` | Adapter |
| `application.py` | `application.py` | Unchanged |
| `atomic_file.py` | `workspace/atomic_file.py` | No |
| `automatic_settlements.py` | `derivatives/automatic_settlements.py` | No |
| `background_ui.py` | `interface/shared/background_ui.py` | No |
| `backup.py` | `workspace/backup.py` | No |
| `backup_ui.py` | `interface/views/workspace/backup_ui.py` | No |
| `balances.py` | `portfolio/balances.py` | No |
| `calendar_ui.py` | `interface/shared/calendar_ui.py` | No |
| `capped_contributions.py` | `portfolio/capped_contributions.py` | No |
| `category_setup_ui.py` | `interface/views/onboarding/category_setup_ui.py` | No |
| `chart_hover.py` | `interface/shared/chart_hover.py` | No |
| `chart_inspection.py` | `interface/shared/chart_inspection.py` | No |
| `chart_navigation.py` | `interface/shared/chart_navigation.py` | No |
| `charts.py` | `interface/shared/charts.py` | No |
| `cocoa_diagnostics.py` | `runtime/cocoa_diagnostics.py` | No |
| `company_analysis.py` | `exposure/company_analysis.py` | No |
| `company_merge_ui.py` | `interface/views/exposure/company_merge_ui.py` | No |
| `company_merges.py` | `exposure/company_merges.py` | No |
| `consors_pdf.py` | `ingestion/consors_pdf.py` | No |
| `constituents.py` | `funds/constituents.py` | No |
| `contract_maturity_ui.py` | `interface/views/derivatives/contract_maturity_ui.py` | No |
| `contributions.py` | `portfolio/contributions.py` | No |
| `cost_basis.py` | `performance/cost_basis.py` | No |
| `costs.py` | `portfolio/costs.py` | No |
| `countries.py` | `exposure/countries.py` | No |
| `csv_import_batch.py` | `ingestion/csv_import_batch.py` | No |
| `csv_transaction_import_ui.py` | `interface/views/ingestion/csv_transaction_import_ui.py` | No |
| `csv_transaction_imports.py` | `ingestion/csv_transaction_imports.py` | No |
| `currencies.py` | `market/currencies.py` | No |
| `currency_display.py` | `interface/shared/currency_display.py` | No |
| `currency_ui.py` | `interface/views/workspace/currency_ui.py` | No |
| `demo.py` | `demo_data/demo.py` | No |
| `demo_history.py` | `demo_data/demo_history.py` | Adapter |
| `derivative_market.py` | `derivatives/derivative_market.py` | No |
| `derivative_types.py` | `derivatives/derivative_types.py` | No |
| `desktop.py` | `runtime/desktop.py` | No |
| `desktop_controls.py` | `runtime/desktop_controls.py` | No |
| `desktop_probe.py` | `runtime/desktop_probe.py` | No |
| `dialog_runtime.py` | `interface/shared/dialog_runtime.py` | No |
| `display_names.py` | `interface/shared/display_names.py` | No |
| `display_preferences.py` | `workspace/display_preferences.py` | No |
| `documentation.py` | `runtime/documentation.py` | No |
| `dws.py` | `funds/providers/dws.py` | No |
| `etf.py` | `funds/etf.py` | No |
| `etf_discovery.py` | `funds/etf_discovery.py` | No |
| `etf_refresh.py` | `funds/etf_refresh.py` | No |
| `etf_refresh_ui.py` | `interface/views/funds/etf_refresh_ui.py` | No |
| `etf_selection.py` | `interface/views/funds/etf_selection.py` | No |
| `etf_setup.py` | `funds/etf_setup.py` | No |
| `etf_setup_ui.py` | `interface/views/funds/etf_setup_ui.py` | No |
| `etf_sources.py` | `funds/etf_sources.py` | Adapter |
| `etf_ui.py` | `interface/views/funds/etf_ui.py` | No |
| `eurex_quotes.py` | `derivatives/eurex_quotes.py` | No |
| `exposure_analysis.py` | `exposure/exposure_analysis.py` | No |
| `exposure_assets_ui.py` | `interface/views/exposure/exposure_assets_ui.py` | No |
| `exposure_plan_ui.py` | `interface/views/exposure/exposure_plan_ui.py` | No |
| `exposure_planning.py` | `exposure/exposure_planning.py` | No |
| `exposure_presentation.py` | `exposure/exposure_presentation.py` | No |
| `exposure_tables.py` | `exposure/exposure_tables.py` | No |
| `exposure_target_presentation.py` | `exposure/exposure_target_presentation.py` | No |
| `exposure_targets.py` | `exposure/exposure_targets.py` | No |
| `exposure_ui.py` | `interface/views/exposure/exposure_ui.py` | No |
| `exposure_workspace.py` | `exposure/exposure_workspace.py` | No |
| `exposures.py` | `exposure/exposures.py` | No |
| `filtering.py` | `portfolio/filtering.py` | No |
| `freshness_ui.py` | `interface/shared/freshness_ui.py` | No |
| `freshness_view.py` | `interface/shared/freshness_view.py` | No |
| `fund_summary.py` | `funds/fund_summary.py` | No |
| `fundamentals.py` | `analytics/fundamentals.py` | No |
| `future_entry_help.py` | `interface/views/derivatives/future_entry_help.py` | No |
| `future_projections.py` | `studies/future_projections.py` | No |
| `futures.py` | `studies/futures.py` | No |
| `futures_assumptions_ui.py` | `interface/views/studies/futures_assumptions_ui.py` | No |
| `futures_chart_reference.py` | `derivatives/futures_chart_reference.py` | No |
| `futures_chart_ui.py` | `interface/views/derivatives/futures_chart_ui.py` | No |
| `futures_clock.py` | `derivatives/futures_clock.py` | No |
| `futures_comparison.py` | `studies/futures_comparison.py` | No |
| `futures_comparison_ui.py` | `interface/views/studies/futures_comparison_ui.py` | No |
| `futures_financing.py` | `studies/futures_financing.py` | No |
| `futures_funding.py` | `studies/futures_funding.py` | No |
| `futures_funding_data.py` | `studies/futures_funding_data.py` | No |
| `futures_funding_study.py` | `studies/futures_funding_study.py` | No |
| `futures_funding_ui.py` | `interface/views/studies/futures_funding_ui.py` | No |
| `futures_history.py` | `studies/futures_history.py` | No |
| `futures_history_data.py` | `studies/futures_history_data.py` | No |
| `futures_history_ui.py` | `interface/views/studies/futures_history_ui.py` | No |
| `futures_investments.py` | `derivatives/futures_investments.py` | No |
| `futures_market.py` | `studies/futures_market.py` | No |
| `futures_market_ui.py` | `interface/views/studies/futures_market_ui.py` | No |
| `futures_mtm.py` | `derivatives/futures_mtm.py` | No |
| `futures_mtm_ui.py` | `interface/views/derivatives/futures_mtm_ui.py` | No |
| `futures_position_ui.py` | `interface/views/studies/futures_position_ui.py` | No |
| `futures_presentation.py` | `derivatives/futures_presentation.py` | No |
| `futures_pricing.py` | `studies/futures_pricing.py` | No |
| `futures_proxy.py` | `studies/futures_proxy.py` | No |
| `futures_quotes.py` | `derivatives/futures_quotes.py` | No |
| `futures_reference.py` | `studies/futures_reference.py` | No |
| `futures_returns.py` | `studies/futures_returns.py` | No |
| `futures_rolling.py` | `studies/futures_rolling.py` | No |
| `futures_sessions.py` | `derivatives/futures_sessions.py` | No |
| `futures_ui.py` | `interface/views/studies/futures_ui.py` | No |
| `futures_valuation.py` | `derivatives/futures_valuation.py` | No |
| `futures_workspace.py` | `studies/futures_workspace.py` | No |
| `fx.py` | `market/fx.py` | No |
| `gain_accounting.py` | `performance/gain_accounting.py` | No |
| `gain_comparison.py` | `performance/gain_comparison.py` | No |
| `gain_components.py` | `performance/gain_components.py` | No |
| `generalized_studies.py` | `studies/generalized_studies.py` | No |
| `generalized_studies_ui.py` | `interface/views/studies/generalized_studies_ui.py` | No |
| `geography.py` | `exposure/geography.py` | No |
| `geography_ui.py` | `interface/views/exposure/geography_ui.py` | No |
| `gold_prices.py` | `market/gold_prices.py` | No |
| `grid_interactions.py` | `interface/shared/grid_interactions.py` | No |
| `group_ui.py` | `interface/views/portfolio/group_ui.py` | No |
| `grouping.py` | `portfolio/grouping.py` | No |
| `gui.py` | `runtime/gui.py` | Adapter |
| `history.py` | `market/history.py` | No |
| `history_commands.py` | `ledger/history_commands.py` | No |
| `history_data_ui.py` | `interface/views/history/history_data_ui.py` | No |
| `history_diagnostics.py` | `performance/history_diagnostics.py` | No |
| `history_flows.py` | `performance/history_flows.py` | No |
| `history_irr.py` | `performance/history_irr.py` | No |
| `history_observations.py` | `performance/history_observations.py` | No |
| `history_order.py` | `ledger/history_order.py` | No |
| `history_publication.py` | `ledger/history_publication.py` | No |
| `history_review_ui.py` | `interface/views/history/history_review_ui.py` | No |
| `history_scope.py` | `performance/history_scope.py` | No |
| `history_status.py` | `performance/history_status.py` | No |
| `history_status_ui.py` | `interface/views/history/history_status_ui.py` | No |
| `history_twr.py` | `performance/history_twr.py` | No |
| `history_ui.py` | `interface/views/history/history_ui.py` | No |
| `history_valuation.py` | `performance/history_valuation.py` | No |
| `history_view.py` | `performance/history_view.py` | No |
| `holdings.py` | `portfolio/holdings.py` | No |
| `import_readers.py` | `ingestion/import_readers.py` | No |
| `import_state.py` | `interface/shared/import_state.py` | No |
| `import_ui.py` | `interface/views/ingestion/import_ui.py` | No |
| `imports.py` | `ingestion/imports.py` | No |
| `input_cache.py` | `interface/shared/input_cache.py` | No |
| `instrument_ui.py` | `interface/shared/instrument_ui.py` | No |
| `instruments.py` | `market/instruments.py` | No |
| `intro.py` | `interface/views/onboarding/intro.py` | No |
| `intro_preview.py` | `interface/views/onboarding/intro_preview.py` | Adapter |
| `investment_detail_ui.py` | `interface/views/derivatives/investment_detail_ui.py` | No |
| `investment_history.py` | `derivatives/investment_history.py` | No |
| `investment_presentation.py` | `derivatives/investment_presentation.py` | No |
| `investment_ui.py` | `interface/views/derivatives/investment_ui.py` | No |
| `ishares.py` | `funds/providers/ishares.py` | No |
| `label_comparison.py` | `exposure/label_comparison.py` | No |
| `label_presentation.py` | `exposure/label_presentation.py` | No |
| `label_ui.py` | `interface/views/exposure/label_ui.py` | No |
| `launcher.py` | `runtime/launcher.py` | No |
| `leverage_settings.py` | `funds/leverage_settings.py` | No |
| `leveraged_funds_ui.py` | `interface/views/funds/leveraged_funds_ui.py` | No |
| `liabilities_ui.py` | `interface/views/studies/liabilities_ui.py` | No |
| `list_ui.py` | `interface/shared/list_ui.py` | No |
| `listing_picker.py` | `interface/shared/listing_picker.py` | No |
| `listing_quality.py` | `market/listing_quality.py` | No |
| `locking.py` | `workspace/locking.py` | No |
| `market_data.py` | `market/market_data.py` | No |
| `market_history_ui.py` | `interface/views/history/market_history_ui.py` | No |
| `metric_interactions.py` | `interface/shared/metric_interactions.py` | No |
| `money.py` | `interface/shared/money.py` | No |
| `monitoring.py` | `monitoring/monitoring.py` | No |
| `monitoring_catalog.py` | `monitoring/monitoring_catalog.py` | No |
| `monitoring_market.py` | `monitoring/monitoring_market.py` | No |
| `monitoring_presentation.py` | `monitoring/monitoring_presentation.py` | No |
| `monitoring_service.py` | `monitoring/monitoring_service.py` | No |
| `monitoring_store.py` | `monitoring/monitoring_store.py` | No |
| `monitoring_ui.py` | `interface/views/monitoring/monitoring_ui.py` | No |
| `msci_history.py` | `market/msci_history.py` | No |
| `notification_model.py` | `interface/shared/notification_model.py` | No |
| `notifications_ui.py` | `interface/shared/notifications_ui.py` | No |
| `onboarding.py` | `interface/views/onboarding/onboarding.py` | No |
| `onboarding_ui.py` | `interface/views/onboarding/onboarding_ui.py` | No |
| `performance.py` | `performance/performance.py` | No |
| `performance_allocation.py` | `performance/performance_allocation.py` | No |
| `performance_ui.py` | `interface/views/portfolio/performance_ui.py` | No |
| `personal_history_refresh.py` | `performance/personal_history_refresh.py` | No |
| `personal_market.py` | `performance/personal_market.py` | No |
| `personal_performance.py` | `performance/personal_performance.py` | No |
| `physical_assets.py` | `portfolio/physical_assets.py` | No |
| `planning_ui.py` | `interface/shared/planning_ui.py` | No |
| `portfolio.py` | `portfolio/portfolio.py` | No |
| `portfolio_analytics_ui.py` | `interface/views/analytics/portfolio_analytics_ui.py` | No |
| `portfolio_exposure.py` | `exposure/portfolio_exposure.py` | No |
| `portfolio_exposure_ui.py` | `interface/views/exposure/portfolio_exposure_ui.py` | No |
| `portfolio_settings.py` | `portfolio/portfolio_settings.py` | No |
| `position_detail.py` | `interface/views/positions/position_detail.py` | No |
| `position_entry.py` | `portfolio/position_entry.py` | No |
| `position_entry_ui.py` | `interface/views/positions/position_entry_ui.py` | No |
| `position_list.py` | `interface/views/positions/position_list.py` | No |
| `position_metrics_ui.py` | `interface/views/analytics/position_metrics_ui.py` | No |
| `position_summary.py` | `performance/position_summary.py` | No |
| `position_ui.py` | `interface/views/positions/position_ui.py` | No |
| `positions.py` | `portfolio/positions.py` | No |
| `posix_window.py` | `runtime/posix_window.py` | No |
| `posix_window_process.py` | `runtime/posix_window_process.py` | No |
| `presentation.py` | `interface/shared/presentation.py` | No |
| `prices.py` | `market/prices.py` | No |
| `privacy.py` | `privacy.py` | Unchanged |
| `provider_data.py` | `funds/providers/provider_data.py` | No |
| `purchase_ui.py` | `interface/views/positions/purchase_ui.py` | No |
| `purchases.py` | `portfolio/purchases.py` | No |
| `rebalance_results_ui.py` | `interface/views/portfolio/rebalance_results_ui.py` | No |
| `rebalance_tables.py` | `portfolio/rebalance_tables.py` | No |
| `rebalance_ui.py` | `interface/views/portfolio/rebalance_ui.py` | No |
| `rebalancing.py` | `portfolio/rebalancing.py` | No |
| `risk.py` | `analytics/risk.py` | No |
| `risk_data.py` | `analytics/risk_data.py` | No |
| `scoped_rebalancing.py` | `portfolio/scoped_rebalancing.py` | No |
| `scoped_ui.py` | `interface/views/portfolio/scoped_ui.py` | No |
| `search_widget.py` | `interface/shared/search_widget.py` | No |
| `sector_classification.py` | `exposure/sector_classification.py` | No |
| `security_history.py` | `ledger/security_history.py` | No |
| `settings.py` | `settings.py` | Unchanged |
| `settlement_archive.py` | `derivatives/settlement_archive.py` | No |
| `settlement_archive_ui.py` | `interface/views/derivatives/settlement_archive_ui.py` | No |
| `settlement_backfill.py` | `derivatives/settlement_backfill.py` | No |
| `settlement_backfill_ui.py` | `interface/views/derivatives/settlement_backfill_ui.py` | No |
| `settlement_collection.py` | `derivatives/settlement_collection.py` | No |
| `spdr.py` | `funds/providers/spdr.py` | No |
| `state_lifecycle.py` | `interface/shared/state_lifecycle.py` | No |
| `statement_diagnostics.py` | `ingestion/statement_diagnostics.py` | No |
| `stock_exposure.py` | `exposure/stock_exposure.py` | No |
| `stock_ui.py` | `interface/views/exposure/stock_ui.py` | No |
| `storage.py` | `workspace/storage.py` | No |
| `strategic.py` | `portfolio/strategic.py` | No |
| `strategic_ui.py` | `interface/views/portfolio/strategic_ui.py` | No |
| `study_market.py` | `studies/study_market.py` | No |
| `target_ui.py` | `interface/shared/target_ui.py` | No |
| `targets.py` | `portfolio/targets.py` | No |
| `taxonomy.py` | `exposure/taxonomy.py` | No |
| `tour.py` | `interface/views/onboarding/tour.py` | No |
| `tour_monitoring.py` | `interface/views/onboarding/tour_monitoring.py` | No |
| `tour_spotlight.py` | `interface/views/onboarding/tour_spotlight.py` | No |
| `tour_state.py` | `interface/views/onboarding/tour_state.py` | No |
| `tour_steps.py` | `interface/views/onboarding/tour_steps.py` | No |
| `tour_style.py` | `interface/views/onboarding/tour_style.py` | No |
| `transaction_csv.py` | `ingestion/transaction_csv.py` | No |
| `transaction_evidence.py` | `ledger/transaction_evidence.py` | No |
| `transaction_fields_ui.py` | `interface/views/history/transaction_fields_ui.py` | No |
| `transaction_import_commands.py` | `ledger/transaction_import_commands.py` | No |
| `transaction_import_ui.py` | `interface/views/ingestion/transaction_import_ui.py` | No |
| `transaction_imports.py` | `ingestion/transaction_imports.py` | No |
| `transaction_list_ui.py` | `interface/views/history/transaction_list_ui.py` | No |
| `transaction_mapping_ui.py` | `interface/views/ingestion/transaction_mapping_ui.py` | No |
| `transaction_matching.py` | `ingestion/transaction_matching.py` | No |
| `transaction_review_ui.py` | `interface/views/ingestion/transaction_review_ui.py` | No |
| `ui.py` | `interface/ui.py` | Adapter |
| `ui_help.py` | `interface/shared/ui_help.py` | No |
| `valuation.py` | `portfolio/valuation.py` | No |
| `vaneck.py` | `funds/providers/vaneck.py` | Adapter |
| `vanguard.py` | `funds/providers/vanguard.py` | No |
| `view_state.py` | `interface/shared/view_state.py` | No |
| `window.py` | `runtime/window.py` | Adapter |
| `window_process.py` | `runtime/window_process.py` | No |
| `window_splash.py` | `runtime/window_splash.py` | No |
| `workspace.py` | `workspace/workspace.py` | No |
| `workspace_activation.py` | `workspace/workspace_activation.py` | No |
| `workspace_lock.py` | `workspace/workspace_lock.py` | No |
| `workspace_selection.py` | `workspace/workspace_selection.py` | No |
| `workspace_ui.py` | `interface/views/workspace/workspace_ui.py` | No |
