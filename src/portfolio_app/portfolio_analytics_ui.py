"""Analytics within Overview's existing category navigation and visual style."""

from portfolio_app.currency_display import currency_symbol, reporting_currency
import pandas as pd
import streamlit as st

from portfolio_app.analytics import snapshot_analytics
from portfolio_app.analytics_service import load_metrics, load_risk
from portfolio_app.analytics_ui import context_key, data_quality_caption, display_value, render_sources, risk_settings
from portfolio_app.charts import correlation_chart
from portfolio_app.company_merges import build_plan, load_company_names, load_settings
from portfolio_app.display_names import instrument_name
from portfolio_app.fundamentals import Metric
from portfolio_app.holdings import DataError
from portfolio_app.stock_exposure import load_company_identities, stock_exposure


def coverage(metric, universe):
    return (f'{100 * metric.covered_value / metric.eligible_value:.0f}% of {universe} value covered'
            if metric.eligible_value > 0 else f'No valued {universe} in this scope')


def render_portfolio_analytics(valued, data_dir, funds, *, demo=False, scope='Portfolio'):
    key = context_key(data_dir, demo) + '_overview'
    title, options = st.columns([5, 1], vertical_alignment='center')
    title.markdown('**Portfolio analytics**')
    with options, st.popover('Options', help='Choose accounts and market-data options for these analytics.', icon=':material/tune:', width='stretch'):
        accounts = sorted(valued.account.unique())
        # Category changes must not retain selections from a different universe.
        scope_key = key + '_' + str(tuple(accounts))
        selected_accounts = st.multiselect('Accounts', accounts, help='Limit analytics to holdings in the selected accounts.', default=accounts, key=scope_key + '_accounts')
        benchmark, years = risk_settings(key)
        refresh = st.button('Refresh fundamentals', help='Request fresh public company fundamentals for this analysis.', icon=':material/refresh:', disabled=demo, key=key + '_refresh')
    selected = valued.loc[valued.account.isin(selected_accounts) & valued.shares.gt(0)].copy()
    if selected.empty:
        st.info('No held positions in this category and account selection.')
        return
    st.caption(f'{scope} · {len(selected)} held positions' + (' · Account filter active' if set(selected_accounts) != set(accounts) else ''))
    with st.container(key='tour_risk'):
        with st.container(key='tour_risk_controls'):
            heading, action = st.columns([4, 1], vertical_alignment='center')
            heading.markdown('**Historical risk**')
            heading.caption(f'{benchmark or "Choose a benchmark in Options"} · {years} years · Weekly {reporting_currency()} returns')
            from portfolio_app.tour import active
            loaded = st.session_state.get(key + '_risk_loaded', False) or (demo and active())
            calculate = action.button('Refresh risk' if loaded else 'Calculate risk', help='Calculate historical risk from public price data for this selection; no holdings are changed.', icon=':material/refresh:' if loaded else ':material/analytics:',
                                      type='secondary', disabled=not benchmark, key=key + '_risk_calculate', width='stretch')
        if calculate:
            st.session_state[key + '_risk_loaded'] = True
            loaded = True
        if loaded and benchmark:
            with st.spinner('Calculating historical risk…'):
                risk, status = load_risk(selected, data_dir, demo, benchmark, years, calculate and not demo)
            render_risk_dashboard(risk, status, selected)
        else:
            st.caption('Estimate beta, volatility and diversification for today’s allocation. Uses market-price history; not personal historical returns.')
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
        st.warning(f'{summary.missing_valuations} positions have no current valuation. Percentages below use known valued assets.')
    data_quality_caption(snapshots)
    pe, cost, income = (summary.metrics[name] for name in ('trailing_pe', 'fee_reporting', 'distribution_yield'))
    columns = st.columns(3)
    with columns[0]:
        st.metric('Direct-stock P/E', display_value(Metric(pe.value, 'ratio'), 'trailing_pe'),
                  help='Value-weighted earnings yield, inverted. Profitable direct equities only; fund-reported ratios are kept separate.')
        st.caption(coverage(pe, 'direct-equity'))
    with columns[1]:
        st.metric('Known annual fund costs', '—' if cost.value is None else (f'€{cost.value:,.2f}').replace('€', currency_symbol()),
                  help='Current fund values × annual fees. Already reflected in fund prices; not deducted again from gains.')
        st.caption(coverage(cost, 'fund'))
    with columns[2]:
        st.metric('Trailing cash yield', display_value(Metric(income.value, 'fraction'), 'distribution_yield'),
                  help='Trailing stock dividends and fund cash distributions for covered holdings. Not a forecast or income received; accumulating share classes pay no cash distributions.')
        st.caption(coverage(income, 'equity and fund'))
    st.markdown('**Concentration**')
    columns = st.columns(3)
    columns[0].metric('Largest holding', '—' if summary.largest_weight is None else f'{summary.largest_weight:.1%}', help='Largest instrument weight in the selected portfolio; repeated instrument IDs are combined.')
    columns[1].metric('Top five', '—' if summary.top_five_weight is None else f'{summary.top_five_weight:.1%}', help='Combined weight of the five largest instruments in this selection.')
    columns[2].metric('Effective holdings', '—' if summary.effective_holdings is None else f'{summary.effective_holdings:.1f}',
                      help='1 / sum of squared value weights. Repeated instrument IDs are combined; fund wrappers count as holdings here.')
    with st.expander('Underlying company exposure'):
        render_company_exposure(selected, snapshots, funds, data_dir)
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


