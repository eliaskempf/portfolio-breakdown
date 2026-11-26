"""Performance's read-only statement at a selected day's close."""
from datetime import date

import streamlit as st

from portfolio_app.interface.shared.currency_display import money, reporting_currency
from portfolio_app.interface.shared.list_ui import ListColumn, render_list
from portfolio_app.interface.views.history.history_data_ui import HistoryReasonFormatter, scope_names
from portfolio_app.interface.shared.number_format import return_percent
from portfolio_app.interface.views.history.history_ui import portfolio_history_scope, prepared_history_calculation
from portfolio_app.performance.history_diagnostics import issue_kind
from portfolio_app.performance.history_observations import load_observations
from portfolio_app.performance.portfolio_snapshot import portfolio_snapshot
from portfolio_app.portfolio.holdings import DataError


def open_date_view(default_date):
    st.session_state['strategic_historical_open'] = True
    st.session_state.setdefault('strategic_historical_date', default_date)


def _coverage_label(row):
    value = row.value
    if row.kind == 'Security' and row.quantity is None:
        return 'Supplied value · quantity unknown' if value.supplied else 'Quantity unknown'
    if value.amount is None:
        gaps = {issue_kind(reason) for reason in value.reasons}
        if gaps == {'price'}:
            return 'Missing price'
        if gaps == {'fx'}:
            return 'Missing FX'
        return 'Missing inputs'
    return 'Supplied value' if value.supplied else 'Valued'


def render_date_view(directory, config, holdings, displayed, journal, observation_snapshot, *, journal_revision):
    scope = portfolio_history_scope(directory, holdings, config, journal, '', displayed)
    observations, revision = observation_snapshot or load_observations(directory)
    calculation = prepared_history_calculation(directory, journal, observations, revision, journal_revision=journal_revision)
    names = scope_names(directory, journal, scope, holdings=holdings)
    formatter = HistoryReasonFormatter(journal, lambda: names)
    st.button('Back to Performance', key='historical_portfolio_back', type='tertiary', icon=':material/arrow_back:',
              on_click=st.session_state.update, kwargs={'strategic_historical_open': False})

    @st.fragment
    def statement():
        with st.container(key='historical_portfolio'):
            today = date.today()
            default = min(date.fromisoformat(st.session_state.get('strategic_historical_date') or today.isoformat()), today)
            earliest = calculation.event_dates(scope)
            minimum = min(default, date.fromisoformat(earliest[0]) if earliest else default,
                          date(today.year-10, 1, 1))
            heading = st.empty()
            stamp = st.date_input('Portfolio date', value=default, min_value=minimum, max_value=today,
                                  key=f'strategic_historical_picker_{directory.resolve()}_{scope.key}')
            if stamp is None:
                return
            st.session_state['strategic_historical_date'] = stamp.isoformat()
            currency = reporting_currency()
            heading.subheader(f'Portfolio on {stamp.isoformat()}')
            st.caption(f'End of day · {currency}', help='Account equity includes cash, debt, outstanding payments and unsettled futures profit or loss.')
            try:
                view = portfolio_snapshot(calculation, scope, stamp.isoformat(), currency)
            except (DataError, ValueError) as exc:
                st.error(str(exc))
                return
            _metrics(view, formatter)
            _holdings(view, names, currency, scope.key)
            _account_balances(view, names, scope.key)
            _coverage_details(view, names, formatter)
    statement()


def _metrics(view, formatter):
    total, gain, twr = st.columns(3)
    total.metric('Historical value', money(view.value.amount), help='\n'.join(formatter.format(view.value.reasons)) or
                 ('Supplied total valuation.' if view.value.supplied else 'Total recorded equity at the selected close.'))
    if view.value.amount is None and view.known_subtotal is not None:
        total.caption(f'Partial · Known subtotal {money(view.known_subtotal)}')
    elif view.value.supplied:
        total.caption('Supplied total', help='Dated total valuation. Individual values and quantities need their own evidence.')
    gain_label = 'Known gain' + (' · partial' if view.gain.amount is not None and view.gain.partial else '')
    gain.metric(gain_label, money(view.gain.amount, signed=True), help='\n'.join(formatter.format(view.gain.reasons)) or view.gain.coverage_help)
    return_label = 'TWR' + (' · estimated' if view.estimated_return and view.time_weighted is not None else '')
    twr.metric(return_label, return_percent(view.time_weighted*100 if view.time_weighted is not None else None),
               help='\n'.join(formatter.format(view.return_reasons)) or f'Cumulative time-weighted return since {view.return_start}.')



def _holdings(view, names, currency, context):
    records = []
    for row in view.rows:
        amount = row.value.amount
        records.append({'id': row.kind + ':' + row.identity, 'Holding': names.get(row.identity, row.identity), 'Type': row.kind,
                        'Quantity': float(row.quantity) if row.quantity is not None else None,
                        'Value': float(amount) if amount is not None else None,
                        'Weight (%)': float(100*amount/view.value.amount) if amount is not None and view.value.amount else None,
                        'Coverage': _coverage_label(row)})
    if records:
        columns = [ListColumn('Holding', 'Holding', secondary='Type'), ListColumn('Quantity', 'Quantity', numeric=True, decimals=10),
                   ListColumn('Value', f'Value ({currency})', numeric=True), ListColumn('Weight (%)', 'Weight (%)', numeric=True),
                   ListColumn('Coverage', 'Coverage')]
        render_list(records, columns, key='historical_holdings_list', title='Historical holdings',
                    context=context, default_sort='Value')
    else:
        st.caption('No recorded holdings on this date.')



def _account_balances(view, names, context):
    accounts = [row for row in view.rows if row.components]
    if not accounts:
        return
    panel = st.expander('Account balances', on_change='rerun')
    if panel.open:
        with panel:
            balances = []
            for row in accounts:
                for index, part in enumerate(row.components):
                    balances.append({'id': f'{row.identity}:{index}', 'Account': names[row.identity],
                                     'Balance': part.label, 'Amount': float(part.amount) if part.amount is not None else None,
                                     'Unit': part.unit})
            render_list(balances, [ListColumn('Account', 'Account'), ListColumn('Balance', 'Balance'),
                        ListColumn('Amount', 'Amount', numeric=True, decimals=10), ListColumn('Unit', 'Unit')],
                        key='historical_account_balances', title='Account balances', context=context)



def _coverage_details(view, names, formatter):
    panel = st.expander('Coverage and valuation dates', on_change='rerun')
    if not panel.open:
        return
    with panel:
        for row in view.rows:
            reasons = list(formatter.format(row.value.reasons))
            reasons.extend(f'{q.kind} · {q.as_of} · {q.source}' for q in row.value.evidence)
            if reasons:
                st.text(names.get(row.identity, row.identity) + ' — ' + ' · '.join(dict.fromkeys(reasons)))
        for reason in formatter.format((*view.value.reasons, *view.gain.reasons, *view.return_reasons)):
            st.text(str(reason))
        if view.value.supplied:
            for mark in view.value.evidence:
                st.text(f'Supplied total · {mark.as_of} · {mark.source}')
