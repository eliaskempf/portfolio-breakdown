"""Optional analytics controls; source-position inputs remain independent of charts."""
from datetime import date
from hashlib import sha256

import pandas as pd
import plotly.express as px
import streamlit as st

from portfolio_app.analytics import metric_value, snapshot_analytics
from portfolio_app.company_merges import build_plan, load_company_names, load_settings
from portfolio_app.fundamentals import (DEFINITIONS, PUBLIC_FEES, DemoFundamentalsProvider, Fundamentals,
    FundamentalsService, FundFee, Metric, YahooFundamentalsProvider, apply_fund_metadata, fee_key,
    load_fee_overrides, finite, save_fee_override)
from portfolio_app.holdings import DataError
from portfolio_app.performance import position_performance
from portfolio_app.prices import PriceService, StaticProvider, YahooProvider
from portfolio_app.risk import DEFAULT_BENCHMARK, risk_analytics
from portfolio_app.risk_data import DemoRiskHistoryProvider, RiskHistoryService, YahooRiskHistoryProvider
from portfolio_app.stock_exposure import load_company_identities, stock_exposure
from portfolio_app.valuation import value_holdings


def context_key(data_dir, demo=False):
    return 'analytics_' + sha256(f'{data_dir.resolve()}:{demo}'.encode()).hexdigest()[:12]


def valued_positions(holdings, data_dir, demo=False, price_service=None):
    service = price_service or PriceService(StaticProvider(data_dir / 'demo_prices.json') if demo else
                    YahooProvider(data_dir / '.cache' / 'yahoo'), None if demo else data_dir / '.cache' / 'prices.json')
    valued = value_holdings(holdings, service)
    rates = {}
    for currency in set(valued.get('acquisition_currency', pd.Series(dtype=str)).dropna()) - {'', 'EUR'}:
        result = service.fx(currency)
        if result.quote and result.quote.currency == 'EUR':
            rates[currency] = result.quote.price
    return position_performance(valued, rates)


def load_metrics(holdings, data_dir, *, demo=False, fetch=True, refresh=False):
    provider = DemoFundamentalsProvider() if demo else YahooFundamentalsProvider()
    service = FundamentalsService(provider, None if demo else data_dir / '.cache' / 'fundamentals')
    overrides = load_fee_overrides(data_dir / 'fund-fees.json')
    result, listings = {}, {}
    for row in holdings.drop_duplicates('id').to_dict('records'):
        kind = row.get('instrument_type') or 'unknown'
        ticker = row['ticker']
        if fetch and kind in {'equity', 'etf', 'unknown'} and ticker:
            if ticker not in listings:
                listings[ticker] = service.get(ticker, refresh=refresh)
            snapshot = listings[ticker]
        else:
            snapshot = Fundamentals(ticker, kind)
        if demo and (kind == 'etf' or row.get('isin') in PUBLIC_FEES):
            snapshot = Fundamentals(ticker, 'etf', {
                'fee': Metric(.0025, 'fraction', 'Synthetic demo'),
                'distribution_yield': Metric(.01, 'fraction', 'Synthetic demo'),
                'fund_pe': Metric(22., 'ratio', 'Synthetic demo'),
                'fund_pb': Metric(2.5, 'ratio', 'Synthetic demo'),
                'fund_assets': Metric(5e8, 'EUR', 'Synthetic demo')}, 'fresh', note='Synthetic demo fund metrics')
            # Avoid presenting public issuer fees as invented demo observations.
            synthetic = dict(overrides)
            synthetic.setdefault(fee_key(row), FundFee(.0025, 'Synthetic demo', date.today().isoformat()))
            snapshot = apply_fund_metadata(snapshot, row, synthetic)
        else:
            snapshot = apply_fund_metadata(snapshot, row, overrides)
        result[row['id']] = snapshot
    return result


