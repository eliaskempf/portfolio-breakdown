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
from portfolio_app.presentation import apply_style, empty_overview
from portfolio_app.allocation import analysis_targets, migration_preview
from portfolio_app.strategic_ui import render_strategic_overview
from portfolio_app.scoped_ui import render_scoped_rebalancing
from portfolio_app.input_cache import load_inputs
from portfolio_app.market_data import coordinator as market_coordinator, prices_for
from portfolio_app.view_state import preserve_view_inputs


def render_app(data_dir: Path, *, demo: bool = False, demo_dir: Path | None = None, price_service: PriceService | None = None, intro: bool = False) -> None:
    from portfolio_app.settings import icon_path
    from portfolio_app.workspace_ui import app_header, workspace_info
    persistent_data_dir = data_dir
    icon = icon_path('favicon.svg')
    st.set_page_config(page_title="Portfolio breakdown", layout="wide", page_icon=str(icon) if icon else None)
    apply_style()
    if intro:
        from portfolio_app.intro import render_startup_intro
        if not render_startup_intro():
            return
    preserve_view_inputs()
    data_dir, demo, refresh, settings_panel = app_header(data_dir, demo_dir, demo=demo)
    with settings_panel:
        display_settings = st.container()
        # Recovery controls must also be available when loading inputs fails.
        workspace_info(data_dir, persistent_data_dir, demo=demo)
    offline_demo = demo and not (data_dir / '.live-demo').exists()
    from portfolio_app.import_ui import render_import_next_steps
    render_import_next_steps()
    if demo:
        st.caption("Offline demo · Invented prices, buy-ins and ETF weights · Resets on restart" if offline_demo else
                   "Demo · Invented quantities, targets and buy-ins · Public market data · Resets on restart")
    etf_revision = refresh_revision(data_dir)
    try:
        snapshot, allocation, classifications, funds = load_inputs(data_dir)
        holdings = snapshot.holdings
        validate_fund_listings(holdings, funds)
    except DataError as exc:
        st.error(str(exc))
        st.info("Edit holdings.csv and classifications.yaml in the data directory, then rerun the app.")
        return
    coordinator.schedule(data_dir, holdings, funds, demo=offline_demo)
    with st.container(key='refresh_status'):
        render_refresh_status(data_dir, funds, etf_revision, demo=offline_demo)
    context_key = sha256(str(data_dir.resolve()).encode()).hexdigest()[:12]
    unit_key = f'performance_unit_{context_key}'
    def remember_unit():
        preferences = dict(st.session_state.get('performance_preferences', {}))
        preferences[context_key] = st.session_state[unit_key] or '€'
        st.session_state['performance_preferences'] = preferences
    def toggle_unit():
        st.session_state[unit_key] = '€' if st.session_state.get(unit_key) == '%' else '%'
        remember_unit()
    with display_settings:
        st.markdown('**Display**')
        percent = st.segmented_control('Performance display', ['€', '%'],
            default=st.session_state.get('performance_preferences', {}).get(context_key, '€'),
            key=unit_key, on_change=remember_unit) == '%'
        hide_empty = st.checkbox('Hide empty positions', key='hide_empty_positions',
            help='Hide zero-quantity rows in Positions and Exposure. Saved targets and planning weights stay unchanged.')
    if holdings.empty and not demo:
        from portfolio_app.onboarding_ui import render_welcome
        if render_welcome(demo_available=demo_dir is not None):
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
        valued = prepare_portfolio(source, price_service, refresh=refresh)
    if demo and live_demo_pending(data_dir):
        if background_prices:
            render_market_status(market_workspace, market_revision)
        if initialize_live_demo(data_dir, valued):
            st.rerun()
        st.info('Preparing the demo with public quotes. Initial quantities will keep the category allocations close to their targets.')
        if not market_coordinator.pending(market_workspace):
            st.warning('Some quotes are unavailable. Use Refresh prices to retry. Synthetic prices are never substituted for live data.')
            st.dataframe(valued.loc[valued.current_value_eur.isna(), ['name', 'valuation_note']], hide_index=True)
            st.caption('For an offline example, restart with --offline-demo. My portfolio remains available in the sidebar.')
        return
    if price_service.cache_warning:
        st.warning(price_service.cache_warning)
    missing_cost = (valued.shares.gt(0) & valued.unrealized_gain_eur.isna()).sum()
    missing_price = valued.current_value_eur.isna().sum()
    stale = (valued.price_status.isin(['cached fallback', 'stale']) | valued.fx_status.isin(['cached fallback', 'stale'])).sum()
    if missing_cost or missing_price or stale:
        with st.expander(f'Data status · {missing_price} missing prices · {missing_cost} performance gaps · {stale} stale quotes'):
            problems = valued.loc[valued.shares.gt(0) & (valued.unrealized_gain_eur.isna() | (valued.price_status.isin(['cached fallback', 'stale']) | valued.fx_status.isin(['cached fallback', 'stale'])))]
            st.dataframe(problems[['name', 'performance_note', 'valuation_note']], hide_index=True, width='stretch')
            st.caption('Edit a position to complete its buy-in or pricing details.')
            if st.button('Complete buy-ins'):
                st.session_state['main_tabs'] = 'Positions'
                st.session_state['positions_workflow_request'] = 'Update balances'
    elif not offline_demo and not valued.price_status.eq('manual').any():
        st.caption('Latest available daily close · Prices may be delayed')
    if valued.price_status.eq('manual').any():
        st.caption(f"{valued.price_status.eq('manual').sum()} dated manual/snapshot prices · These do not refresh automatically.")
    if 'main_tabs' not in st.session_state:
        st.session_state['main_tabs'] = 'Positions' if holdings.empty else 'Overview'
    overview, exposure, positions, rebalance = st.tabs(['Overview', 'Exposure', 'Positions', 'Rebalance'],
        default='Overview', key='main_tabs', on_change='rerun')
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
    if overview.open:
        with overview:
            if valued.empty:
                empty_overview()
            else:
                if allocation:
                    overview_config, overview_values = allocation, valued
                else:
                    overview_config, overview_values = migration_preview(valued)
                    # Legacy targets are percentages of the whole portfolio; don't
                    # reinterpret them as within-category targets in this overview.
                    overview_values['within_bucket_target'] = float('nan')
                from portfolio_app.portfolio_analytics_ui import render_portfolio_analytics
                render_strategic_overview(overview_values, overview_config, open_position=open_valued_position,
                                          percent=percent, on_toggle_gain=toggle_unit, position_context=context_key,
                                          edit_position=lambda position_id: open_valued_position(position_id, editing=True),
                                          analytics=lambda selected, scope: render_portfolio_analytics(
                                              selected, data_dir, funds, demo=offline_demo, scope=scope))
    if exposure.open:
        with exposure:
            if visible.empty:
                st.info('No visible positions. Add a position or turn off Hide empty positions.')
            else:
                render_analysis(data_dir, holdings.loc[holdings.position_id.isin(visible.position_id)], classifications,
                                funds, demo=offline_demo, price_service=price_service, source_valued=visible, performance_percent=percent,
                                allocation=allocation, etf_revision=etf_revision, on_toggle_gain=toggle_unit)
    if rebalance.open:
        with rebalance:
            plan_tab, targets_tab = st.tabs(['Plan', 'Targets'], key='rebalance_tabs', on_change='rerun')
            if targets_tab.open:
                with targets_tab:
                    from portfolio_app.allocation_ui import render_allocation_editor
                    render_allocation_editor(data_dir / 'holdings.csv', snapshot, allocation)
            if plan_tab.open:
                with plan_tab:
                    try:
                        if allocation:
                            render_scoped_rebalancing(valued, allocation)
                        else:
                            render_rebalancing(valued)
                    except RebalanceError as exc:
                        st.error(str(exc))
    render_position_dialog(data_dir / 'holdings.csv', snapshot, funds, demo=offline_demo, allocation=allocation, valued=valued)
    if background_prices:
        render_market_status(market_workspace, market_revision)



def render_market_status(market_workspace, market_revision):
    pending = market_coordinator.pending(market_workspace)
    @st.fragment(run_every=.5 if pending else None)
    def market_status():
        if market_coordinator.revision(market_workspace) != market_revision:
            st.rerun()
        if market_coordinator.pending(market_workspace):
            st.caption('Updating prices in the background · Saved quotes remain visible with their original dates')
    market_status()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, default=Path.cwd() / "data" / "portfolio")
    parser.add_argument("--demo-dir", type=Path)
    parser.add_argument("--demo", action="store_true")
    parser.add_argument("--skip-intro", action="store_true")
    args = parser.parse_args()
    render_app(args.data_dir, demo=args.demo, demo_dir=args.demo_dir, intro=not args.skip_intro)
