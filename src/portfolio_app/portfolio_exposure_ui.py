"""Signed exposure displays and opt-in settings; calculations stay in pure modules."""
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from portfolio_app.money import euros, format_money
from portfolio_app.charts import chart_config

from portfolio_app.charts import style_figure
from portfolio_app.exposure_targets import ExposureTargets, exposure_gaps, load_exposure_targets, save_exposure_targets
from portfolio_app.holdings import DataError
from portfolio_app.leverage_settings import FundFactor, LeverageSettings, MarginLiability, load_leverage_settings, save_leverage_settings, requires_leverage
from portfolio_app.strategic import category_labels


def render_exposure_coverage(rows, sources, *, field='market_exposure_reporting'):
    """Explain unavailable exposure separately from source price/FX coverage."""
    missing = rows.loc[rows[field].isna()]
    if missing.empty:
        return
    st.warning('Exposure is partially known. Known contributions remain visible; total-exposure shares and leverage ratios need complete coverage.')
    details = missing[['source_position_id', 'asset_name', 'status']].drop_duplicates().copy()
    names = sources.set_index('position_id')['name']
    details['source_position_id'] = details.source_position_id.map(names)
    details['status'] = details.status.replace({
        'Unknown composition': 'Constituent exposure cannot be determined from its reported market value.',
        'Missing valuation': 'Source price or currency conversion is unavailable.',
    })
    with st.expander('Why is exposure incomplete?', expanded=len(details) <= 5):
        st.dataframe(details.rename(columns={'source_position_id': 'Source', 'asset_name': 'Unresolved asset',
            'status': 'Reason'}).head(20), hide_index=True, width='stretch')
        if len(details) > 20:
            st.caption('First 20 unresolved entries. Full details are available in Exposure provenance.')
        st.caption('A known fund price does not establish exposure for every constituent. Unresolved amounts are neither zero nor missing ETF prices.')


def render_equity_exposure_metric(result, *, market=False, bucket_ids=None, freshness=None):
    from portfolio_app.metric_interactions import toggle_metric_value
    gross = st.session_state.get('exposure_multiple_mode', 'net') == 'gross'
    basis = 'market' if market else 'equity'
    value = result.selected_multiple(bucket_ids, gross=gross, market=market)
    scope = 'Category' if bucket_ids is not None else 'Portfolio'
    label = (f'{scope} leverage · gross' if gross else f'{scope} leverage · net') if market else ('Gross equity exposure' if gross else 'Net equity exposure')
    text = '—' if value is None else f'{value:.2f}×'
    calculation = ''
    if value is not None and bucket_ids is None:
        amount = result.market_amount(gross=gross) if market else result.gross_exposure if gross else result.net_exposure
        calculation = (f'{format_money(amount,result.reporting_currency)} exposure / '
                       f'{format_money(result.net_equity,result.reporting_currency)} net equity = {value:.4f}×. ')
    with st.container(key='equity_exposure_metric'):
        st.metric(label, text,
                  help=calculation+'Click to switch net/gross. Net offsets longs and shorts; gross adds absolute exposures before offsets. '
                       'Exposure / selected capital, including allocated futures cash and cumulative P&L. '
                       'Futures contribute contract notional to exposure; their allocated cash is counted only in capital. '
                       'Portfolio debt is included only at portfolio scope; category leverage is unavailable with unassigned debt. '
                       'Gold and crypto count; cash contributes zero exposure.')
        if freshness and freshness[0]:
            st.caption(freshness[0], help=freshness[1])
    def toggle():
        st.session_state['exposure_multiple_mode'] = 'net' if gross else 'gross'
    toggle_metric_value(label=f'Show {"net" if gross else "gross"} {basis} exposure',
                        on_toggle=toggle, container_key='equity_exposure_metric', interaction_key='equity_exposure')


def render_saved_collateral(future):
    with st.expander('Saved position collateral'):
        cols = st.columns(3)
        cols[0].metric('Saved initial margin', euros(future.margin))
        cols[1].metric('Saved maintenance margin', euros(future.maintenance_margin))
        cols[2].metric('Strategy cash balance' if future.reference_capital_eur is not None else 'Sandbox collateral assumption',
                       euros(future.capital_value if future.reference_capital_eur is not None else future.collateral_budget))
        st.caption('A funded strategy owns its dedicated cash plus lifetime gains; cash earns 0%. No broker eligibility or liquidation model is implied. '
                   'Legacy collateral assumptions and standalone Starting cash do not create portfolio assets.')


