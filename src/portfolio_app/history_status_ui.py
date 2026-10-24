"""One actionable history checklist inside the existing Positions views."""
from datetime import date
from hashlib import sha256

import streamlit as st

from portfolio_app.account_journal import load_journal
from portfolio_app.currency_display import reporting_currency
from portfolio_app.display_names import instrument_name
from portfolio_app.history_data_ui import readable_reasons
from portfolio_app.history_observations import HistoryScope, load_observations
from portfolio_app.history_status import position_history_statuses
from portfolio_app.input_cache import cached_input
from portfolio_app.list_ui import ListColumn, render_list
from portfolio_app.position_list import list_context


def render_history_status_list(path, snapshot, holdings, *, demo=False):
    from portfolio_app.personal_history_refresh import refresher, RefreshStatus
    journal, _ = load_journal(path.parent)
    selected = set(holdings.position_id)
    owners = {p.id: p.account_id for p in (*journal.positions, *journal.investments)}
    owners.update({p.position_id: p.account_id for p in journal.cash_links})
    accounts = {owners[p] for p in selected if p in owners}
    accounts.update(getattr(row, 'account_id', '') for row in holdings.itertuples()
                    if getattr(row, 'account_id', '') in {a.id for a in journal.accounts})
    scope = HistoryScope(tuple(b.position_id for b in journal.securities if b.active and b.position_id in selected), tuple(accounts))
    target = reporting_currency()
    refresh = RefreshStatus() if demo else refresher.request(path.parent, journal, scope, target)

    @st.fragment(run_every=2 if refresh.pending else None)
    def region():
        nonlocal refresh
        if refresh.pending and not refresher.pending(path.parent):
            current, _ = load_journal(path.parent)
            refresh = refresher.request(path.parent, current, scope, target)
        if refresh.pending:
            st.caption('Loading historical prices and FX…')
        elif refresh.errors:
            if st.button('Retry prices', key='history_status_retry'):
                current, _ = load_journal(path.parent)
                refresher.request(path.parent, current, scope, target, force=True)
                st.rerun()
        _render_history_status_list(path, snapshot, holdings)
    region()


def _render_history_status_list(path, snapshot, holdings):
    journal, journal_revision = load_journal(path.parent)
    observations, observations_revision = load_observations(path.parent)
    target = reporting_currency()
    signature = (str(path.parent.resolve()), snapshot.revision, journal_revision, observations_revision,
                 target, date.today(), tuple((row.position_id, getattr(row, 'instrument_type', ''), getattr(row, 'account_id', '')) for row in holdings.itertuples()))
    statuses = cached_input('position_history_status', signature,
        lambda: position_history_statuses(holdings, journal, observations, target=target))
    only_issues = st.checkbox('Only show positions needing attention', value=True, key='positions_history_only_issues',
        help='Checks use saved evidence and the recorded interval. Matching balances cannot prove that no transactions are missing.')
    st.caption('Select a position to review its history.')
    names = {row.position_id: instrument_name(row) for row in holdings.itertuples()}
    details = {item.position_id: item for item in statuses}
    rows = []
    for item in statuses:
        if only_issues and not item.needs_attention:
            continue
        explanations = readable_reasons(path.parent, journal, item.scope, item.reasons, names=names)
        rows.append(dict(id=item.position_id, name=names[item.position_id], transactions=item.transactions,
                         market=item.market, money_weighted=item.money_weighted, time_weighted=item.time_weighted, freshness=item.freshness, action=item.action, explanation=((f'Checked {item.start} – {item.end}. ' if item.start else '')+' '.join(explanations[:4]))))
    if not rows:
        st.info('No missing history inputs detected for these positions.' if statuses else 'No positions to check.')
        return
    def open_position(event):
        item = details[event['id']]
        st.session_state['position_edit_open_request'] = dict(event, revision=snapshot.revision, history_target=item.target, history_screen=item.screen)
        from portfolio_app.dialog_runtime import route_position_callback
        route_position_callback()
    columns = [ListColumn('name', 'Position'), ListColumn('transactions', 'Transactions', tooltip='explanation'),
               ListColumn('market', 'Market data', tooltip='explanation'),
               ListColumn('money_weighted', 'Annualized', tooltip='explanation'),
               ListColumn('time_weighted', 'TWR', tooltip='explanation'),
               ListColumn('freshness', 'Valuation'), ListColumn('action', 'Next step')]
    render_list(rows, columns, key='positions_history_list', context=list_context(path), title='History status',
                revision=sha256(repr(signature).encode()).hexdigest(), on_open=open_position, search_label='Filter history',
                search_fields=['name', 'transactions', 'market', 'action'], default_sort='name')
