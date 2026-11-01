"""Indicator exploration, saved rules and their recorded observations."""
from portfolio_app.chart_hover import chart_hover
from dataclasses import replace
from datetime import date
from hashlib import sha256
from uuid import uuid4

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from portfolio_app.background_ui import refresh_allowed

from portfolio_app.charts import style_figure
from portfolio_app.holdings import DataError
from portfolio_app.monitoring import Rule, SeriesSpec, evaluate_series, indicator_frame, condition, utc_now
from portfolio_app.monitoring_presentation import observation_time, alert_details
from portfolio_app.monitoring_service import monitoring_for
from portfolio_app.monitoring_store import (load_monitoring, rule_from_dict, save_rule, delete_rule, mark_events)


def request_monitoring(*, symbol='', account=''):
    st.session_state.update(main_tabs='Monitoring', monitoring_view='Browse' if symbol else 'Account alerts',
                            monitoring_seed_symbol=symbol, monitoring_seed_account=account, position_edit_dialog=False)


def open_rule(identity):
    st.session_state['monitoring_route'] = identity
    st.session_state['position_edit_dialog'] = False


def apply_monitoring_route():
    identity = st.session_state.pop('monitoring_route', None)
    if identity:
        st.session_state.update(main_tabs='Monitoring', monitoring_view='Tracked rules', monitoring_selected=identity,
                                workspace_notifications=False)


def _value(value, suffix='%'):
    return '—' if value is None else f'{value:,.2f}{suffix}'


def describe(rule):
    if rule.kind == 'sma':
        spec = rule.series
        timing = {'daily': 'Daily close', 'monthly': 'Month-end close', 'intraday': 'Intraday observation'}[rule.cadence]
        return (f'{spec.name or spec.symbol} · {spec.currency or "Source currency"} · {spec.convention} · '
                f'{timing} {"at or below" if rule.direction == "below" else "at or above"} {rule.threshold:+g}% '
                f'from its {rule.window}-{rule.period} SMA · {"Developing period included" if rule.developing else "Completed periods"}')
    return (f'{"LTV" if rule.kind == "ltv" else "Maintenance headroom"} '
            f'{"at or above" if rule.direction == "above" else "at or below"} {rule.threshold:g}% · {rule.currency} · User-defined limit')