def render_capital_reconciliation(result, *, actual=False):
    if actual:
        st.caption('Ordinary assets + account cash + unsettled claims − debt = net equity. Posted settlement is already in cash; collateral is an existing asset and notional is exposure only.')
        st.dataframe(pd.DataFrame({'Capital component': ['Assets, cash and unsettled claims', 'Recorded debt / overdrafts', 'Net equity'],
            result.reporting_currency: [result.owned_assets, -result.liabilities if result.liabilities is not None else None, result.net_equity]}), hide_index=True, width='stretch')
        return
    st.caption('Owned holdings + funded futures strategy + legacy mock settlement adjustment − liabilities = net equity. '
               'The funded strategy includes its dedicated cash and lifetime gains once. Notional is never wealth.')
    st.dataframe(pd.DataFrame({'Capital component': ['Owned assets', 'Funded futures strategy', 'Mock settlement adjustment', 'Recorded liabilities', 'Net equity'],
        result.reporting_currency: [result.owned_assets, result.futures_capital_value, result.settlement_adjustment, -result.liabilities if result.liabilities is not None else None, result.net_equity]}),
        hide_index=True, width='stretch')


def signed_chart(table, label, measure, key):
    selected = table.dropna(subset=[measure])
    if selected.empty:
        st.info('No complete values to chart for this selection.')
        return
    if len(selected) > 30:
        st.caption('Chart: 30 largest absolute exposure contributions. The table retains every row; percentages are not renormalized.')
        selected = selected.loc[selected[measure].abs().nlargest(30).index]
    figure = style_figure(go.Figure(go.Bar(x=selected[measure].tolist(), y=list(range(len(selected))), orientation='h',
        customdata=selected[label].tolist(),
        marker_color=['#b84655' if v < 0 else '#5470c6' for v in selected[measure]],
        hovertemplate='%{customdata}<br>%{x:,.2f}<extra></extra>')))
    figure.update_yaxes(tickmode='array', tickvals=list(range(len(selected))), ticktext=selected[label].tolist())
    figure.update_xaxes(title=measure, zeroline=True, zerolinewidth=2, zerolinecolor='#888888')
    figure.update_layout(height=max(260, min(900, len(selected)*32)), margin=dict(l=10, r=10, t=10, b=35))
    st.plotly_chart(figure, key=key, width='stretch', config=chart_config())


def render_company_move(result, rows):
    from portfolio_app.exposure_presentation import company_move
    panel = st.expander('Company move', key='exposure_company_open', on_change='rerun')
    if not panel.open:
        return
    with panel:
        candidates = rows.loc[rows.exposure_reporting.notna() & rows.exposure_reporting.ne(0)
                              & ~rows.status.str.contains('Unresolved|Unknown|Funding|Non-equity', case=False)]
        names = candidates.groupby('asset_id').asset_name.first().to_dict()
        if not names:
            st.info('No identified company exposures in this selection.')
            return
        asset = st.selectbox('Company', sorted(names, key=lambda key: names[key]), index=None,
            format_func=lambda key: names[key], key='exposure_company_move', placeholder='Search for a company')
        change = st.number_input('Price change (%)', min_value=-100., value=-5., step=1., key='exposure_company_change')
        if asset is not None:
            pnl, impact = company_move(result, asset, change, rows=rows)
            left, right = st.columns(2)
            left.metric('Estimated P&L', format_money(pnl, result.reporting_currency))
            right.metric('Portfolio impact', '—' if impact is None else f'{impact:+.2f}%')
            sources = rows.loc[rows.asset_id.eq(asset)].copy()
            sources['Channel'] = sources.source_type.map(lambda kind: 'Futures proxy' if kind == 'future' else 'ETF holdings' if kind.startswith('etf_') else 'Direct holdings')
            table = sources.groupby('Channel').exposure_reporting.sum().rename(f'Exposure ({result.reporting_currency})').reset_index()
            table[f'Estimated P&L ({result.reporting_currency})'] = table[f'Exposure ({result.reporting_currency})'] * change / 100
            st.dataframe(table, hide_index=True, width='stretch')
        st.caption('Identified exposure only. Isolated company price move with other prices and FX unchanged. Futures contribution uses approximate index weights; this is not a forecast of correlated market moves.')