def load_risk(valued, data_dir, demo, benchmark, years, refresh=False):
    service = RiskHistoryService(DemoRiskHistoryProvider() if demo else YahooRiskHistoryProvider(),
                                 None if demo else data_dir / '.cache' / 'risk-history')
    memo, histories, failures, status = {}, {}, {}, []
    bench = service.eur(benchmark, years, refresh=refresh, memo=memo)
    status.append(dict(Instrument=benchmark, Status=bench.status, Retrieved=bench.fetched_at, Note=bench.note))
    for identity, rows in valued.loc[valued.shares.gt(0)].groupby('id'):
        row = rows.iloc[0]
        if row.get('instrument_type') == 'cash' and row.quote_currency == 'EUR':
            continue
        if not row.ticker or pd.notna(row.get('manual_price', float('nan'))):
            failures[identity] = 'no supported history for a manual or unlisted asset'
            continue
        history = service.eur(row.ticker, years, refresh=refresh, memo=memo)
        status.append(dict(Instrument=row.ticker, Status=history.status, Retrieved=history.fetched_at, Note=history.note))
        if not history.prices.empty:
            histories[identity] = history.prices
        else:
            failures[identity] = history.note or 'history unavailable'
    result = risk_analytics(valued, histories, bench.prices, years=years, failures=failures)
    return result, pd.DataFrame(status).drop_duplicates('Instrument')


def risk_controls(key):
    left, right = st.columns(2)
    benchmark = left.text_input('Risk benchmark ticker', DEFAULT_BENCHMARK, key=key + '_benchmark').strip().upper()
    years = right.selectbox('Risk window (years)', [1, 3, 5], index=1, key=key + '_years')
    st.caption('Default: IUSQ.DE · MSCI ACWI ETF proxy · All risk calculations use EUR returns.')
    return benchmark, years


def display_value(metric, key):
    value = finite(metric.value)
    if value is None:
        return '—'
    if key in {'trailing_pe', 'forward_pe', 'fund_pe'} and value <= 0:
        return 'Not meaningful'
    if metric.unit == 'fraction':
        return f'{value * 100:,.2f}%'
    return f'{value:,.2f}' + (f' {metric.unit}' if metric.unit not in {'ratio', ''} else '')


def render_metric_details(snapshot, row, data_dir, key):
    st.caption(f'{snapshot.status.title()} · Retrieved {snapshot.fetched_at or "not fetched"}')
    if snapshot.note:
        st.caption(snapshot.note)
    records = []
    allowed = (set(DEFINITIONS) - {'fee', 'fund_assets', 'fund_pe', 'fund_pb'}) if snapshot.kind == 'equity' else (
        {'fee', 'fund_assets', 'fund_pe', 'fund_pb', 'distribution_yield'} if snapshot.kind == 'etf' else set())
    for name, definition in DEFINITIONS.items():
        if name not in allowed:
            continue
        metric = snapshot.metrics.get(name, Metric())
        records.append({'Metric': definition.label, 'Value': display_value(metric, name),
                        'Definition': definition.description, 'Source': metric.source,
                        'As of / verified': metric.as_of or 'Provider period unspecified', 'Note': metric.note})
    if records:
        st.dataframe(pd.DataFrame(records), hide_index=True, width='stretch')
    else:
        st.info('Fundamental metrics are not available for this instrument type.')
    if snapshot.kind == 'etf' and (row.get('isin') or row.get('ticker')):
        with st.expander('Maintain fund fee'):
            st.caption('Saved privately for this exact share class. Enter the annual fee as a percentage.')
            path = data_dir / 'fund-fees.json'
            current = load_fee_overrides(path).get(fee_key(row))
            with st.form(key + '_fee_' + row['id']):
                rate = st.number_input('Annual fund fee (%)', min_value=0., max_value=100.,
                    value=current.rate * 100 if current else None, step=.01, format='%.4f')
                source = st.text_input('Fee source', value=current.source if current else '')
                verified = st.date_input('Fee verification date', value=date.fromisoformat(current.verified_on) if current else date.today(), max_value=date.today())
                accumulating = st.checkbox('Verified accumulating share class', value=current.accumulating if current else False)
                save = st.form_submit_button('Save private fee')
                remove = st.form_submit_button('Remove private fee', disabled=current is None)
            if save or remove:
                try:
                    if save and rate is None:
                        raise DataError('Enter the annual fee.')
                    fee = FundFee(rate / 100, source, verified.isoformat(), 'User-verified annual fee', accumulating) if save else None
                    save_fee_override(path, fee_key(row), fee)
                    st.rerun()
                except (DataError, OSError, ValueError) as exc:
                    st.error(str(exc))