def _plot(rule, series, *, key='monitoring_chart_', show_condition=True, tracked=True):
    # Calculate with all source history before clipping; a display window must
    # never shorten the SMA's warm-up or change a saved rule's live evaluation.
    frame = indicator_frame(rule, series)
    if frame.empty:
        st.info('No completed observations available.')
        return
    chart_key = key+sha256((rule.series.symbol+rule.series.basis).encode()).hexdigest()[:10]+'_'
    toolbar, details = st.columns([4, 1], vertical_alignment='bottom')
    ranges = ['1 year', '3 years', '5 years', '10 years', 'All history', 'Dotcom (1999–2003)', 'GFC (2007–2009)', 'Custom dates']
    selected = toolbar.selectbox('Chart period', ranges, index=1, key=chart_key+'range',
        help='Changes the chart only. SMA calculations use earlier history for warm-up; saved alerts always evaluate the latest eligible observation.')
    latest = frame.index[-1]
    start, end = frame.index[0].date(), latest.date()
    if rule.cadence == 'intraday' and series.quote_at:
        end = max(end, pd.Timestamp(series.quote_at).date())
    if selected.endswith(('year', 'years')):
        start = (latest-pd.DateOffset(years=int(selected.split()[0]))).date()
    elif selected == 'Dotcom (1999–2003)':
        start, end = date(1999,1,1), date(2003,12,31)
    elif selected == 'GFC (2007–2009)':
        start, end = date(2007,1,1), date(2009,12,31)
    elif selected == 'Custom dates':
        a,b = st.columns(2)
        start = a.date_input('Start date', value=start, min_value=date(1900,1,1), max_value=date.today(), key=chart_key+'start')
        end = b.date_input('End date', value=end, min_value=date(1900,1,1), max_value=date.today(), key=chart_key+'end')
    with details.popover('Data details', width='stretch'):
        st.write(f'{rule.series.source} · {series.currency} · {rule.series.convention}')
        st.write(f'Available history: {series.dates[0]} to {series.dates[-1]}')
        st.write('Retrieved '+observation_time(series.fetched_at))
        st.write(series.note)
        if rule.series.symbol.startswith('MSCI:'):
            st.caption('This source provides daily history from 2000. Initial SMA values need their full warm-up window.')
    if start > end:
        st.warning('Start date must be on or before end date.')
        return
    display = frame.loc[str(start):str(end)]
    if display.empty:
        st.info(f'No observations in this period. Available history: {series.dates[0]} to {series.dates[-1]}.')
        return
    figure = go.Figure()
    figure.add_scatter(x=display.index, y=display.price, name='Completed close', line=dict(color='#5470c6'))
    figure.add_scatter(x=display.index, y=display.sma, name='SMA', line=dict(color='#e6a23c'))
    if show_condition:
        figure.add_scatter(x=display.index, y=display.sma*(1+rule.threshold/100), name='Trigger boundary', line=dict(color='#cf4446', dash='dash'))
        warning = rule.threshold + (rule.warning_band if rule.direction == 'below' else -rule.warning_band)
        if rule.warning_band:
            figure.add_scatter(x=display.index, y=display.sma*(1+warning/100), name='Approaching boundary', line=dict(color='#d3ac57', dash='dot'))
    current = evaluate_series(rule, series)
    if rule.cadence == 'intraday' and series.quote is not None and series.quote_at and start <= pd.Timestamp(series.quote_at).date() <= end:
        figure.add_scatter(x=[series.quote_at], y=[series.quote], name='Latest observation', mode='markers', marker=dict(size=10))
        if current.reference is not None:
            figure.add_scatter(x=[series.quote_at], y=[current.reference], name='Current SMA reference', mode='markers', marker=dict(size=10, symbol='diamond'))
    figure = style_figure(figure)
    figure.update_layout(height=440, yaxis_title=series.currency, legend=dict(orientation='h'), margin=dict(l=12,r=12,t=20,b=30),
                         meta=dict(symbol=rule.series.symbol, basis=rule.series.basis, start=str(start), end=str(end)))
    with st.container(key='tour_monitoring_chart'):
        st.plotly_chart(chart_hover(figure), width='stretch')
    historical = end < latest.date()
    row = display.iloc[-1]
    value = row.distance if historical else current.value
    reference = row.sma if historical else current.reference
    quality = pd.notna(row.sma) if historical else current.quality == 'valid'
    columns = st.columns(3 if show_condition else 2)
    a,b = columns[:2]
    a.metric('Distance from SMA', _value(value) if quality else '—', help='At the final close in the selected historical period, or the latest eligible observation for a current chart.')
    b.metric('SMA', _value(reference, ' '+series.currency) if quality else '—')
    if show_condition:
        columns[2].metric('Historical condition' if historical else 'Rule status' if tracked else 'Preview condition', condition(float(value),rule).title() if quality else 'Unavailable',
                 help='Preview and historical conditions do not create alerts. Monitoring begins only after you track the rule.')
    if historical:
        st.caption('Historical close · '+observation_time(display.index[-1].date().isoformat()))
    elif current.quality != 'valid':
        st.info(current.note)
    if display.sma.isna().any():
        st.caption('SMA unavailable where the source has insufficient earlier observations.')