def render_exposure_targets(directory, config, result):
    st.subheader('Exposure targets')
    if config is None or not config.leaves():
        st.info('Save strategic categories first to configure exposure targets using stable category IDs.')
        return
    try:
        saved, revision = load_exposure_targets(directory, config)
    except DataError as exc:
        st.error(str(exc))
        return
    if saved:
        st.caption(f'Saved policy: {saved.universe} exposure · {saved.multiple:g}× whole-portfolio net equity')
        labels = {'': 'Portfolio', **category_labels(config)}
        scope = st.selectbox('Target hierarchy', list(labels), format_func=labels.get, key='exposure_control_target_hierarchy')
        try:
            st.dataframe(exposure_gaps(result, saved, config, parent=scope).drop(columns='Bucket ID'), hide_index=True, width='stretch')
            st.caption('Gap = desired − current exposure; percentages use net equity. These are exposure gaps, not purchase budgets.')
        except DataError as exc:
            st.warning(str(exc))
    else:
        st.info('Exposure targets are unset. Existing capital targets are unchanged.')
    labels = category_labels(config)
    universe = st.selectbox('Target exposure universe', ['equity', 'market'], index=['equity', 'market'].index(saved.universe) if saved else 1,
        format_func=lambda value: {'equity': 'Equity only', 'market': 'Total market (including gold and crypto)'}[value], key='exposure_control_universe_' + str(revision))
    weights = st.selectbox('Composition weights', ['leaves', 'parent_relative'], index=['leaves', 'parent_relative'].index(saved.weights) if saved else 1,
        format_func=lambda value: {'leaves': 'Leaf shares of total exposure', 'parent_relative': 'Category shares within each parent'}[value], key='exposure_control_weights_' + str(revision))
    converting = saved is not None and (universe != saved.universe or weights != saved.weights)
    if converting:
        st.warning('Saving explicitly replaces the exposure universe or weight convention. Enter the new weights; saved capital targets stay unchanged.')
    leaves = sorted(config.leaves()) if weights == 'leaves' else [b.id for b in config.buckets]
    with st.form('exposure_target_form_' + str(revision)):
        policy = st.selectbox('Exposure policy', ['composition', 'overlay_only'], index=(['composition', 'overlay_only'].index(saved.policy) if saved else None),
            format_func=lambda key: {'composition': 'Category composition', 'overlay_only': 'Keep funded allocation; overlay in one category'}[key])
        multiple = st.number_input('Desired total exposure (× net equity)', min_value=0., value=saved.multiple if saved else None, step=.05)
        st.caption('Composition sets category shares of the declared exposure universe. Parent-relative weights multiply down the hierarchy; each sibling group totals 100%. '
                   'Overlay-only retains funded exposures and puts the remainder in one leaf. Targets remain nonnegative.')
        frame = pd.DataFrame({'Bucket ID': leaves, 'Category': [labels[x] for x in leaves],
                              'Exposure composition (%)': [saved.composition.get(x, 0.)*100 if saved and not converting and saved.policy == 'composition' else None for x in leaves]})
        from portfolio_app.view_state import persistent_editor
        edited = persistent_editor(frame, key='exposure_composition_' + str(revision) + universe + weights, hide_index=True, disabled=['Bucket ID', 'Category'], width='stretch',
            column_config={'Exposure composition (%)': st.column_config.NumberColumn(min_value=0., max_value=100.)})
        overlay = st.selectbox('Overlay destination (overlay-only policy)', sorted(config.leaves()),
                              index=sorted(config.leaves()).index(saved.overlay_bucket) if saved and saved.overlay_bucket in config.leaves() else None, format_func=labels.get)
        save = st.form_submit_button('Save exposure targets')
        clear = st.form_submit_button('Clear exposure targets')
    if save or clear:
        try:
            if clear and load_leverage_settings(directory)[0].active_basis == 'exposure':
                raise DataError('Activate the capital policy before clearing the active exposure policy.')
            if save and (multiple is None or policy is None):
                raise DataError('Choose an exposure policy and total multiple.')
            composition = {row['Bucket ID']: float(row['Exposure composition (%)']) for row in edited.to_dict('records') if pd.notna(row['Exposure composition (%)'])}
            if save and policy == 'composition' and len(composition) != len(leaves):
                raise DataError('Set every leaf composition explicitly, including zeros.')
            target = None if clear else ExposureTargets(float(multiple), policy, {k: v/100 for k, v in composition.items()} if policy == 'composition' else {}, overlay or '' if policy == 'overlay_only' else '', version=2, universe=universe, weights=weights)
            save_exposure_targets(directory, target, config, revision)
        except (DataError, OSError) as exc:
            st.error(str(exc))
        else:
            st.rerun()


