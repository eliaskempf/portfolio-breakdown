"""Analytics within Overview's existing category navigation and visual style."""
from portfolio_app.chart_hover import chart_hover

from portfolio_app.currency_display import money, reporting_currency
import pandas as pd
import streamlit as st
from portfolio_app.charts import chart_config

from portfolio_app.analytics import snapshot_analytics
from portfolio_app.analytics_service import load_metrics, load_risk
from portfolio_app.analytics_ui import benchmark_name, context_key, data_quality_caption, display_value, render_sources, risk_settings
from portfolio_app.charts import correlation_chart
from portfolio_app.company_analysis import load_company_analysis
from portfolio_app.display_names import instrument_name
from portfolio_app.fundamentals import Metric
from portfolio_app.holdings import DataError


def coverage(metric, universe):
    return (f'{100 * metric.covered_value / metric.eligible_value:.0f}% of {universe} covered'
            if metric.eligible_value > 0 else f'No valued {universe}')


def render_portfolio_analytics(valued, data_dir, funds, *, demo=False, scope='Portfolio', future=None, portfolio_result=None, scoped=False, portfolio_net_equity=None):
    key = context_key(data_dir, demo) + '_overview'
    with st.container(key='tour_risk'):
        with st.container(key='tour_risk_controls'):
            heading, action, options = st.columns([4, 1, 1], vertical_alignment='center')
            heading.markdown('**Historical risk**')
            with options, st.popover('Options', help='Choose accounts and market-data options for these analytics.', icon=':material/tune:', width='stretch'):
                accounts = sorted(valued.account.unique())
                future_account = '__future_overlay__'
                while future_account in accounts:
                    future_account += '_'
                if future is not None:
                    accounts.append(future_account)
                # Category changes must not retain selections from a different universe.
                scope_key = key + '_' + str(tuple(accounts))
                selected_accounts = st.multiselect('Accounts', accounts, default=accounts, key=scope_key + '_accounts',
                    format_func=lambda value: 'Futures position · no account assigned' if value == future_account else value)
                benchmark, years = risk_settings(key)
                refresh = st.button('Refresh fundamentals', help='Request fresh public company fundamentals for this analysis.', icon=':material/refresh:', disabled=demo, key=key + '_refresh')
            selected = valued.loc[valued.account.isin(selected_accounts) & valued.shares.gt(0)].copy()
            scope_active = scoped or set(selected_accounts) != set(accounts)
            risk_future = future if future_account in selected_accounts else None
            if selected.empty and risk_future is None:
                st.info('No held positions in this category and account selection.')
                return
            if set(selected_accounts) != set(accounts):
                heading.caption(f'{scope} · Account filter active')
            benchmark_slot = heading.container()
            from portfolio_app.tour import active
            loaded = st.session_state.get(key + '_risk_loaded', False) or (demo and active())
            calculate = action.button('Refresh risk' if loaded else 'Calculate risk', help='Calculate historical risk from public price data for this selection; no holdings are changed.', icon=':material/refresh:' if loaded else ':material/analytics:',
                                      type='secondary', disabled=not benchmark, key=key + '_risk_calculate', width='stretch')
        if calculate:
            st.session_state[key + '_risk_loaded'] = True
            loaded = True
        benchmark_symbol = benchmark
        if loaded and benchmark:
            with st.spinner('Calculating historical risk…'):
                risk, status = load_risk(selected, data_dir, demo, benchmark, years, calculate and not demo,
                    future=risk_future, portfolio_result=portfolio_result, scoped=scope_active,
                    portfolio_net_equity=portfolio_net_equity)
            benchmark_symbol = status.attrs.get('benchmark_symbol', benchmark)
            render_risk_dashboard(risk, status, selected, future=risk_future, scoped=scope_active)
        else:
            st.caption('Calculate to view risk and holdings correlations.')
        with benchmark_slot:
            render_benchmark(benchmark_symbol, years)
    if selected.empty:
        return
    st.divider()
    st.markdown('**Valuation & income**')
    try:
        with st.spinner('Loading analytics…'):
            snapshots = load_metrics(selected, data_dir, demo=demo, refresh=refresh)
    except DataError as exc:
        st.error(str(exc))
        return
    summary = snapshot_analytics(selected, snapshots)
    if not summary.valuation_complete:
        st.caption(f'{summary.missing_valuations} unvalued · Percentages use known values')
    data_quality_caption(snapshots)
    pe, cost, income = (summary.metrics[name] for name in ('trailing_pe', 'fee_reporting', 'distribution_yield'))
    columns = st.columns(3)
    with columns[0]:
        st.metric('Direct-stock P/E', display_value(Metric(pe.value, 'ratio'), 'trailing_pe'),
                  help='Value-weighted earnings yield, inverted. Profitable direct equities only; fund-reported ratios are kept separate.')
        st.caption(coverage(pe, 'direct-equity'))
    with columns[1]:
        st.metric('Known annual fund costs', '—' if cost.value is None else money(cost.value),
                  help='Current fund values × annual fees. Already reflected in fund prices; not deducted again from gains.')
        st.caption(coverage(cost, 'fund'))
    with columns[2]:
        st.metric('Trailing cash yield', display_value(Metric(income.value, 'fraction'), 'distribution_yield'),
                  help='Trailing stock dividends and fund cash distributions for covered holdings. Not a forecast or income received; accumulating share classes pay no cash distributions.')
        st.caption(coverage(income, 'equity and fund'))
    with st.expander('Valuation, income & fee details'):
        names = {'trailing_pe': 'Trailing P/E · direct stocks', 'forward_pe': 'Forward P/E · direct stocks',
                 'fee': 'Weighted annual fee · covered funds', 'fee_reporting': 'Known annual fund costs',
                 'distribution_yield': 'Trailing cash yield', 'distribution_yield_reporting': 'Annualized trailing distributions'}
        rows = []
        for name, metric in summary.metrics.items():
            unit = reporting_currency() if name.endswith('_reporting') else 'ratio' if name.endswith('_pe') else 'fraction'
            rows.append({'Metric': names[name], 'Value': display_value(Metric(metric.value, unit), name),
                         'Covered value': metric.covered_value, 'Eligible value': metric.eligible_value,
                         'Missing / nonmeaningful positions': metric.excluded_count})
        st.dataframe(pd.DataFrame(rows), hide_index=True, width='stretch')
        st.caption('Missing metrics stay unavailable. Loss-making equities are excluded from P/E. Fees are already reflected in prices; '
                   'trailing distributions are not a forecast. Buy-in performance is available in the Performance view.')
    with st.expander('Fundamental sources & data quality'):
        render_sources(snapshots)
    st.markdown('**Concentration**')
    columns = st.columns(3)
    columns[0].metric('Largest holding', '—' if summary.largest_weight is None else f'{summary.largest_weight:.1%}', help='Largest instrument weight in the selected portfolio; repeated instrument IDs are combined.')
    columns[1].metric('Top five', '—' if summary.top_five_weight is None else f'{summary.top_five_weight:.1%}', help='Combined weight of the five largest instruments in this selection.')
    columns[2].metric('Effective holdings', '—' if summary.effective_holdings is None else f'{summary.effective_holdings:.1f}',
                      help='1 / sum of squared value weights. Repeated instrument IDs are combined; fund wrappers count as holdings here.')
    with st.expander('Underlying company exposure'):
        render_company_exposure(selected, snapshots, funds, data_dir)