def _freshness(key, minutes, *, label='Maximum data age'):
    units = {'Minutes':1, 'Hours':60, 'Days':1440}
    initial_unit = 'Days' if minutes % 1440 == 0 else 'Hours' if minutes % 60 == 0 else 'Minutes'
    a,b = st.columns([2,1])
    unit = b.selectbox('Age unit', list(units), index=list(units).index(initial_unit), key=key+'unit')
    value = a.number_input(label, min_value=1, value=max(1,minutes//units[unit]), step=1, key=key+'amount_'+unit,
        help='Older observations withhold new signals and all-clears. Intraday observations may become stale overnight. This does not change the five-minute checking interval.')
    return int(value)*units[unit]


def _delivery(key, existing=None):
    options = ['dialog', 'banner', 'center']
    return st.selectbox('Alert presentation', options, index=options.index(existing.delivery) if existing else 0,
        format_func=lambda x: {'dialog':'Pop-up + Monitoring banner', 'banner':'Banner only', 'center':'Notification center only'}[x], key=key+'delivery', help='Popups can appear on any tab. Banners and check status appear in Monitoring; all alerts are also recorded in Notifications.')


def _rule_controls(key, existing=None):
    columns = st.columns([1, 1, 1])
    direction = columns[0].selectbox('Trigger direction', ['below','above'], index=1 if existing and existing.direction == 'above' else 0, key=key+'direction')
    a,b = columns[1:]
    threshold = a.number_input('Distance from SMA trigger (%)', value=float(existing.threshold) if existing else None, step=.5, key=key+'threshold', placeholder='Enter a threshold to create a rule', help='Leave empty to explore prices and SMA without rule bands. Negative values are below the SMA. Equality triggers the alert.')
    warning = b.number_input('Approaching band (percentage points)', min_value=0., value=float(existing.warning_band) if existing else 1., step=.5, key=key+'warning', disabled=threshold is None, help='Distance before the trigger, in percentage points. For a −2% trigger and a 1 pp band, approaching begins at −1%.')
    return direction, threshold, warning


def _browse(directory, holdings, *, demo=False, existing=None):
    from portfolio_app.monitoring_catalog import BENCHMARKS, KNOWN_REFERENCES, portfolio_instruments
    key = 'monitoring_edit_'+existing.id+'_'+str(existing.revision)+'_' if existing else 'monitoring_browse_'
    if existing:
        symbol, name = existing.series.symbol, existing.series.name
        st.markdown(f'**Edit {existing.name}**')
    else:
        holdings_choices = portfolio_instruments(holdings)
        seed = st.session_state.pop('monitoring_seed_symbol', '')
        if seed:
            st.session_state[key+'source'] = 'Portfolio assets' if seed in holdings_choices else 'Market indices' if seed in BENCHMARKS else 'Provider ticker'
            if seed in holdings_choices:
                st.session_state[key+'holding'] = seed
            elif seed in BENCHMARKS:
                st.session_state[key+'index'] = seed.split(':')[1]
                st.session_state[key+'index_currency'] = BENCHMARKS[seed].currency
            else:
                st.session_state[key+'symbol'] = seed
        source = st.segmented_control('Instrument source', ['Market indices','Portfolio assets','Provider ticker'],
            default=None if key+'source' in st.session_state else 'Market indices', key=key+'source',
            help='Market indices are a fixed set of broad equity benchmarks. Portfolio assets are the unique market tickers of your current nonzero holdings. Provider ticker accepts other instruments.') or 'Market indices'
        if source == 'Market indices':
            from portfolio_app.msci_history import INDEX_NAMES
            a,b = st.columns([3,1])
            code = a.selectbox('Market index', list(INDEX_NAMES), format_func=INDEX_NAMES.get, key=key+'index')
            currency = b.selectbox('Index currency', ['EUR','USD','GBP'], key=key+'index_currency',
                help='Published net-return index currency. No ETF substitution or portfolio-currency conversion.')
            symbol = f'MSCI:{code}:NETR:{currency}'
            name = INDEX_NAMES[code]
        elif source == 'Portfolio assets':
            if not holdings_choices:
                st.info('No currently held assets have a market ticker. Choose a market index or enter a provider ticker.')
                return
            symbol = st.selectbox('Portfolio asset', list(holdings_choices), format_func=lambda x: holdings_choices[x]+' · '+x, key=key+'holding')
            name = holdings_choices[symbol]
        else:
            symbol = st.text_input('Provider ticker', placeholder='e.g. SPY, BTC-EUR or an exchange-specific ETF ticker', key=key+'symbol',
                help='Use the exact Yahoo Finance symbol, including its exchange or quote-currency suffix.').strip().upper()
            name = symbol
            if not symbol:
                return
    reference = KNOWN_REFERENCES.get(symbol)
    basis_options = ['close'] if reference else ['close', 'adjusted']
    basis_column, preset_column, cadence_column = st.columns(3)
    basis = basis_column.selectbox('Price basis', basis_options, index=basis_options.index(existing.series.basis) if existing else 0,
                         format_func=lambda x: 'Published index levels' if reference else 'Closing prices (split adjusted)' if x == 'close' else 'Dividend-adjusted history', key=key+'basis_'+('index' if reference else 'market'), help='Indices use their published return convention. Closing prices include provider split adjustments; dividend-adjusted history is a separate choice.')
    spec = SeriesSpec(symbol, reference.name if reference else name,
        reference.currency if reference else existing.series.currency if existing else '', basis,
        reference.source if reference else 'Yahoo Finance', reference.convention if reference else 'Dividend adjusted' if basis == 'adjusted' else 'Closing price')
    preset = preset_column.selectbox('SMA preset', ['200 sessions', '10 months', 'Custom'], index=2 if existing else 0, key=key+'preset')
    if preset == 'Custom':
        a,b = st.columns(2)
        period_options = ['sessions','months']
        period = a.selectbox('SMA periods', period_options,
            index=period_options.index(existing.period) if existing else 0, key=key+'period_Custom')
        window = int(b.number_input('SMA window', min_value=2, max_value=1000,
            value=existing.window if existing else 200, step=1, key=key+'window_Custom'))
    else:
        period, window = ('months',10) if preset == '10 months' else ('sessions',200)
    cadences = ['daily', 'monthly'] + ([] if symbol.startswith('MSCI:') else ['intraday'])
    cadence = cadence_column.selectbox('Evaluate on', cadences, index=cadences.index(existing.cadence) if existing else 0,
        format_func=lambda x: {'daily':'Completed daily close', 'monthly':'Completed month-end close', 'intraday':'Latest intraday observation'}[x], key=key+'cadence_'+('msci' if symbol.startswith('MSCI:') else 'market'), help='Daily and month-end decisions use completed closes. Direct MSCI references supply daily levels only.')
    with st.container(key='tour_monitoring_condition'):
        direction, threshold, warning = _rule_controls(key, existing)
    if not existing:
        st.caption('Untracked preview', help='Browsing and entering a condition do not enable alerts. Enter a threshold and choose Track rule to start monitoring.')
    age_default = existing.max_age_minutes if existing else 30 if cadence == 'intraday' else 10080
    timing, refresh_slot, _ = st.columns([2,1.8,4.5])
    with timing.popover('Timing & data freshness', icon=':material/schedule:'):
        developing = st.checkbox('Include developing period', value=bool(existing and existing.developing), key=key+'developing', disabled=cadence != 'intraday',
            help='Intraday only: use the latest observation plus N−1 completed periods. Otherwise the reference uses completed periods only. Months finalize when the next calendar month begins.') and cadence == 'intraday'
        age = _freshness(key+'age_'+cadence+'_', age_default)
    service = monitoring_for(directory, demo=demo)
    refresh = refresh_slot.button('Refresh market data', key=key+'refresh',
        help='Refresh the selected instrument’s prices and history. This does not check saved rules.')
    series = service.browse(spec, refresh=refresh)
    rule = Rule(existing.id if existing else uuid4().hex, existing.name if existing else f'{spec.name or symbol} SMA {window}',
        series=spec, window=window, period=period, cadence=cadence, developing=developing,
        # The calculation definition needs a finite threshold. This placeholder
        # is never shown as a condition or saved while the draft is incomplete.
        direction=direction, threshold=threshold if threshold is not None else 0., warning_band=warning, max_age_minutes=age)
    if series and series.dates:
        spec = replace(spec, currency=series.currency) if not spec.currency else spec
        rule = replace(rule, series=spec)
    with st.container(key='tour_monitoring_save'):
        name_slot, delivery_slot, save_slot = st.columns([3,2,1.3], vertical_alignment='bottom')
        name_input = name_slot.text_input('Rule name', value=existing.name if existing else f'{spec.name or symbol} SMA {window}', key=key+'name_'+sha256(symbol.encode()).hexdigest()[:10])
        with delivery_slot:
            delivery = _delivery(key, existing)
        if save_slot.button('Save changes' if existing else 'Track rule', key=key+'save', type='primary', width='stretch',
                            disabled=threshold is None or not name_input.strip() or not series or not series.dates,
                            help='Enter a trigger threshold and name, and load market history before saving.'):
            saved = replace(rule, name=name_input, delivery=delivery, revision=existing.revision+1 if existing else 1,
                            enabled=existing.enabled if existing else True, created_at=utc_now().isoformat())
            save_rule(directory, saved, expected_rule_revision=existing.revision if existing else None)
            st.session_state.pop('monitoring_editing', None)
            open_rule(saved.id)
            service.schedule(refresh=True)
            st.rerun()
    if series and series.dates:
        _plot(rule, series, key=key+'chart_', show_condition=threshold is not None, tracked=False)
    else:
        st.info(series.note if series else 'Loading indicator history…')


def _account_editor(directory, holdings, journal, *, demo=False, existing=None):
    from portfolio_app.account_journal import replay
    if not journal or not journal.accounts:
        st.info('Create an account under Positions → Position tools → Accounts & futures before adding an account alert.')
        return
    key = 'monitoring_account_'+existing.id+'_'+str(existing.revision)+'_' if existing else 'monitoring_account_'
    names = {a.id:a.name for a in journal.accounts}
    seed = st.session_state.pop('monitoring_seed_account', '')
    if seed in names:
        st.session_state[key+'account'] = seed
    account = st.selectbox('Monitored account', list(names), index=list(names).index(existing.account_id) if existing and existing.account_id in names else 0, format_func=names.get, key=key+'account')
    kind = st.selectbox('Account metric', ['ltv','coverage'], index=int(bool(existing and existing.kind == 'coverage')),
                        format_func=lambda x: 'Loan-to-value (LTV)' if x == 'ltv' else 'Maintenance headroom', key=key+'kind',
                        help='LTV uses recorded loans and negative cash against selected gross backing assets. Maintenance headroom uses pledged collateral after haircuts: (collateral − requirement) / requirement × 100%.')
    currency = st.selectbox('Rule currency', ['EUR','USD','GBP'], index=['EUR','USD','GBP'].index(existing.currency) if existing else 0, key=key+'currency')
    backing, cash = (), ()
    if kind == 'ltv':
        linked = {l.position_id for l in journal.holding_links if l.account_id == account}
        cash_ids = {l.position_id for l in journal.cash_links}
        labels = {r.position_id:r['name'] for _,r in holdings.iterrows() if r.position_id in linked-cash_ids and r.get('instrument_type') != 'cash'}
        if not labels:
            st.caption('Link securities to this account under Accounts & futures → Collateral to make them available here.')
        backing = tuple(st.multiselect('Backing securities', list(labels), default=[x for x in existing.backing if x in labels] if existing else [], format_func=labels.get, key=key+account+'backing'))
        units = sorted({unit for owner,unit in replay(journal).cash if owner == account})
        cash = tuple(st.multiselect('Backing cash balances', units, default=[x for x in existing.cash if x in units] if existing else [], key=key+account+'cash'))
        threshold_text = st.text_input('Maximum LTV (%)', value=str(existing.threshold) if existing else '', key=key+'ltv_limit',
            help='Your own upper limit, not an inferred liquidation threshold. Debt includes loans and negative cash; backing includes selected assets and positive cash without haircuts. Pending transfers and futures notional are excluded.')
        warning_text = st.text_input('Warning LTV (%)', value=str(existing.threshold-existing.warning_band) if existing else '', key=key+'ltv_warning')
    else:
        threshold_text = st.text_input('Trigger at headroom (%)', value=str(existing.threshold) if existing else '', key=key+'coverage_limit',
            help='Your own lower limit based on entered requirements. These alerts do not model a broker’s liquidation rules.')
        warning_text = st.text_input('Warn at headroom (%)', value=str(existing.threshold+existing.warning_band) if existing else '', key=key+'coverage_warning')
    name = st.text_input('Account rule name', value=existing.name if existing else names[account]+' '+('LTV' if kind == 'ltv' else 'maintenance'), key=key+'name')
    delivery = _delivery(key, existing)
    with st.popover('Data freshness', icon=':material/schedule:'):
        age = _freshness(key+'age_', existing.max_age_minutes if existing else 10080, label='Maximum valuation / FX age')
    if st.button('Save account rule', type='primary', key=key+'save'):
        try:
            threshold, warning = float(threshold_text), float(warning_text)
        except ValueError as exc:
            raise DataError('Enter both trigger and warning percentages.') from exc
        rule = Rule(existing.id if existing else uuid4().hex, name, kind=kind, revision=existing.revision+1 if existing else 1,
            account_id=account, currency=currency, backing=backing, cash=cash, direction='above' if kind == 'ltv' else 'below',
            threshold=threshold, warning_band=threshold-warning if kind == 'ltv' else warning-threshold,
            delivery=delivery, created_at=utc_now().isoformat(), max_age_minutes=age, enabled=existing.enabled if existing else True)
        save_rule(directory, rule, expected_rule_revision=existing.revision if existing else None)
        st.session_state.pop('monitoring_editing', None)
        open_rule(rule.id)
        monitoring_for(directory, demo=demo).schedule(refresh=True)
        st.rerun()


def _account_inputs(rule, values, holdings, journal):
    names = {row.position_id:row['name'] for _,row in holdings.iterrows()}
    inputs = values.get('inputs', {})
    def asset_name(identity):
        return names.get(identity, identity.replace('cash:', 'Cash · '))
    if rule.kind == 'ltv':
        a,b = st.columns(2)
        a.metric('Recorded debt', _value(inputs.get('debt'), ' '+rule.currency))
        b.metric('Selected backing value', _value(inputs.get('asset_value'), ' '+rule.currency))
        entries = [{'Obligation':identity.replace('negative-cash:', 'Negative cash · ').replace('loan:', 'Loan · '),
                    'Value ('+rule.currency+')':amount} for identity,amount in inputs.get('obligations', {}).items()]
        if entries:
            st.dataframe(pd.DataFrame(entries), hide_index=True, width='stretch')
        assets = [{'Backing asset':asset_name(identity), 'Value ('+rule.currency+')':amount}
                  for identity,amount in inputs.get('backing', {}).items()]
        if assets:
            st.dataframe(pd.DataFrame(assets), hide_index=True, width='stretch')
        if inputs.get('debt_entries'):
            with st.expander('Recorded balance and debt dates'):
                st.dataframe(pd.DataFrame([{'Date':e['date'], 'Entry':e['action'].replace('_',' ').title()}
                    for e in inputs['debt_entries']]), hide_index=True, width='stretch')
    else:
        a,b,c = st.columns(3)
        a.metric('Eligible collateral', _value(inputs.get('collateral'), ' '+rule.currency))
        b.metric('Maintenance requirement', _value(inputs.get('maintenance'), ' '+rule.currency))
        c.metric('Headroom', _value(inputs.get('headroom'), ' '+rule.currency))
        assets = [{'Backing asset':asset_name(row['asset']), 'Reserved units / cash':row['reserved'],
                   'Haircut (%)':row['haircut_pct'], 'Eligible value ('+rule.currency+')':row['eligible_value'],
                   'Note':row['issue']} for row in inputs.get('backing', [])]
        if assets:
            st.dataframe(pd.DataFrame(assets), hide_index=True, width='stretch')
        if inputs.get('requirements'):
            st.dataframe(pd.DataFrame([{'Requirement date':r['date'], 'Source':r['source']}
                for r in inputs['requirements']]), hide_index=True, width='stretch')


def _tracked(directory, holdings, journal, *, demo=False):
    doc, _ = load_monitoring(directory)
    rules = {r['id']:rule_from_dict(r) for r in doc['rules']}
    if not rules:
        st.info('No tracked rules yet. Browse an indicator or create an account alert.')
        return
    records = []
    for rule in rules.values():
        state = doc['states'].get(rule.id, {})
        latest = state.get('latest', {}) if state.get('quality') == 'not_applicable' else state.get('last_valid', {})
        value = latest.get('value')
        records.append({'Rule': rule.name, 'Status': 'Paused' if not rule.enabled else state.get('condition', 'Waiting').title(),
            'Data': state.get('quality','Waiting'), 'Value (%)': value, 'Trigger (%)': rule.threshold,
            'Distance to trigger (pp)': abs(value-rule.threshold) if value is not None else None,
            'Observed': latest.get('observed_at','—')})
    with st.container(key='tour_monitoring_tracked'):
        st.dataframe(pd.DataFrame(records), hide_index=True, width='stretch')
    if st.session_state.get('monitoring_selected') not in rules:
        st.session_state['monitoring_selected'] = next(iter(rules))
    identity = st.selectbox('Rule details', list(rules), format_func=lambda x:rules[x].name, key='monitoring_selected')
    rule = rules[identity]
    st.caption(describe(rule))
    a,b,c = st.columns(3)
    if a.button('Resume rule' if not rule.enabled else 'Pause rule', key='monitoring_pause'):
        save_rule(directory, replace(rule, revision=rule.revision+1, enabled=not rule.enabled, created_at=utc_now().isoformat()), expected_rule_revision=rule.revision)
        monitoring_for(directory,demo=demo).schedule(refresh=True)
        st.rerun()
    if b.button('Edit rule', key='monitoring_edit'):
        st.session_state['monitoring_editing'] = identity
    if c.button('Delete rule', key='monitoring_delete'):
        st.session_state['monitoring_delete_requested'] = identity
    if st.session_state.get('monitoring_delete_requested') == identity:
        st.warning('Delete this saved rule? Recorded observations and alerts will remain in History.')
        if st.button('Confirm deletion', key='monitoring_delete_confirm'):
            delete_rule(directory, identity, rule.revision)
            st.session_state.pop('monitoring_delete_requested', None)
            st.rerun()
    if st.session_state.get('monitoring_editing') == identity:
        if st.button('Cancel editing', key='monitoring_cancel_edit'):
            st.session_state.pop('monitoring_editing', None)
            st.rerun()
        if rule.kind == 'sma':
            _browse(directory, holdings, demo=demo, existing=rule)
        else:
            _account_editor(directory, holdings, journal, demo=demo, existing=rule)
        return
    state = doc['states'].get(identity, {})
    latest = state.get('latest', {})
    if latest.get('quality') != 'valid' and latest.get('note'):
        st.warning(latest['note'])
    if rule.kind == 'sma':
        series = monitoring_for(directory,demo=demo).browse(rule.series)
        if series and series.dates:
            _plot(rule, series, key='monitoring_chart_'+rule.id+'_')
    else:
        valid = state.get('latest', {}) if state.get('quality') == 'not_applicable' else state.get('last_valid', {})
        st.metric('LTV' if rule.kind == 'ltv' else 'Maintenance headroom', _value(valid.get('value')))
        if valid.get('note'):
            st.caption(valid['note'])
        _account_inputs(rule, valid, holdings, journal)
    _history(doc, identity)


def _history(doc, identity=None):
    st.markdown('**Recorded observations**')
    observations = [x for x in doc['observations'] if identity is None or x['rule_id'] == identity]
    st.caption('Recorded from rule creation onward. Gaps are not continuous monitoring. Recovered daily/monthly closes are labeled; intraday and account observations are never backfilled.')
    if observations:
        rows = [{'Rule': doc['definitions'].get(f"{x['rule_id']}:{x['revision']}",{}).get('name',x['rule_id']),
                 'Revision':x['revision'], 'Observed':x['evaluation']['observed_at'], 'Recorded':x['recorded_at'],
                 'Value (%)':x['evaluation']['value'], 'Condition':x['evaluation']['condition'],
                 'Data':x['evaluation']['quality'], 'Recovered':x['recovered'], 'Note':x['evaluation']['note']} for x in observations]
        st.dataframe(pd.DataFrame(rows[-1000:][::-1]), hide_index=True, width='stretch')
        if identity:
            valid = [x for x in observations if x['evaluation']['quality'] == 'valid' and x['evaluation']['value'] is not None]
            if valid:
                chart = go.Figure(go.Scatter(x=[x['recorded_at'] for x in valid], y=[x['evaluation']['value'] for x in valid], mode='markers', name='Observed metric (%)'))
                st.plotly_chart(chart_hover(style_figure(chart)), width='stretch')
    else:
        st.info('No observations recorded yet.')
    st.markdown('**Alert history**')
    events = [x for x in doc['events'] if identity is None or x['rule_id'] == identity]
    if events:
        st.dataframe(pd.DataFrame([{k:e.get(k) for k in ('name','condition','observed_at','recorded_at','acknowledged','resolved','recovered')} for e in events[::-1]]), hide_index=True, width='stretch')
    else:
        st.caption('No alert episodes yet.')


def render_monitoring(directory, holdings, journal, *, demo=False):
    apply_monitoring_route()
    view = st.segmented_control('Monitoring view', ['Browse','Tracked rules','Account alerts','History'],
                                default='Browse', key='monitoring_view') or 'Browse'
    try:
        if view == 'Tracked rules':
            service = monitoring_for(directory, demo=demo)
            status, check = st.columns([4,1.5], vertical_alignment='center')
            status.caption('Last rule check · '+observation_time(service.last_success),
                help='Last completed evaluation pass over all enabled saved rules. Individual results may be stale or unavailable; see the Data column below. Checks run every five minutes while the app is open, on any tab.')
            if check.button('Check all rules now', key='monitoring_refresh', width='stretch',
                            help='Request a fresh evaluation of all enabled saved rules. This does not refer to the instrument being explored in Browse.'):
                service.schedule(refresh=True)
            if service.error:
                st.warning(service.error)
        _monitoring_banners(load_monitoring(directory)[0])
        if view == 'Browse':
            _browse(directory, holdings, demo=demo)
        elif view == 'Tracked rules':
            _tracked(directory, holdings, journal, demo=demo)
        elif view == 'Account alerts':
            _account_editor(directory, holdings, journal, demo=demo)
        else:
            _history(load_monitoring(directory)[0])
    except (DataError, OSError) as exc:
        st.error(str(exc))


def watch_monitoring(directory, *, demo=False, disabled=False):
    """Invisible heartbeat: evaluation remains active regardless of selected tab."""
    if disabled:
        return
    service = monitoring_for(directory, demo=demo)
    directory = service.directory
    try:
        doc, _ = load_monitoring(directory)
    except DataError:
        return  # The Monitoring view reports the corrupt document.
    enabled = any(r['enabled'] for r in doc['rules'])
    if enabled:
        service.schedule()
    from portfolio_app.market_data import coordinator
    revision = coordinator.revision(directory)
    @st.fragment(run_every=60 if enabled else None)
    def heartbeat():
        if enabled:
            service.schedule()
        if coordinator.revision(directory) != revision and refresh_allowed():
            st.rerun()
    heartbeat()


def _monitoring_banners(doc):
    for raw in doc['rules']:
        if not raw['enabled'] or raw['delivery'] == 'center':
            continue
        state = doc['states'].get(raw['id'], {})
        if state.get('condition') in {'approaching', 'triggered'}:
            if not any(e['rule_id'] == raw['id'] and e['revision'] == raw['revision'] and
                       e['condition'] == state['condition'] and not e['acknowledged'] and not e['resolved'] for e in doc['events']):
                continue
            status = state['condition']
            latest = state.get('last_valid', {})
            message = f"{raw['name']} · {status.title()} · {_value(latest.get('value'))} · observed {latest.get('observed_at','—')}"
            if latest.get('value') is None and latest.get('note'):
                message += ' · '+latest['note']
            if state.get('quality') != 'valid':
                message += ' · Current data unavailable; showing last valid condition'
            with st.container(border=True):
                (st.error if status == 'triggered' else st.warning)(message)
                st.button('View rule', key='monitoring_banner_'+raw['id'], on_click=open_rule, args=(raw['id'],))


def render_alert_dialog(directory):
    # All tabs, including Browse and inline editors, can receive alerts.
    # Streamlit permits only one modal per run; queue behind another modal.
    if st.session_state.get('tour_welcome') or st.session_state.get('position_edit_dialog') or any(
        value for key,value in st.session_state.items() if key.endswith(('_dialog_open','_dialog_requested'))):
        return
    try:
        doc, _ = load_monitoring(directory)
    except DataError:
        return
    rules = {r['id']:r for r in doc['rules'] if r['enabled'] and r['delivery'] == 'dialog'}
    events = [e for e in doc['events'] if e['rule_id'] in rules and e['revision'] == rules[e['rule_id']]['revision']
              and e['condition'] == 'triggered' and not e['acknowledged'] and not e.get('resolved')
              and doc['states'].get(e['rule_id'],{}).get('quality') == 'valid']
    shown = st.session_state.setdefault('monitoring_presented', [])
    events = [e for e in events if e['id'] not in shown]
    if not events:
        return
    def dismiss():
        shown.extend(e['id'] for e in events if e['id'] not in shown)
    def review(identity):
        dismiss()
        open_rule(identity)
    @st.dialog('Monitoring alert', width='large', icon=':material/notification_important:', on_dismiss=dismiss)
    def dialog():
        if st.session_state.get('monitoring_route'):
            st.rerun()
        st.html('<style>[role="dialog"]:has(.st-key-monitoring_alert_body) [slot="title"] p {font-size:1.5rem;font-weight:750;}</style>')
        with st.container(key='monitoring_alert_body'):
            for event in events:
                rule = rule_from_dict(rules[event['rule_id']])
                render_alert_event(directory, rule, event, review=review)
    dialog()


def render_alert_event(directory, rule, event, *, review=None):
    """Shared alert explanation and acknowledgment for delivery and the isolated tour."""
    condition_text, observed = alert_details(rule, event['evaluation'])
    st.markdown(f'Your rule **{event["name"]}** has triggered.')
    st.markdown('**Condition:** '+condition_text)
    st.markdown('**Observed:** '+observed)
    st.caption(observation_time(event['observed_at']) + (' · Recovered completed-close event' if event.get('recovered') else ''))
    with st.expander('Observation details'):
        evaluation = event['evaluation']
        if rule.kind == 'sma':
            st.write(f'{rule.series.source} · {rule.series.currency} · {rule.series.convention}')
            a,b = st.columns(2)
            a.metric('Observed price', _value(evaluation.get('price'), ' '+rule.series.currency))
            b.metric('SMA', _value(evaluation.get('reference'), ' '+rule.series.currency))
        else:
            st.write('Recorded account inputs · '+rule.currency)
        if evaluation.get('note'):
            st.write(evaluation['note'])
    a,b = st.columns(2)
    a.button('View rule', key='monitoring_dialog_'+event['id'], on_click=review, args=(event['rule_id'],), width='stretch', disabled=review is None)
    if b.button('Acknowledge alert', type='primary', key='monitoring_ack_'+event['id'], width='stretch',
                help='Silence this alert episode. The rule remains enabled and rearms after recovery.'):
        mark_events(directory, [event['id']], acknowledge=True)
        st.rerun()