def render_leverage_editor(directory, valued, *, future=None):
    with st.expander('Recorded liabilities & leveraged funds'):
        settings, revision = load_leverage_settings(directory, future=future)
        st.caption('Record current EUR debt separately from owned assets. Do not record a liability twice. '
                   'Blank debt balances mean unknown. Factors apply only to explicitly selected funds; no ticker/name inference. '
                   'Document a current daily equity factor and date, or leave its factor blank for unknown embedded leverage.')
        from dataclasses import asdict
        with st.form('exposure_leverage_form_' + str(revision)):
            debts = st.data_editor(pd.DataFrame([asdict(x) for x in settings.liabilities], columns=['id', 'name', 'balance_eur', 'account']),
                num_rows='dynamic', hide_index=True, key='exposure_debts_' + str(revision), column_config={'balance_eur': st.column_config.NumberColumn(min_value=0.)})
            positions = valued.loc[valued.get('instrument_type', pd.Series('', index=valued.index)).eq('etf')]
            factors = st.data_editor(pd.DataFrame([asdict(x) for x in settings.fund_factors], columns=['position_id', 'factor', 'method', 'as_of', 'benchmark', 'proxy_position_id', 'reset_convention']),
                num_rows='dynamic', hide_index=True, key='exposure_factors_' + str(revision),
                column_config={'position_id': st.column_config.SelectboxColumn(options=positions.position_id.tolist()), 'factor': st.column_config.NumberColumn()})
            st.caption('Fund source IDs: ' + '; '.join(f'{r.position_id}: {r.name}' for r in positions.itertuples()))
            save = st.form_submit_button('Save leverage settings')
        if save:
            try:
                def clean(frame):
                    return [{k: None if pd.isna(v) else v for k, v in row.items()} for row in frame.to_dict('records')]
                liabilities = tuple(MarginLiability(**row) for row in clean(debts))
                adapters = tuple(FundFactor(**{**row, 'method': row['method'] or '', 'as_of': row['as_of'] or ''}) for row in clean(factors))
                if {x.position_id for x in adapters} - set(positions.position_id):
                    raise DataError('Choose an existing fund source position.')
                from dataclasses import replace
                save_leverage_settings(directory, replace(settings, liabilities=liabilities, fund_factors=adapters), revision)
            except (DataError, OSError, TypeError) as exc:
                st.error(str(exc))
            else:
                st.rerun()


def render_policy_settings(directory, settings, config, *, future=None, planning=False):
    from dataclasses import replace
    settings, revision = load_leverage_settings(directory, future=future)
    with st.expander('Active target policy' if planning else 'Leverage & target policy', expanded=planning):
        with st.form(('planning_policy_form_' if planning else 'targets_policy_form_') + str(revision)):
            required = requires_leverage(settings, future)
            enabled = st.checkbox('Enable leverage', value=settings.enabled, disabled=required or planning)
            if required and not planning:
                st.caption('Existing futures, liabilities or fund factors require leverage controls.')
            basis = st.selectbox('Active planning basis', ['capital', 'exposure'], index=['capital', 'exposure'].index(settings.active_basis),
                                 format_func=lambda value: 'Capital allocation' if value == 'capital' else 'Market / equity exposure') if settings.enabled else 'capital'
            st.caption('Changing chart measure or editing the optional policy does not change this active basis.')
            save = st.form_submit_button('Save active policy')
        if save:
            try:
                if basis == 'exposure':
                    if config is None or load_exposure_targets(directory, config)[0] is None:
                        raise DataError('Save an exposure target policy first, then activate it.')
                    if not enabled:
                        raise DataError('Enable leverage to activate an exposure policy.')
                save_leverage_settings(directory, replace(settings, enabled=enabled, active_basis=basis), revision)
            except (DataError, OSError) as exc:
                st.error(str(exc))
            else:
                st.rerun()
