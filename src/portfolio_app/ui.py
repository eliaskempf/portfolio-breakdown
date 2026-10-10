"""Streamlit controls and presentation; calculations live in pure modules."""

import argparse
from hashlib import sha256
from pathlib import Path

import streamlit as st

from portfolio_app.demo import initialize_live_demo, live_demo_pending
from portfolio_app.etf import validate_fund_listings
from portfolio_app.exposure_ui import render_analysis
from portfolio_app.etf_refresh import coordinator
from portfolio_app.etf_refresh_ui import refresh_revision, render_refresh_status
from portfolio_app.holdings import DataError
from portfolio_app.position_ui import render_position_editor, render_position_dialog, request_position
from portfolio_app.positions import read_snapshot
from portfolio_app.portfolio import prepare_portfolio
from portfolio_app.rebalancing import RebalanceError
from portfolio_app.rebalance_ui import render_rebalancing
from portfolio_app.prices import PriceService, StaticProvider, UnavailableProvider
from portfolio_app.presentation import apply_style, empty_overview, mark_view_ready
from portfolio_app.allocation import analysis_targets, migration_preview
from portfolio_app.strategic_ui import render_strategic_overview
from portfolio_app.scoped_ui import render_scoped_rebalancing
from portfolio_app.input_cache import load_inputs
from portfolio_app.market_data import coordinator as market_coordinator, prices_for
from portfolio_app.view_state import preserve_view_inputs
from portfolio_app.futures_workspace import load_sandbox