def render_company_exposure(valued, snapshots, funds, data_dir):
    try:
        identities = load_company_identities(data_dir / 'company-identities.yaml')
        typed = valued.copy()
        typed['instrument_type'] = [snapshots[row.id].kind for row in typed.itertuples()]
        plan = build_plan(typed, funds, identities, load_settings(data_dir / 'company-merges.yaml'), {}, load_company_names(data_dir / 'company-names.yaml'))
        company_positions, company_funds, _ = plan.apply(typed, funds, {})
        company = stock_exposure(company_positions, company_funds, identities=identities)
        st.dataframe(company.companies.head(10), hide_index=True, width='stretch')
        if not company.unresolved.empty:
            st.caption('Unresolved look-through exposure is excluded from the named-company ranking.')
            st.dataframe(company.unresolved, hide_index=True, width='stretch')
        for fund in company_funds:
            if fund.proxy_source:
                st.caption(f'{fund.name}: {fund.proxy_source}')
    except DataError as exc:
        st.error(str(exc))


def render_risk_dashboard(result, status, holdings):
    with st.container(key='tour_risk_metrics'):
        ratio = result.covered_value / result.known_value if result.known_value > 0 else None
        denominator = 'portfolio value' if result.valuation_complete else 'known valued assets; whole-portfolio coverage unavailable'
        st.caption(f'History covers {ratio:.1%} of {denominator}' if ratio is not None else 'No valued assets with history.')
        if result.status == 'partial':
            st.warning('Covered-subportfolio estimate: weights are renormalized within holdings with usable history.')
        if result.status == 'unavailable':
            st.info(result.note)
        columns = st.columns(3)
        columns[0].metric('Beta', '—' if result.beta is None else f'{result.beta:.2f}', help=f'Sensitivity to the selected benchmark on the common weekly {reporting_currency()} sample.')
        columns[1].metric('Annualized volatility', '—' if result.volatility is None else f'{result.volatility:.2%}', help='Historical variability estimated from common weekly reporting-currency returns and annualized; today’s portfolio weights are held constant.')
        columns[2].metric('Benchmark correlation', '—' if result.correlation is None else f'{result.correlation:.2f}', help='Correlation with the selected benchmark over the common weekly reporting-currency return sample, from -1 to 1.')
        if result.start:
            st.caption(f'{result.observations} common weekly returns · {result.start} to {result.end} · Today’s weights held constant, not personal historical performance.')
    if not result.holdings.empty:
        names = {row.id: instrument_name(row) for row in holdings.itertuples()}
        frame = result.holdings.copy()
        frame.insert(0, 'Investment', [names.get(key, key) for key in frame.index])
        labels = {'Weight': 'Allocation (%)', 'Beta': 'Beta', 'Annual volatility': 'Volatility (%)',
                  'Volatility contribution': 'Volatility contribution (pp)', 'Risk share': 'Share of risk (%)'}
        for field, label in labels.items():
            if field != 'Beta':
                frame[label] = 100 * frame.pop(field)
        st.dataframe(frame, hide_index=True, width='stretch', column_config={label: st.column_config.NumberColumn(format='%.2f') for label in labels.values()})
        st.caption('Contributions sum to portfolio volatility. Negative contributions indicate diversification; cash correlations are undefined.')
        with st.expander('Holding correlations'):
            figure = correlation_chart(result.correlations, names)
            st.plotly_chart(figure, width='stretch', config={'displayModeBar': False})
    if status.Status.eq('stale').any():
        st.warning('Risk estimates include cached fallback market or FX histories.')
    with st.expander('Risk coverage & sources'):
        if result.excluded:
            st.dataframe(pd.DataFrame(result.excluded.items(), columns=['Instrument', 'Reason']), hide_index=True)
        st.dataframe(status, hide_index=True, width='stretch')