def position_metric_controls(holdings, data_dir, *, demo=False):
    """Return extra numeric list columns and values; default does no IO/network."""
    key = context_key(data_dir, demo) + '_positions'
    preset = st.selectbox('Position metrics', ['Default', 'Valuation', 'Income & fees', 'Risk'], key=key + '_preset')
    details = st.checkbox('Show instrument metrics', key=key + '_details')
    if preset == 'Default' and not details:
        return [], {}
    instruments = holdings.drop_duplicates('id').set_index('id', drop=False)
    if instruments.empty:
        return [], {}
    selected = None
    if details:
        selected = st.selectbox('Instrument for metrics', list(instruments.index),
            format_func=lambda asset: f'{instruments.loc[asset, "name"]} · {instruments.loc[asset, "ticker"] or asset}', key=key + '_instrument')
    risk, risk_status = None, None
    if preset == 'Risk':
        benchmark, years = risk_controls(key)
        refresh = st.button('Refresh position risk', disabled=demo, key=key + '_risk_refresh')
        if benchmark:
            with st.spinner('Loading adjusted market history…'):
                valued = valued_positions(holdings, data_dir, demo)
                risk, risk_status = load_risk(valued, data_dir, demo, benchmark, years, refresh)
            render_risk(risk, risk_status, holdings, compact=True)
        choices = ['beta', 'volatility']
        labels = {'beta': 'Beta', 'volatility': 'Annual volatility (%)'}
    else:
        choices = ['trailing_pe', 'forward_pe', 'price_book', 'price_sales', 'market_cap', 'fund_pe', 'fund_pb',
                   'revenue_growth', 'earnings_growth', 'profit_margin', 'return_equity'] if preset == 'Valuation' else [
                   'distribution_yield', 'fee', 'fund_assets', 'payout_ratio']
        labels = {name: DEFINITIONS[name].label + (' (%)' if DEFINITIONS[name].unit == 'fraction' else '') for name in choices}
    chosen = st.multiselect('Metric columns', choices, default=choices[:3], format_func=labels.get,
                           key=key + '_columns_' + preset) if preset != 'Default' else []
    snapshots = {}
    if preset in {'Valuation', 'Income & fees'} or details:
        refresh = st.button('Refresh fundamentals', disabled=demo, key=key + '_refresh')
        selected_rows = holdings if preset in {'Valuation', 'Income & fees'} else holdings.loc[holdings.id.eq(selected)]
        try:
            with st.spinner('Loading fundamentals…'):
                snapshots = load_metrics(selected_rows, data_dir, demo=demo, refresh=refresh)
        except DataError as exc:
            st.error(str(exc))
    if details and selected in snapshots:
        render_metric_details(snapshots[selected], instruments.loc[selected], data_dir, key)
    values = {}
    for identity in instruments.index:
        entry = {}
        for name in chosen:
            snapshot = snapshots.get(identity)
            if name in {'beta', 'volatility'}:
                value = finite(risk.holdings.loc[identity, 'Beta' if name == 'beta' else 'Annual volatility']) if risk and identity in risk.holdings.index else None
                if name == 'volatility' and value is not None:
                    value *= 100
            else:
                value = metric_value(snapshot, name)
                if name in {'trailing_pe', 'forward_pe', 'fund_pe'} and value is not None and value <= 0:
                    value = None
                    entry['metric_' + name + '_display'] = 'Not meaningful'
                if DEFINITIONS[name].unit == 'fraction' and value is not None:
                    value *= 100
                metric = snapshot.metrics.get(name) if snapshot else None
                entry['metric_' + name + '_note'] = (f'{metric.unit} · {metric.source} · {metric.note} · {snapshot.status} · Retrieved {snapshot.fetched_at}' if metric else 'Unavailable')
            entry['metric_' + name] = value
        values[identity] = entry
    if snapshots:
        with st.expander('Fundamentals data status'):
            st.dataframe(pd.DataFrame([dict(Instrument=asset, Status=s.status, Retrieved=s.fetched_at, Note=s.note) for asset, s in snapshots.items()]), hide_index=True)
            st.caption('Monetary metrics retain each provider’s currency; see instrument details. Fund ratios have separate definitions.')
    return [('metric_' + name, labels[name]) for name in chosen], values