def render_app(data_dir: Path, *, demo: bool = False, demo_dir: Path | None = None, price_service: PriceService | None = None, intro: bool = False, ignore_selection: bool = False) -> None:
    from portfolio_app.settings import icon_path
    from portfolio_app.workspace_ui import app_header, workspace_info
    persistent_data_dir = data_dir
    from portfolio_app.workspace_selection import selection, Selection
    from portfolio_app.workspace_lock import write_context, bind_workspace
    write_context.set(None)
    try:
        chosen = Selection(data_dir.resolve()) if ignore_selection else selection(data_dir)
    except DataError as exc:
        st.error(str(exc))
        return
    context = (str(persistent_data_dir.resolve()), chosen)
    if st.session_state.get('active_workspace_selection', context) != context:
        from portfolio_app.view_state import reset_workspace
        from portfolio_app.backup_ui import clear_backup
        clear_backup()
        reset_workspace()
        st.session_state['active_workspace_selection'] = context
        st.rerun()
    st.session_state['active_workspace_selection'] = context
    data_dir = chosen.directory
    icon = icon_path('favicon.svg')
    st.set_page_config(page_title="Portfolio breakdown", layout="wide", page_icon=str(icon) if icon else None)
    apply_style()
    if intro:
        from portfolio_app.intro import render_startup_intro
        if not render_startup_intro():
            return
    preserve_view_inputs()
    data_dir, demo, refresh, settings_panel = app_header(data_dir, demo_dir, demo=demo)
    from portfolio_app.backup_ui import backup_controls, render_backup_dialog
    if not demo and not ignore_selection:
        bind_workspace(persistent_data_dir, chosen)
    if settings_panel is not None:
        with settings_panel:
            backup_controls(demo=demo, recovery=ignore_selection)
    if not demo and render_backup_dialog(data_dir, persistent_data_dir, chosen.generation):
        mark_view_ready()
        return
    from portfolio_app.tour import active as tour_active
    if tour_active():
        write_context.set(None)
        # Always value the isolated tour with its own synthetic quotes.
        price_service = PriceService(StaticProvider(data_dir / 'demo_prices.json'))
    from portfolio_app.portfolio_settings import load_settings
    from portfolio_app.currency_ui import render_currency_setting, render_currency_dialog, historical_for
    from portfolio_app.currency_display import currency_symbol
    try:
        currency_settings = load_settings(data_dir)
    except DataError as exc:
        if settings_panel is not None:
            with settings_panel:
                workspace_info(data_dir, persistent_data_dir, demo=demo)
        st.error(str(exc))
        mark_view_ready()
        return
    currency_context = (str(data_dir.resolve()), currency_settings.revision)
    initial_setup = st.session_state.get('onboarding_step') == 'position' and st.session_state.get('currency_context', (None, None))[1] is None
    if st.session_state.get('currency_context', currency_context) != currency_context:
        # Setup can save a currency after Settings already mounted its selector.
        # Recreate that widget from the saved preference on the next render.
        st.session_state.pop('currency_setting_choice', None)
        if not initial_setup:
            from portfolio_app.currency_ui import reset_currency_views
            reset_currency_views()
    st.session_state['currency_context'] = currency_context
    reporting_currency = currency_settings.reporting_currency
    st.session_state['reporting_currency'] = reporting_currency
    historical = historical_for(data_dir, demo=demo and not (data_dir / '.live-demo').exists())
    display_settings = None
    if settings_panel is not None:
        with settings_panel:
            render_currency_setting(data_dir, currency_settings)
            display_settings = st.container()
            # Recovery controls must also be available when loading inputs fails.
            workspace_info(data_dir, persistent_data_dir, demo=demo)
    offline_demo = demo and not (data_dir / '.live-demo').exists()
    demo_pending = demo and live_demo_pending(data_dir)
    from portfolio_app.import_ui import render_import_next_steps
    render_import_next_steps()
    if demo and not demo_pending:
        st.caption("Offline demo · Invented prices, buy-ins and ETF weights · Resets on restart" if offline_demo else
                   "Demo · Invented quantities, targets and buy-ins · Public market data · Resets on restart")
    from portfolio_app.leverage_settings import load_leverage_settings, requires_leverage
    from portfolio_app.portfolio_exposure import calculate_portfolio_exposure
    from portfolio_app.portfolio_exposure_ui import render_market_analysis, render_exposure_targets, render_leverage_editor
    try:
        sandbox = load_sandbox(data_dir)
        leverage, _ = load_leverage_settings(data_dir, future=sandbox[0] if sandbox else None)
    except DataError as exc:
        st.error(str(exc))
        mark_view_ready()
        return
    leverage_supported = reporting_currency == 'EUR'
    feature_active = leverage_supported and (leverage.enabled or sandbox is not None)
    unsupported_records = not leverage_supported and (leverage.enabled or requires_leverage(leverage, sandbox[0] if sandbox else None))
    if unsupported_records:
        st.warning('Saved leverage records are in EUR. Switch reporting currency to EUR to see complete portfolio totals, exposure, risk and plans. Your saved records are unchanged.')
        from dataclasses import asdict
        with st.expander('Saved EUR leverage records'):
            if sandbox:
                st.json(asdict(sandbox[0]))
            st.json(asdict(leverage))
    etf_revision = refresh_revision(data_dir)
    try:
        snapshot, allocation, classifications, funds = load_inputs(data_dir)
        holdings = snapshot.holdings
        validate_fund_listings(holdings, funds)
    except DataError as exc:
        st.error(str(exc))
        st.info("Edit holdings.csv and classifications.yaml in the data directory, then rerun the app.")
        mark_view_ready()
        return
    coordinator.schedule(data_dir, holdings, funds, demo=offline_demo)
    if not demo_pending:
        with st.container(key='refresh_status'):
            render_refresh_status(data_dir, funds, etf_revision, demo=offline_demo)
    context_key = sha256(str(data_dir.resolve()).encode()).hexdigest()[:12]
    unit_key = f'performance_unit_{context_key}_{reporting_currency}'
    def remember_unit():
        preferences = dict(st.session_state.get('performance_preferences', {}))
        preferences[context_key] = '%' if st.session_state[unit_key] == '%' else 'money'
        st.session_state['performance_preferences'] = preferences
    def toggle_unit():
        st.session_state[unit_key] = currency_symbol() if st.session_state.get(unit_key) == '%' else '%'
        remember_unit()
    percent, hide_empty = False, False
    if display_settings is not None:
        with display_settings:
            st.markdown('**Display**')
            percent = st.segmented_control('Performance display', [currency_symbol(), '%'], help='Show unrealized performance as a currency amount or a percentage of known cost.',
                default='%' if st.session_state.get('performance_preferences', {}).get(context_key) == '%' else currency_symbol(),
                key=unit_key, on_change=remember_unit) == '%'
            hide_empty = st.checkbox('Hide empty positions', key='hide_empty_positions',
                help='Hide zero-quantity rows in Positions and Exposure. Saved targets and planning weights stay unchanged.')
    if holdings.empty and st.session_state.get('currency_change_requested'):
        if render_currency_dialog(data_dir, snapshot, currency_settings, price_service or prices_for(data_dir), historical):
            mark_view_ready()
            return
    if holdings.empty and not demo and not feature_active:
        from portfolio_app.onboarding_ui import render_welcome, render_guided_setup
        if render_welcome(demo_available=demo_dir is not None):
            mark_view_ready()
            return
        if render_guided_setup(data_dir, snapshot, allocation):
            mark_view_ready()
            return
    background_prices = price_service is None and not offline_demo
    market_workspace = str(data_dir.resolve())
    market_revision = market_coordinator.revision(market_workspace)
    if price_service is None:
        if offline_demo:
            try:
                provider = StaticProvider(data_dir / 'demo_prices.json')
            except (OSError, ValueError):
                provider = UnavailableProvider()
            price_service = PriceService(provider)
        else:
            price_service = prices_for(data_dir)
    source = analysis_targets(holdings, allocation) if allocation else holdings
    with st.spinner('Valuing portfolio…'):
        valued = prepare_portfolio(source, price_service, refresh=refresh, reporting_currency=reporting_currency, historical=historical)
    if notice := st.session_state.pop('currency_notice', None):
        st.info(notice)
    if render_currency_dialog(data_dir, snapshot, currency_settings, price_service, historical):
        mark_view_ready()
        return
    estimates = int(valued.cost_estimated.sum())
    if estimates:
        st.warning(f'{estimates} positions use confirmed FX estimates. Their gains and returns are estimated.')
    if demo_pending:
        if background_prices:
            render_market_status(market_workspace, market_revision, show_caption=False)
        if initialize_live_demo(data_dir, valued):
            st.rerun()
        with st.container(key='demo_loading'):
            if market_coordinator.pending(market_workspace):
                # Keep the status running across renders; a context manager would
                # mark it complete as soon as this non-blocking render returns.
                status = st.status('Preparing your demo…', state='running', expanded=True)
                status.write('Fetching current market prices. Your demo will open automatically.')
            else:
                st.warning('Some prices could not be loaded. Choose Refresh prices to try again.')
                st.dataframe(valued.loc[valued.current_value_reporting.isna(), ['name', 'valuation_note']], hide_index=True)
                st.caption('You can also explore an offline example with ? → Take the tour, or return to My portfolio using the workspace menu.')
                mark_view_ready()
        return
    exposure_result = calculate_portfolio_exposure(valued, funds, future=sandbox[0] if sandbox else None,
        allocation=allocation, liabilities=leverage.liabilities, fund_factors=leverage.fund_factors) if feature_active else None
    if price_service.cache_warning:
        st.warning(price_service.cache_warning)
    missing_cost = (valued.shares.gt(0) & valued.unrealized_gain_reporting.isna()).sum()
    missing_price = valued.current_value_reporting.isna().sum()
    stale = (valued.price_status.isin(['cached fallback', 'stale']) | valued.fx_status.isin(['cached fallback', 'stale'])).sum()
    if missing_cost or missing_price or stale:
        with st.expander(f'Data status · {missing_price} missing prices · {missing_cost} performance gaps · {stale} stale quotes'):
            problems = valued.loc[valued.shares.gt(0) & (valued.unrealized_gain_reporting.isna() | (valued.price_status.isin(['cached fallback', 'stale']) | valued.fx_status.isin(['cached fallback', 'stale'])))]
            st.dataframe(problems[['name', 'performance_note', 'valuation_note']], hide_index=True, width='stretch')
            st.caption('Edit a position to complete its buy-in or pricing details.')
            if st.button('Complete buy-ins', help='Open Positions to complete missing purchase cost or pricing details.'):
                st.session_state['main_tabs'] = 'Positions'
                st.session_state['positions_workflow_request'] = 'Update balances'
    elif not offline_demo and not valued.price_status.eq('manual').any():
        st.caption('Latest available daily close · Prices may be delayed')
    if valued.price_status.eq('manual').any():
        st.caption(f"{valued.price_status.eq('manual').sum()} dated manual/snapshot prices · These do not refresh automatically.")
    if 'main_tabs' not in st.session_state:
        st.session_state['main_tabs'] = 'Positions' if holdings.empty and not feature_active else 'Overview'
    from portfolio_app.tour import prepare_tour, render_tour
    prepare_tour()
    tab_names = ['Overview', 'Exposure', 'Positions', 'Rebalance'] + (['Futures'] if sandbox else [])
    if st.session_state['main_tabs'] not in tab_names:
        st.session_state['main_tabs'] = 'Overview'
    tabs = st.tabs(tab_names,
        default='Overview', key='main_tabs', on_change='rerun')
    overview, exposure, positions, rebalance = tabs[:4]
    if sandbox and tabs[4].open and leverage_supported:
        with tabs[4]:
            from portfolio_app.futures_ui import render_futures
            render_futures(valued, data_dir, *sandbox, allocation=allocation, funds=funds, leverage=leverage)
    visible = valued.loc[valued.shares.gt(0)].copy() if hide_empty else valued
    if positions.open:
        with positions:
            render_position_editor(data_dir / 'holdings.csv', snapshot, funds, demo=offline_demo, embedded=True,
                                   allocation=allocation, valued=visible, percent=percent, defer_dialog=True)
    def open_valued_position(position_id, *, editing=False):
        if read_snapshot(data_dir / 'holdings.csv').revision != snapshot.revision:
            st.session_state['position_stale_notice'] = 'The portfolio changed. Select the position again from the refreshed view.'
            return
        request_position(position_id, editing=editing)
    if overview.open and not unsupported_records:
        with overview:
            if valued.empty and not feature_active:
                empty_overview()
            else:
                if allocation:
                    overview_config, overview_values = allocation, valued
                else:
                    overview_config, overview_values = migration_preview(valued)
                    # Legacy targets are percentages of the whole portfolio; don't
                    # reinterpret them as within-category targets in this overview.
                    overview_values['within_bucket_target'] = float('nan')
                overview_exposure = exposure_result if allocation or not feature_active else calculate_portfolio_exposure(
                    overview_values, funds, future=sandbox[0] if sandbox else None, allocation=overview_config,
                    liabilities=leverage.liabilities, fund_factors=leverage.fund_factors)
                from portfolio_app.portfolio_analytics_ui import render_portfolio_analytics
                render_strategic_overview(overview_values, overview_config, data_dir=data_dir, exposure_result=overview_exposure, future=sandbox[0] if sandbox else None, open_position=open_valued_position,
                                          leverage_enabled=leverage.enabled and leverage_supported,
                                          percent=percent, on_toggle_gain=toggle_unit, position_context=context_key,
                                          edit_position=lambda position_id: open_valued_position(position_id, editing=True),
                                          analytics=lambda selected, scope, scoped_future, scoped: render_portfolio_analytics(
                                              selected, data_dir, funds, demo=offline_demo, scope=scope, future=scoped_future,
                                              portfolio_result=overview_exposure if sandbox or leverage.liabilities else None, scoped=scoped))
    if exposure.open and not unsupported_records:
        with exposure:
            measure = st.segmented_control('Exposure measure', ['Market exposure', 'Funded holdings analysis'],
                default='Market exposure', key='exposure_measure') if leverage.enabled and leverage_supported else 'Funded holdings analysis'
            if measure == 'Market exposure':
                from portfolio_app.allocation import Allocation
                render_market_analysis(exposure_result, allocation or Allocation(()), classifications, valued, funds, future=sandbox[0] if sandbox else None)
            elif visible.empty:
                st.info('No visible positions. Add a position or turn off Hide empty positions.')
            else:
                render_analysis(data_dir, holdings.loc[holdings.position_id.isin(visible.position_id)], classifications,
                                funds, demo=offline_demo, price_service=price_service, source_valued=visible, performance_percent=percent,
                                allocation=allocation, etf_revision=etf_revision, on_toggle_gain=toggle_unit)
    if rebalance.open and not unsupported_records:
        with rebalance:
            plan_tab, targets_tab = st.tabs(['Plan', 'Targets'], key='rebalance_tabs', on_change='rerun')
            if targets_tab.open:
                with targets_tab:
                    from portfolio_app.allocation_ui import render_allocation_editor
                    from portfolio_app.portfolio_exposure_ui import render_policy_settings
                    if leverage_supported:
                        render_policy_settings(data_dir, leverage, allocation, future=sandbox[0] if sandbox else None)
                    else:
                        st.caption('Leverage settings currently require EUR reporting.')
                    target_mode = st.segmented_control('Target measure', ['Capital targets', 'Exposure targets'], default='Exposure targets' if leverage.active_basis == 'exposure' else 'Capital targets', key='exposure_target_mode') if leverage.enabled and leverage_supported else 'Capital targets'
                    if target_mode == 'Exposure targets':
                        render_exposure_targets(data_dir, allocation, exposure_result)
                    else:
                        render_allocation_editor(data_dir / 'holdings.csv', snapshot, allocation)
                    if leverage.enabled and leverage_supported:
                        render_leverage_editor(data_dir, valued, future=sandbox[0] if sandbox else None)
            if plan_tab.open:
                with plan_tab:
                    if leverage.enabled and leverage_supported:
                        from portfolio_app.portfolio_exposure_ui import render_policy_settings
                        render_policy_settings(data_dir, leverage, allocation, future=sandbox[0] if sandbox else None, planning=True)
                    try:
                        if leverage.enabled and leverage_supported and leverage.active_basis == 'exposure':
                            from portfolio_app.exposure_plan_ui import render_exposure_plan
                            render_exposure_plan(data_dir, exposure_result, allocation, valued, future=sandbox[0] if sandbox else None)
                        elif allocation:
                            if leverage.enabled and leverage_supported:
                                st.caption('Capital plan · Recorded holdings only; excludes dedicated futures strategy cash. Notional is not spendable cash.')
                            render_scoped_rebalancing(valued, allocation)
                        else:
                            render_rebalancing(valued)
                    except RebalanceError as exc:
                        st.error(str(exc))
    render_position_dialog(data_dir / 'holdings.csv', snapshot, funds, demo=offline_demo, allocation=allocation, valued=valued)
    if background_prices:
        render_market_status(market_workspace, market_revision)
    render_tour(empty=holdings.empty)
    mark_view_ready()



def render_market_status(market_workspace, market_revision, *, show_caption=True):
    pending = market_coordinator.pending(market_workspace)
    @st.fragment(run_every=.5 if pending else None)
    def market_status():
        if market_coordinator.revision(market_workspace) != market_revision:
            st.rerun()
        if show_caption and market_coordinator.pending(market_workspace):
            st.caption('Updating prices in the background · Saved quotes remain visible with their original dates')
    market_status()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, default=Path.cwd() / "data" / "portfolio")
    parser.add_argument("--demo-dir", type=Path)
    parser.add_argument("--demo", action="store_true")
    parser.add_argument("--ignore-workspace-selection", action="store_true")
    parser.add_argument("--skip-intro", action="store_true")
    args = parser.parse_args()
    render_app(args.data_dir, demo=args.demo, demo_dir=args.demo_dir, intro=not args.skip_intro, ignore_selection=args.ignore_workspace_selection)