def render_company_exposure(valued, snapshots, funds, data_dir):
    try:
        prepared = load_company_analysis(valued, snapshots, funds, data_dir)
        company = prepared.exposure
        st.dataframe(company.companies.head(10), hide_index=True, width='stretch')
        if not company.unresolved.empty:
            st.caption('Unresolved look-through exposure is excluded from the named-company ranking.')
            st.dataframe(company.unresolved, hide_index=True, width='stretch')
        for note in prepared.proxy_notes:
            st.caption(note)
    except DataError as exc:
        st.error(str(exc))


def render_benchmark(symbol, years):
    label = benchmark_name(symbol) or 'Choose a benchmark in Options'
    with st.popover(f'{label} · {reporting_currency()} · {years} years', help='Benchmark and return methodology'):
        st.markdown(f'**{label}**')
        if symbol.startswith('MSCI:892400:NETR'):
            st.write('Direct MSCI ACWI net total return index levels in the portfolio reporting currency. '
                     'Dividends are reinvested after index-assumed withholding taxes; ETF fees and personal taxes are not deducted.')
            st.link_button('MSCI ACWI index', 'https://www.msci.com/indexes/index/892400/msci-acwi-index')
        elif symbol == 'IUSQ.DE':
            st.caption('iShares MSCI ACWI UCITS ETF USD (Acc) · IUSQ.DE')
            st.write('Adjusted ETF history, including fund costs and tracking differences. '
                     'The ETF tracks the MSCI ACWI Net Return index; these are ETF returns, not direct index levels.')
        st.write(f'Weekly returns in {reporting_currency()}. Currency conversion does not hedge currency risk. '
                 'At least 52 common weekly observations are required.')


def render_risk_details(result, status, names, *, scoped=False):
    """Keep exclusions and calculation basis one action from the status line."""
    st.markdown('**Risk coverage & sources**')
    if result.note:
        st.write(result.note)
    denominator = ('modeled weight amounts (absolute)' if result.integrated else
                   'portfolio value' if result.valuation_complete else
                   'known valued assets; whole-portfolio coverage unavailable')
    st.caption(f'Coverage basis: {denominator}.')
    if result.start:
        st.caption(f'{result.observations} common weekly returns · {result.start} to {result.end}')
    st.write('Today’s weights held constant; not personal historical performance. '
             'Excluded positions may increase or decrease actual risk.')
    if scoped:
        st.write('Subset risk uses covered selected holdings’ net value, or selected capital backing futures. '
                 'Futures retain signed notional weights; margin is not used as capital. '
                 'Portfolio beta contribution uses whole-portfolio net equity and this subset’s common sample.')
    elif result.integrated:
        st.write('Risk weights use whole-portfolio net equity; debt reduces that denominator. '
                 'Futures use signed notional with identified contract-currency proxies. '
                 'Foreign cash, debt and unsettled claims include FX risk. '
                 'Fund price histories already include their embedded leverage.')
    if result.excluded:
        st.dataframe(pd.DataFrame([(names.get(key, key), reason) for key, reason in result.excluded.items()],
                                  columns=['Investment', 'Reason']), hide_index=True, width='stretch')
    st.dataframe(status, hide_index=True, width='stretch')