def render_risk(result, status, holdings, *, compact=False):
    covered = 100 * result.covered_value / result.known_value if result.known_value > 0 else None
    denominator = 'portfolio value' if result.valuation_complete else 'known valued assets; whole-portfolio coverage unavailable'
    st.caption(f'History covers {covered:.1f}% of {denominator}' if covered is not None else 'No valued assets with history.')
    if result.status == 'partial':
        st.warning('Covered-subportfolio estimate: weights are renormalized within the holdings with usable history.')
    if not result.valuation_complete:
        st.warning('Some current valuations are missing; total portfolio weights are unknown.')
    st.caption(result.note)
    if result.observations:
        st.caption(f'{result.observations} common weekly returns · {result.start or "—"} to {result.end or "—"}')
    if not compact:
        columns = st.columns(3)
        columns[0].metric('Portfolio beta' if result.status == 'complete' else 'Covered-subportfolio beta', f'{result.beta:.2f}' if result.beta is not None else '—')
        columns[1].metric('Annualized volatility', f'{100 * result.volatility:.2f}%' if result.volatility is not None else '—')
        columns[2].metric('Benchmark correlation', f'{result.correlation:.2f}' if result.correlation is not None else '—')
        if not result.holdings.empty:
            names = holdings.drop_duplicates('id').set_index('id').name.to_dict()
            frame = result.holdings.copy()
            for column in ['Weight', 'Annual volatility', 'Volatility contribution', 'Risk share']:
                frame[column + ' (%)'] = 100 * frame.pop(column)
            frame.insert(0, 'Instrument', [f'{names.get(key, key)} [{key}]' for key in frame.index])
            st.dataframe(frame, hide_index=True, width='stretch')
            st.caption('Volatility contributions sum to annualized portfolio volatility; negative contributions indicate diversification. Cash correlations are undefined.')
            correlation = result.correlations.rename(index=lambda key: f'{names.get(key, key)} [{key}]', columns=lambda key: f'{names.get(key, key)} [{key}]')
            figure = px.imshow(correlation, zmin=-1, zmax=1, color_continuous_scale='RdBu_r', title='Holding correlations · common EUR weekly sample')
            st.plotly_chart(figure, width='stretch')
    if result.excluded:
        with st.expander('Excluded from risk estimate'):
            st.dataframe(pd.DataFrame(result.excluded.items(), columns=['Instrument ID', 'Reason']), hide_index=True)
    if status.Status.eq('stale').any():
        st.warning('Risk estimates include stale cached market or FX histories.')
    with st.expander('Risk data status'):
        st.dataframe(status, hide_index=True, width='stretch')


