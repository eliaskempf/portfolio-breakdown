"""Interactive futures overlay, separate from funded-asset targets."""
from hashlib import sha256
from dataclasses import replace

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from portfolio_app.money import euros
from portfolio_app.charts import chart_config

from portfolio_app.futures import CONTRACTS, futures_scenarios, summarize_future
from portfolio_app.futures_market_ui import render_market_reference, render_price_provenance
from portfolio_app.futures_workspace import save_sandbox, switch_to_fmwn
from portfolio_app.holdings import DataError
from portfolio_app.futures_reference import ECB_URL, IBKR_URL, load_references, refresh_references



def render_futures(valued, data_dir, position, revision, *, allocation=None, funds=(), leverage=None):
    st.subheader(f'{position.instrument} · {CONTRACTS[position.instrument][0]} futures')
    if position.instrument == 'FMAE':
        st.info('This saved mock is still FMAE. Switching starts FMWN at invented 500-point entry and mark prices, '
                'resets margin and futures-beta assumptions, and keeps a private backup of the old position.')
        if st.button('Start FMWN mock position'):
            try:
                switch_to_fmwn(data_dir, revision)
            except (DataError, OSError) as exc:
                st.error(str(exc))
            else:
                st.rerun()
    results_area = st.container()
    settings_area = st.container()
    context = sha256(str(data_dir.resolve()).encode()).hexdigest()[:12]
    with settings_area:
        st.subheader('Position, assumptions & data')
        try:
            reference = load_references(data_dir)
        except DataError as exc:
            st.warning(str(exc))
            reference = None
        with st.expander('ECB and IBKR reference data'):
            if reference:
                st.caption(f"€STR {reference['estr_rate']:.3%} · Reference date {reference['estr_reference_date']} · "
                           f"IBKR margins observed {reference['fetched_at'][:10]}. Snapshots do not update automatically.")
            else:
                st.info('No published reference snapshot saved. Inputs below are hypothetical until you load one.')
            st.markdown(f'[ECB €STR]({ECB_URL}) · [IBKR Ireland futures margins]({IBKR_URL})')
            if st.button('Refresh ECB / IBKR reference data'):
                try:
                    with st.spinner('Reading published rates and margins…'):
                        refresh_references(data_dir)
                except (OSError, ValueError) as exc:
                    st.error(f'Refresh failed; existing snapshot retained. {exc}')
                else:
                    if position.instrument == 'FMWN':
                        st.session_state[f'futures_input_{context}_published'] = True
                    st.rerun()
        render_market_reference(data_dir, position, revision)
        key = f'futures_input_{context}_{revision}_'
        with st.expander('Mock position'):
            from portfolio_app.strategic import category_labels
            labels = {'': 'Unassigned', **(category_labels(allocation) if allocation else {})}
            choices = ['', *sorted(allocation.leaves())] if allocation else ['']
            bucket_id = st.selectbox('Futures strategy category', choices, index=choices.index(position.bucket_id) if position.bucket_id in choices else 0, format_func=labels.get, key=key + 'bucket')
            from portfolio_app.etf import matching_fund
            companions = {'': 'None · keep index constituents unresolved'}
            for row in valued.to_dict('records'):
                fund = matching_fund(row, funds)
                if fund and fund.equity_fund:
                    companions[row['position_id']] = row['name'] + (' · ' + row.get('account', '') if row.get('account') else '')
            if position.proxy_position_id and position.proxy_position_id not in companions:
                companions[position.proxy_position_id] = 'Unavailable saved companion · review selection'
            proxy_position_id = st.selectbox('Companion ETF / holdings proxy', list(companions),
                index=list(companions).index(position.proxy_position_id), format_func=companions.get,
                key=key + 'proxy', help='Choose an ETF tracking the same index. Its holdings approximate the future’s company, sector and geographic exposure; weights and dates may differ from the index.')
            left, middle, right = st.columns(3)
            contracts = left.number_input('Signed contracts', min_value=-10000, max_value=10000,
                value=position.contracts, step=1, key=key + 'contracts', help='Positive = long; negative = short; zero removes exposure.')
            entry = middle.number_input('Entry price (points)', min_value=0.05, value=float(position.entry_price),
                                        step=0.05, format='%.2f', key=key + 'entry')
            mark = right.number_input('Current mock price (points)', min_value=0.05, value=float(position.mark_price),
                                     step=0.05, format='%.2f', key=key + 'mark')
            render_price_provenance(position, entry, mark)
        with st.expander('Margin and beta assumptions'):
            published = st.checkbox('Use published IBKR overnight margins', value=bool(reference) and position.instrument == 'FMWN',
                disabled=not reference or position.instrument != 'FMWN', key=f'futures_input_{context}_published')
            side = 'short' if contracts < 0 else 'long'
            use_reference = published and reference is not None and position.instrument == 'FMWN'
            initial_value = reference[f'{side}_initial'] if use_reference else position.margin_per_contract
            maintenance_value = reference[f'{side}_maintenance'] if use_reference else (position.maintenance_per_contract if position.maintenance_per_contract is not None else position.margin_per_contract)
            left, right = st.columns(2)
            source_key = f"{side}_{reference['fetched_at']}" if use_reference else 'custom'
            margin = left.number_input('Initial margin per contract (€)', min_value=0., value=float(initial_value),
                                       step=100., key=key + source_key + '_margin', disabled=bool(use_reference))
            maintenance = right.number_input('Maintenance margin per contract (€)', min_value=0., value=float(maintenance_value),
                                       step=100., key=key + source_key + '_maintenance', disabled=bool(use_reference))
            collateral = position.collateral_budget  # Legacy position metadata; analysis cash is shared below.
            st.caption('IBKR mode uses the published long/short overnight requirements for FMWN, in EUR per contract. '
                       'Custom inputs are assumptions. Account-specific offsets, extra house margin and future changes are not modelled. '
                       'Earmarking collateral does not reduce net equity.')
            left, right = st.columns(2)
            base_beta = left.number_input('Base portfolio beta (assumption)', min_value=-3., max_value=3.,
                                          value=float(position.base_beta), step=0.05, key=key + 'base_beta')
            future_beta = right.number_input('Futures beta (assumption)', min_value=-3., max_value=3.,
                                             value=float(position.futures_beta), step=0.05, key=key + 'future_beta')
            st.caption('Both betas start at an illustrative 1.0 against a common global-equity benchmark. '
                       'These are editable assumptions, not estimated historical betas.')
        with st.expander('Strategy capital & lifetime gains'):
            reference_capital = st.number_input('Reference capital (€)', min_value=0.01, value=position.reference_capital_eur,
                key=key + 'reference_capital', help='Dedicated cash not already recorded in other holdings. Leave blank for a legacy unfunded overlay.')
            net_funding = st.number_input('Net additional funding (€)', value=float(position.net_funding_eur), key=key + 'net_funding')
            closed_pnl = st.number_input('Previous contracts P&L (€)', value=float(position.closed_pnl_eur), key=key + 'closed_pnl')
            fees = st.number_input('Lifetime transaction fees (€)', min_value=0., value=float(position.fees_eur), key=key + 'fees')
            st.caption('Value = reference capital + net additional funding + previous and active contract P&L − fees. '
                       'Net funding is extra deposits minus withdrawals; it changes value, not gain. '
                       'Return always uses fixed reference capital. Cash earns 0%. No transaction ledger is required. '
                       'Standalone Starting cash is an independent analysis input.')
            st.caption('To roll: save the old contract’s closing mark and fees, close it below, then enter the new contract. '
                       'Closing preserves cash and moves its P&L into Previous contracts P&L. '
                       'Updating a mark never resets lifetime gains. This model does not simulate automatic liquidation.')
            if st.button('Close saved contract and retain cash', disabled=position.reference_capital_eur is None or not position.contracts):
                from portfolio_app.futures import close_contract
                try:
                    save_sandbox(data_dir, close_contract(position), revision)
                except (DataError, OSError) as exc:
                    st.error(str(exc))
                else:
                    st.rerun()
        try:
            preview = replace(position, contracts=contracts, entry_price=entry, mark_price=mark, margin_per_contract=margin,
                              collateral_budget=collateral, base_beta=base_beta, futures_beta=future_beta,
                              maintenance_per_contract=maintenance, bucket_id=bucket_id, proxy_position_id=proxy_position_id,
                              reference_capital_eur=reference_capital, net_funding_eur=net_funding,
                              closed_pnl_eur=closed_pnl, fees_eur=fees)
        except DataError as exc:
            st.error(str(exc))
            return
        if st.button('Save mock position', type='primary', key='futures_save'):
            try:
                if position.reference_capital_eur is not None and position.contracts and contracts != position.contracts:
                    raise DataError('Close the saved contract first to preserve its P&L before changing contract count.')
                save_sandbox(data_dir, preview, revision)
            except (DataError, OSError) as exc:
                st.error(str(exc))
            else:
                st.session_state['futures_saved_notice'] = True
                st.rerun()
        if st.session_state.pop('futures_saved_notice', False):
            st.success('Mock position saved in this sandbox.')
        if preview != position:
            st.caption('Unsaved preview · Save to keep these settings after restarting and update the other tabs.')
    summary = summarize_future(valued, preview)
    from portfolio_app.portfolio_exposure import calculate_portfolio_exposure
    marked = calculate_portfolio_exposure(valued, funds, future=preview, allocation=allocation,
        liabilities=leverage.liabilities if leverage else (), fund_factors=leverage.fund_factors if leverage else ())
    with settings_area:
        from portfolio_app.portfolio_exposure_ui import render_saved_collateral
        render_saved_collateral(position)
    with settings_area.expander('Portfolio effect'):
        columns = st.columns(4)
        columns[0].metric('Base holdings value', euros(summary.base_value))
        columns[1].metric('Cumulative futures P&L', euros(preview.lifetime_gain))
        columns[2].metric('Net equity', euros(marked.net_equity), delta=euros(preview.lifetime_gain))
        columns[3].metric('Signed futures exposure', euros(preview.notional))
        integrated = marked
        from portfolio_app.portfolio_exposure_ui import render_equity_exposure_metric
        render_equity_exposure_metric(integrated)
        st.caption('Current equity exposure uses classified funded assets plus signed futures notional. Cash contributes zero. '
                   'Integrated beta is unavailable without suitable underlying beta coverage. Scenario beta inputs remain illustrative.')
        for issue in integrated.issues:
            st.warning(issue)
        if summary.missing_prices:
            st.warning(f'{summary.missing_prices} held positions have missing valuations. Whole-portfolio equity, leverage and beta '
                       'are unavailable; futures P&L remains calculable.')
        elif summary.equity is not None and summary.equity <= 0:
            st.warning('Simulated net equity is zero or negative. Leverage and beta ratios are undefined.')
        if preview.reference_capital_eur is None and summary.base_value is not None and collateral > summary.base_value:
            st.warning('The collateral budget exceeds the base portfolio value. It is not added as new capital.')
    with results_area:
        view = st.segmented_control('Futures analysis', ['Funding policies', 'Stress & history', 'Investment outcomes'],
                                     default='Funding policies', key=f'futures_input_{context}_view_v3', label_visibility='collapsed')
        from portfolio_app.futures_assumptions_ui import render_shared_assumptions
        # Standalone cash belongs above outcome charts; it is not the funding-study reserve.
        with (results_area if view == 'Investment outcomes' else settings_area):
            assumptions, provenance = render_shared_assumptions(preview, data_dir, context, reference, settings_area)
        if assumptions is None:
            return
        if view == 'Investment outcomes':
            outcome = st.radio('Investment analysis', ['Collateral & savings', 'Historical quarters'], horizontal=True,
                               key=f'futures_input_{context}_outcome')
            st.caption('Standalone investment comparison · no top-ups or re-entry after a model exit.')
            if outcome == 'Historical quarters':
                from portfolio_app.futures_history_ui import render_historical
                render_historical(preview, data_dir, context, assumptions, settings_area)
            else:
                from portfolio_app.futures_comparison_ui import render_comparison
                render_comparison(preview, assumptions, provenance, summary.base_value)
            return
        if view == 'Funding policies':
            from portfolio_app.futures_funding_ui import render_funding
            render_funding(preview, assumptions, provenance, data_dir, context, settings_area)
            return
        stress_mode = st.radio('Stress analysis', ['Funding replay', 'Price scenarios'], horizontal=True,
                               key=f'futures_input_{context}_stress_mode')
        if stress_mode == 'Funding replay':
            from portfolio_app.futures_funding_ui import render_funding
            render_funding(preview, assumptions, provenance, data_dir, context, settings_area, stress=True)
            return
        st.divider()
        st.markdown('**Move the market**')
        mode = st.radio('Scenario', ['Futures only; base holdings stay flat', 'Global market; both respond to assumed beta'],
                        key=f'futures_input_{context}_mode', horizontal=True)
        move_base = mode.startswith('Global')
        move = st.slider('Market move from current price (%)', -30., 30., 0., step=1., key=f'futures_input_{context}_move')
        selected = futures_scenarios(summary, preview, [move / 100], move_base=move_base, assumptions=assumptions, portfolio_result=marked).iloc[0]
        columns = st.columns(3)
        columns[0].metric('Additional futures gain / loss', euros(selected['Additional futures P&L (EUR)']))
        columns[1].metric('Scenario net equity', euros(None if pd.isna(selected['With futures (EUR)']) else selected['With futures (EUR)']))
        columns[2].metric('Illustrative collateral shortfall', euros(selected['Illustrative collateral shortfall (EUR)']))
        curve = futures_scenarios(summary, preview, [value / 100 for value in range(-30, 31)], move_base=move_base, assumptions=assumptions, portfolio_result=marked)
        figure = go.Figure()
        if summary.base_value is not None:
            for label, color in [('Without futures (EUR)', '#94a3b8'), ('With futures (EUR)', '#5470c6')]:
                figure.add_scatter(x=curve['Market move (%)'], y=curve[label], name=label.removesuffix(' (EUR)'),
                                   mode='lines', line=dict(color=color, width=3))
            figure.update_yaxes(title='Portfolio equity (€)', tickprefix='€')
        else:
            figure.add_scatter(x=curve['Market move (%)'], y=curve['Cumulative futures P&L (EUR)'],
                               name='Cumulative futures P&L', mode='lines')
            figure.update_yaxes(title='Futures P&L (€)', tickprefix='€')
        figure.add_vline(x=move, line_dash='dot', line_color='#94a3b8')
        figure.update_layout(height=340, margin=dict(l=10, r=10, t=15, b=10),
                             xaxis_title='Move from current market (%)', legend=dict(orientation='h', y=1.12))
        st.plotly_chart(figure, width='stretch', config=chart_config(zoom=True))
        st.caption('Scenarios keep the contract count fixed. Global-market mode applies each assumed beta to the market move; '
                   'futures-only mode moves the futures price directly. Shared starting cash, margin stress and scaling apply. '
                   'Recorded EUR debt stays fixed. The global-market shock uses an illustrative beta on the funded base, '
                   'not an estimated integrated portfolio beta. An instantaneous shock has no holding period, financing accrual or new execution costs.')
        with st.expander('Scenario details'):
            st.dataframe(futures_scenarios(summary, preview, [-.3, -.2, -.1, -.05, 0., .05, .1, .2, .3], move_base=move_base, assumptions=assumptions, portfolio_result=marked),
                         hide_index=True, width='stretch')
        with st.expander('Exposure alongside the portfolio'):
            table = marked.exposures[['asset_name', 'source_position_id', 'value', 'exposure_reporting', 'status']].copy()
            table.columns = ['Investment', 'Source position', 'Funded value (EUR)', 'Equity exposure (EUR)', 'Coverage']
            table['Exposure / net equity (%)'] = table['Equity exposure (EUR)'] / marked.denominator * 100 if marked.denominator and marked.exposure_complete else float('nan')
            st.dataframe(table, hide_index=True, width='stretch')
            from portfolio_app.portfolio_exposure_ui import render_capital_reconciliation
            render_capital_reconciliation(marked)
        with st.expander('How this price shock works'):
            st.markdown('Futures P&L = signed contracts × €100 × (current price − entry price). '
                        'Notional = signed contracts × €100 × current price. '
                        'Net equity = current base holdings value + strategy capital value − recorded liabilities. '
                        'A funded strategy includes reference capital, net funding and lifetime gain less fees. '
                        'A legacy unfunded overlay contributes only mock P&L. Notional is never wealth.')
            st.markdown('Collateral shortfall = max(0, stressed maintenance margin − shared starting cash − cumulative futures P&L). '
                        'This illustrates funding pressure; it does not reproduce broker maintenance margin, netting, '
                        'haircuts or forced liquidation. Recorded lifetime fees are retained; an instantaneous shock adds no new fees or interest.')
            st.markdown('Base allocation targets and rebalancing continue to describe funded holdings. '
                        'A funded strategy participates in Overview capital allocation, but the purchase optimizer excludes its dedicated cash. '
                        'No company look-through or automatic roll execution is implied.')
            st.markdown(f'[{position.instrument} contract information at Eurex]({CONTRACTS[position.instrument][2]})')