def render_risk_dashboard(result, status, holdings, *, future=None, scoped=False):
    names = {row.id: instrument_name(row) for row in holdings.itertuples()}
    if future is not None:
        names['future:overlay'] = f'{future.instrument} future · index proxy'
    with st.container(key='tour_risk_metrics'):
        columns = st.columns(3)
        columns[0].metric('Subset beta' if scoped else 'Beta', '—' if result.beta is None else f'{result.beta:.2f}',
            help=f'Sensitivity to the selected benchmark on one common weekly {reporting_currency()} sample. Subset risk uses selected capital; whole-portfolio risk uses portfolio net equity. Foreign cash and debt include FX risk.')
        if scoped:
            contribution = '—' if result.portfolio_beta_contribution is None else f'{result.portfolio_beta_contribution:.2f}'
            columns[0].caption(f'Portfolio contribution **{contribution}**',
                help='Contribution to portfolio beta, using whole-portfolio net equity and the selected common sample; excludes holdings without usable history.')
        columns[1].metric('Subset volatility (annualized)' if scoped else 'Annualized volatility', '—' if result.volatility is None else f'{result.volatility:.2%}',
            help='Standard deviation of the selected weekly return series, annualized with the square root of 52.')
        columns[2].metric('Benchmark correlation', '—' if result.correlation is None else f'{result.correlation:.2f}',
            help='Co-movement with the benchmark, from −1 to +1. Uses the same selected return series and common sample.')
        ratio = result.covered_value / result.known_value if result.known_value > 0 else None
        state = {'partial': ':orange[●] Partial estimate', 'unavailable': 'Estimate unavailable'}.get(result.status, 'Historical estimate')
        parts = [state, f'{ratio:.0%} coverage' if ratio is not None else 'Coverage unavailable']
        if not result.valuation_complete:
            parts.append('Incomplete valuation')
        if result.excluded:
            parts.append(f'{len(result.excluded)} excluded')
        if status.attrs.get('benchmark_fallback'):
            parts.append('ETF benchmark fallback')
        if status.Status.eq('stale').any():
            parts.append('Cached fallback data')
        summary, details = st.columns([5, 1], vertical_alignment='center')
        summary.caption(' · '.join(parts))
        with details.popover('Details', help='Coverage, excluded holdings, methodology and sources', width='stretch'):
            render_risk_details(result, status, names, scoped=scoped)
        if result.status == 'unavailable':
            # A blocked estimate needs its reason without requiring exploration.
            st.caption(result.note)
    if not result.holdings.empty:
        correlations = result.correlations.dropna(how='all').dropna(axis=1, how='all')
        with st.container(key='risk_correlations'):
            heading, expand = st.columns([4, 1], vertical_alignment='center')
            heading.markdown('**Holdings correlations**', help='Pairwise correlations on the same weekly sample. Positions with no defined correlations are omitted here but remain in the risk calculations. Foreign-currency cash retains its FX correlations; blank cells are not zero.')
            if correlations.empty:
                st.caption('No varying returns to compare.')
            else:
                expanded = expand.toggle('Expand', key='analytics_correlations_expanded', help='Enlarge the correlation matrix and show full investment names.')
                figure = correlation_chart(correlations, names, compact=not expanded)
                st.plotly_chart(chart_hover(figure), width='stretch', config=chart_config())
        frame = result.holdings.copy()
        frame.insert(0, 'Investment', [names.get(key, key) for key in frame.index])
        labels = {'Weight': 'Weight / subset capital (%)' if scoped else 'Weight / net equity (%)' if result.integrated else 'Allocation (%)',
                  'Annual volatility': 'Volatility (%)', 'Volatility contribution': 'Volatility contribution (pp)',
                  'Risk share': 'Share of risk (%)', 'Beta contribution': 'Contribution to subset beta' if scoped else 'Beta contribution'}
        for field in ('Weight', 'Annual volatility', 'Volatility contribution', 'Risk share'):
            frame[field] *= 100
        heading, options = st.columns([5, 1], vertical_alignment='center')
        heading.markdown('**Holdings risk**', help='Volatility contributions sum to the displayed estimate. Negative contributions indicate diversification.')
        with options.popover('Columns', width='stretch'):
            visible = st.multiselect('Risk columns', [field for field in frame.columns if field != 'Investment'],
                default=['Weight', 'Beta', 'Annual volatility', 'Risk share'], format_func=lambda field: labels.get(field, field),
                key='analytics_risk_columns')
        frame = frame.rename(columns=labels)
        st.dataframe(frame, hide_index=True, width='stretch',
            column_order=['Investment', *[labels.get(field, field) for field in visible]],
            column_config={field: st.column_config.NumberColumn(format='%.2f') for field in frame.columns if field != 'Investment'})
