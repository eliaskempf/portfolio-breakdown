"""Streamlit controls and presentation; calculations live in pure modules."""

import argparse
from hashlib import sha256
from pathlib import Path

import streamlit as st

from portfolio_app.etf import load_funds, validate_fund_listings
from portfolio_app.exposure_ui import render_analysis
from portfolio_app.etf_refresh import coordinator
from portfolio_app.etf_refresh_ui import refresh_revision
from portfolio_app.holdings import DataError
from portfolio_app.position_ui import render_position_editor, render_position_dialog, request_position
from portfolio_app.positions import read_snapshot
from portfolio_app.portfolio import prepare_portfolio
from portfolio_app.rebalancing import ignore_empty_positions, RebalanceError
from portfolio_app.rebalance_ui import render_rebalancing
from portfolio_app.prices import PriceService, StaticProvider, YahooProvider, UnavailableProvider
from portfolio_app.presentation import apply_style, empty_overview, workspace_header
from portfolio_app.taxonomy import load_classifications
from portfolio_app.allocation import load_allocation, analysis_targets, ignore_empty_by_bucket, migration_preview
from portfolio_app.strategic_ui import render_strategic_overview
from portfolio_app.scoped_ui import render_scoped_rebalancing


def render_app(data_dir: Path, *, demo: bool = False, demo_dir: Path | None = None, price_service: PriceService | None = None) -> None:
    st.set_page_config(page_title="Portfolio breakdown", layout="wide")
    apply_style()
    if demo_dir is not None:
        workspace = st.sidebar.radio("Portfolio workspace", ["My portfolio", "Demo portfolio"], index=1 if demo else 0, key="active_portfolio")
        demo = workspace == "Demo portfolio"
        if demo:
            data_dir = demo_dir
        context = (str(data_dir.resolve()), demo)
        if st.session_state.get("portfolio_workspace_context") != context:
            for key in list(st.session_state):
                if key.startswith(("position_edit_", "filter_", "label_compare_", "allocation_group_", "rebalance_", "strategic_", "exposure_", "position_draft", "positions_")) or key in {"position_saved_notice", "ignore_empty_positions", "hide_empty_positions"}:
                    del st.session_state[key]
            st.session_state["portfolio_workspace_context"] = context
    workspace_header(demo)
    if demo:
        st.caption("Demo · Synthetic data · Resets on restart")
    etf_revision = refresh_revision(data_dir)
    try:
        snapshot = read_snapshot(data_dir / "holdings.csv")
        holdings = snapshot.holdings
        allocation = load_allocation(data_dir / 'allocation.yaml', holdings)
        classifications_path = data_dir / "classifications.yaml"
        classifications = load_classifications(classifications_path) if classifications_path.exists() else {}
        funds = load_funds(data_dir / "etfs")
        validate_fund_listings(holdings, funds)
    except DataError as exc:
        st.error(str(exc))
        st.info("Edit holdings.csv and classifications.yaml in the data directory, then rerun the app.")
        return
    coordinator.schedule(data_dir, holdings, funds, demo=demo)
    refresh = st.sidebar.button('Refresh prices', disabled=demo)
    context_key = sha256(str(data_dir.resolve()).encode()).hexdigest()[:12]
    unit_key = f'performance_unit_{context_key}'
    def remember_unit():
        preferences = dict(st.session_state.get('performance_preferences', {}))
        preferences[context_key] = st.session_state[unit_key] or '€'
        st.session_state['performance_preferences'] = preferences
    def toggle_unit():
        st.session_state[unit_key] = '€' if st.session_state.get(unit_key) == '%' else '%'
        remember_unit()
    percent = st.sidebar.segmented_control('Performance display', ['€', '%'],
        default=st.session_state.get('performance_preferences', {}).get(context_key, '€'),
        key=unit_key, on_change=remember_unit) == '%'
    hide_empty = st.sidebar.checkbox('Hide empty positions', key='hide_empty_positions',
        help='Hide zero-quantity rows in Positions and Exposure. Saved targets and planning weights stay unchanged.')
    if price_service is None:
        if demo:
            try:
                provider = StaticProvider(data_dir / 'demo_prices.json')
            except (OSError, ValueError):
                provider = UnavailableProvider()
        else:
            provider = YahooProvider(data_dir / '.cache' / 'yahoo')
        price_service = PriceService(provider, None if demo else data_dir / '.cache' / 'prices.json')
    source = analysis_targets(holdings, allocation) if allocation else holdings
    with st.spinner('Valuing portfolio…'):
        valued = prepare_portfolio(source, price_service, refresh=refresh)
    missing_cost = (valued.shares.gt(0) & valued.unrealized_gain_eur.isna()).sum()
    missing_price = valued.current_value_eur.isna().sum()
    stale = (valued.price_status.eq('cached fallback') | valued.fx_status.eq('cached fallback')).sum()
    if missing_cost or missing_price or stale:
        with st.expander(f'Data status · {missing_price} missing prices · {missing_cost} performance gaps · {stale} stale quotes'):
            problems = valued.loc[valued.shares.gt(0) & (valued.unrealized_gain_eur.isna() | (valued.price_status.eq('cached fallback') | valued.fx_status.eq('cached fallback')))]
            st.dataframe(problems[['name', 'performance_note', 'valuation_note']], hide_index=True, width='stretch')
            st.caption('Edit a position to complete its buy-in or pricing details.')
            if st.button('Complete buy-ins'):
                st.session_state['main_tabs'] = 'Positions'
                st.session_state['positions_workflow'] = 'Update balances'
    else:
        st.caption('Latest available daily close · Prices may be delayed')
    overview, exposure, positions, rebalance = st.tabs(['Overview', 'Exposure', 'Positions', 'Rebalance'],
        default='Positions' if holdings.empty else 'Overview', key='main_tabs')
    visible = valued.loc[valued.shares.gt(0)].copy() if hide_empty else valued
    with positions:
        render_position_editor(data_dir / 'holdings.csv', snapshot, funds, demo=demo, embedded=True,
                               allocation=allocation, valued=visible, percent=percent, defer_dialog=True)
    def open_valued_position(position_id):
        if read_snapshot(data_dir / 'holdings.csv').revision != snapshot.revision:
            st.session_state['position_stale_notice'] = 'The portfolio changed. Select the position again from the refreshed view.'
            return
        request_position(position_id)
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
                                      percent=percent, on_toggle_gain=toggle_unit,
                                      analytics=lambda selected, scope: render_portfolio_analytics(
                                          selected, data_dir, funds, demo=demo, scope=scope))
    with exposure:
        if visible.empty:
            st.info('No visible positions. Add a position or turn off Hide empty positions.')
        else:
            render_analysis(data_dir, holdings.loc[holdings.position_id.isin(visible.position_id)], classifications,
                            funds, demo=demo, price_service=price_service, source_valued=visible, performance_percent=percent,
                            allocation=allocation, etf_revision=etf_revision)
    with rebalance:
        plan_tab, targets_tab = st.tabs(['Plan', 'Targets'])
        with targets_tab:
            from portfolio_app.allocation_ui import render_allocation_editor
            render_allocation_editor(data_dir / 'holdings.csv', snapshot, allocation)
        with plan_tab:
            redistribute = st.checkbox('Exclude empty positions and redistribute their planning targets',
                key='ignore_empty_positions', help='Changes temporary planning weights only. Saved targets stay unchanged.')
            planning = valued
            try:
                if redistribute:
                    planning = analysis_targets(ignore_empty_by_bucket(valued), allocation) if allocation else ignore_empty_positions(valued)
                    st.caption('Planning weights exclude empty positions; their targets are redistributed. Saved targets are unchanged.')
                    if planning.empty and not valued.empty:
                        st.info('All positions have zero shares. Disable planning target redistribution to allocate new money to them.')
                if allocation:
                    render_scoped_rebalancing(planning, allocation)
                else:
                    render_rebalancing(planning)
            except RebalanceError as exc:
                st.error(str(exc))
    render_position_dialog(data_dir / 'holdings.csv', snapshot, funds, demo=demo, allocation=allocation, valued=valued)




if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, default=Path.cwd() / "data" / "portfolio")
    parser.add_argument("--demo-dir", type=Path)
    parser.add_argument("--demo", action="store_true")
    args = parser.parse_args()
    render_app(args.data_dir, demo=args.demo, demo_dir=args.demo_dir)