def render_portfolio_analytics(holdings, data_dir, funds, *, allocation=None, demo=False, price_service=None):
    key = context_key(data_dir, demo) + '_overview'
    if not st.checkbox('Show portfolio analytics', key=key + '_show'):
        return
    st.subheader('Portfolio analytics')
    st.caption('Uses source positions; independent of exposure filters and position-list search.')
    accounts = sorted(holdings.account.unique())
    selected_accounts = st.multiselect('Analytics accounts', accounts, default=accounts, key=key + '_accounts')
    bucket_column = 'bucket_id' if allocation else 'portfolio'
    categories = sorted(holdings[bucket_column].unique())
    names = {bucket.id: bucket.name for bucket in allocation.buckets} if allocation else {}
    selected_categories = st.multiselect('Analytics categories', categories, default=categories,
        format_func=lambda value: names.get(value, value) or 'Unassigned', key=key + '_categories')
    selected = holdings.loc[holdings.account.isin(selected_accounts) & holdings[bucket_column].isin(selected_categories) & holdings.shares.gt(0)]
    if selected.empty:
        st.info('No held positions in this analytics scope.')
        return
    valued = valued_positions(selected, data_dir, demo, price_service)
    fetch = st.checkbox('Load portfolio fundamentals', key=key + '_fundamentals')
    refresh = st.button('Refresh portfolio fundamentals', disabled=demo or not fetch, key=key + '_refresh')
    try:
        with st.spinner('Preparing portfolio metrics…'):
            snapshots = load_metrics(selected, data_dir, demo=demo, fetch=fetch, refresh=refresh)
    except DataError as exc:
        st.error(str(exc))
        return
    summary = snapshot_analytics(valued, snapshots)
    if not summary.valuation_complete:
        st.warning(f'{summary.missing_valuations} positions lack current valuations. Allocation statistics use known valued assets only.')
    columns = st.columns(3)
    columns[0].metric('Largest holding', f'{summary.largest_weight * 100:.2f}%' if summary.largest_weight is not None else '—')
    columns[1].metric('Top-five holdings', f'{summary.top_five_weight * 100:.2f}%' if summary.top_five_weight is not None else '—')
    columns[2].metric('Effective number of holdings', f'{summary.effective_holdings:.2f}' if summary.effective_holdings is not None else '—')
    st.caption('Concentration combines repeated instrument IDs across accounts. Effective holdings = 1 / sum of squared value weights; ETF wrappers count as instruments here.')
    with st.expander('Underlying company concentration'):
        try:
            identities = load_company_identities(data_dir / 'company-identities.yaml')
            typed = valued.copy()
            typed['instrument_type'] = [snapshots[row.id].kind for row in typed.itertuples()]
            plan = build_plan(typed, funds, identities, load_settings(data_dir / 'company-merges.yaml'), {}, load_company_names(data_dir / 'company-names.yaml'))
            company_positions, company_funds, _ = plan.apply(typed, funds, {})
            company = stock_exposure(company_positions, company_funds, identities=identities)
            st.dataframe(company.companies.head(10), hide_index=True, width='stretch')
            if not company.unresolved.empty:
                st.caption('Unresolved look-through exposure remains outside the named-company ranking.')
                st.dataframe(company.unresolved, hide_index=True)
            for fund in company_funds:
                if fund.proxy_source:
                    st.caption(f'{fund.name}: {fund.proxy_source}')
        except DataError as exc:
            st.error(str(exc))
    records = []
    labels = {'fee': 'Weighted annual fee · covered funds', 'fee_eur': 'Known annual fund costs',
              'distribution_yield': 'Trailing cash yield · covered holdings', 'distribution_yield_eur': 'Annualized trailing cash distributions',
              'trailing_pe': 'Trailing P/E · profitable direct equities', 'forward_pe': 'Forward P/E · profitable direct equities'}
    for name, metric in summary.metrics.items():
        unit = 'EUR' if name.endswith('_eur') else 'ratio' if name.endswith('_pe') else 'fraction'
        records.append({'Metric': labels[name], 'Value': display_value(Metric(metric.value, unit), name),
                        'Covered value (EUR)': metric.covered_value, 'Eligible valued assets (EUR)': metric.eligible_value,
                        'Coverage of eligible value (%)': 100 * metric.covered_value / metric.eligible_value if metric.eligible_value > 0 else None,
                        'Excluded valued positions': metric.excluded_count})
    st.dataframe(pd.DataFrame(records), hide_index=True, width='stretch')
    costs = summary.metrics['fee_eur'].value
    if costs is not None and summary.known_value > 0:
        st.caption(f'Known fund fees / {"portfolio value" if summary.valuation_complete else "known valued assets"}: {100 * costs / summary.known_value:.3f}% per year.')
    st.caption('Fees are already reflected in fund prices; not deducted again. Missing fees and distributions are not zero. Cash yields describe trailing distributions, not forecasts or received income. P/E uses value-weighted earnings yields and excludes nonpositive or missing P/E; funds are not included.')
    with st.expander('Coverage and fundamental sources'):
        st.dataframe(pd.DataFrame([{'Instrument': row['name'], 'Type': snapshots[row['id']].kind,
            'Value (EUR)': row['current_value_eur'], 'Status': snapshots[row['id']].status,
            'Retrieved': snapshots[row['id']].fetched_at,
            **{DEFINITIONS[name].label: display_value(snapshots[row['id']].metrics.get(name, Metric()), name)
               for name in ['trailing_pe', 'forward_pe', 'fee', 'distribution_yield']}}
            for row in valued.to_dict('records')]), hide_index=True, width='stretch')
        st.caption('Open instrument metrics in Positions for definitions, fee verification dates, and individual sources.')
    if summary.performance and summary.performance.covered_count:
        performance = summary.performance
        with st.expander('Unrealized gains on recorded EUR costs'):
            st.metric('Covered unrealized gain', f'{performance.gain_eur:+,.2f} EUR')
            st.metric('Return on recorded cost', f'{performance.return_pct:+.2f}%' if performance.return_pct is not None else '—')
            st.caption(f'{performance.covered_count} of {performance.held_count} held positions have comparable recorded EUR costs.')
            ranked = valued.loc[valued.unrealized_gain_eur.notna(), ['name', 'unrealized_gain_eur', 'return_pct']].sort_values('unrealized_gain_eur', ascending=False)
            st.dataframe(ranked, hide_index=True)
    if st.checkbox('Load historical risk', key=key + '_risk'):
        benchmark, years = risk_controls(key)
        refresh = st.button('Refresh portfolio risk', disabled=demo, key=key + '_risk_refresh')
        if benchmark:
            with st.spinner('Loading adjusted market and FX histories…'):
                risk, status = load_risk(valued, data_dir, demo, benchmark, years, refresh)
            render_risk(risk, status, selected)
