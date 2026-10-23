"""Data-quality notices kept out of the portfolio's main views."""
import streamlit as st
from hashlib import sha256
import json

from portfolio_app.etf_refresh_ui import refresh_notices, render_refresh_status


def notification_fingerprints(valued, status, *, refresh_issues=(), cache_warning='', exposure_issues=()):
    """Identify actual notices, excluding routine refresh progress and prices."""
    details = list(refresh_issues)
    manual = valued.loc[valued.price_status.eq('manual')]
    for row in manual.to_dict('records'):
        details.append(('manual', row.get('position_id'), str(row.get('quote_date', ''))))
    if cache_warning:
        details.append(('cache', cache_warning))
    if status.estimated_costs:
        details.append(('estimated-costs', status.estimated_costs))
    details.extend(('exposure', issue) for issue in exposure_issues)
    details.extend(('valuation', *row) for row in status.problems.itertuples(index=False, name=None))
    return {sha256(json.dumps(item, default=str).encode()).hexdigest() for item in details}


def unread_notifications(current, seen, *, opened=False):
    """Resolved notices are forgotten so a later recurrence becomes unread."""
    retained = set(seen).intersection(current)
    return (False, set(current)) if opened else (bool(set(current)-retained), retained)


def _monitoring_document(directory):
    from portfolio_app.monitoring_store import load_monitoring
    from portfolio_app.holdings import DataError
    try:
        return load_monitoring(directory)[0]
    except DataError:
        return {'events': [], 'notice_seen': [], 'rules': []}


def notification_bell(slot, directory, funds, valued, status, *, demo=False, cache_warning='', exposure_issues=(), disabled=False, journal=None):
    with slot:
        panel = st.popover('Notifications', icon=':material/notifications:',
            help='Notifications', key='workspace_notifications', on_change='rerun',
            width='stretch', disabled=disabled)

        @st.fragment(run_every=2 if journal and journal.positions and not demo else None)
        def indicator():
            _, refresh_issues = refresh_notices(directory, funds, demo=demo)
            from portfolio_app.settlement_archive_ui import settlement_notices
            _, settlement_issues = settlement_notices(directory, journal)
            current = notification_fingerprints(valued, status,
                refresh_issues=(*refresh_issues, *settlement_issues),
                cache_warning=cache_warning, exposure_issues=exposure_issues)
            doc = _monitoring_document(directory)
            seen = st.session_state.setdefault('notification_seen', {})
            workspace = str(directory.resolve())
            saved_seen = set(doc['notice_seen']) | set(seen.get(workspace, ()))
            count = len(current-saved_seen) + sum(not e['read'] for e in doc['events'])
            _, seen[workspace] = unread_notifications(current, saved_seen, opened=panel.open)
            from portfolio_app.monitoring_store import remember_notices, mark_events
            from portfolio_app.holdings import DataError
            try:
                remember_notices(directory, current, opened=panel.open)
                if panel.open:
                    mark_events(directory, [e['id'] for e in doc['events'] if not e['read']])
            except (DataError, OSError):
                pass  # The monitoring area provides persistence recovery guidance.
            badge = str(min(count, 99)) if count and not panel.open else ''
            st.html('<style>.st-key-header_notifications [data-testid="stPopoverButton"]::after {'
                + f'content:"{badge}";display:{"block" if badge else "none"};'
                + 'width:auto;min-width:15px;height:15px;padding:0 2px;right:0;top:0;font-size:10px;color:white;line-height:15px;border-radius:8px;text-align:center;}</style>')
        indicator()
    return panel


def navigate(destination, directory):
    from portfolio_app.accounts_ui import _key
    from portfolio_app.monitoring_ui import open_rule
    from portfolio_app.position_ui import request_position
    if destination.kind == 'rule':
        open_rule(destination.identity)
    elif destination.kind in {'pricing', 'costs'}:
        st.session_state.update(main_tabs='Positions', positions_workflow_request='Update balances')
        if destination.identity:
            request_position(destination.identity, editing=True, local=False)
    elif destination.kind in {'settlements', 'account'}:
        st.session_state.update(main_tabs='Futures', **{_key(directory)+'view':'Settlement history' if destination.kind == 'settlements' else 'Collateral'})
    else:
        st.session_state.update(main_tabs='Exposure', exposure_measure='Funded holdings analysis', exposure_notification_sources=True)
    st.session_state['workspace_notifications'] = False


def _render_notice(message):
    with st.container(border=True):
        st.markdown(message)


def _card(notice, directory, *, on_navigate=None):
    with st.container(border=True):
        st.markdown(f'**{notice.title}**')
        st.markdown(notice.message)
        st.caption(notice.severity.title() + (f' · Observed {notice.observed_at}' if notice.observed_at else ''))
        st.button(notice.action, key='notice_action_'+notice.id, on_click=on_navigate or navigate, args=(notice.destination,directory))


def render_notifications(directory, funds, revision, valued, status, *, demo=False,
                         cache_warning='', exposure_issues=(), prices_pending=False, journal=None):
    from portfolio_app.notification_model import Notice, Destination
    from portfolio_app.monitoring_store import mark_events
    from portfolio_app.settlement_archive_ui import settlement_notices
    st.subheader('Notifications')
    doc = _monitoring_document(directory)
    events = [e for e in doc['events'] if not e.get('resolved') and not e['acknowledged']]
    st.caption(f"{len(events)} active rule alert(s) · Opening this panel marks entries read; acknowledgment is separate.")
    for event in reversed(events):
        value = event['evaluation']['value']
        value_text = f'{value:,.2f}%' if value is not None else 'Undefined ratio'
        definition = next((r for r in doc['rules'] if r['id'] == event['rule_id']), {})
        message = f"{event['condition'].title()} · {value_text} · Trigger {definition.get('threshold', '—')}%"
        if value is None and event['evaluation'].get('note'):
            message += ' · '+event['evaluation']['note']
        if event.get('recovered'):
            message += ' · Recovered completed-close event'
        _card(Notice(event['id'], event['name'], message, event['condition'], Destination('rule',event['rule_id']), 'View rule', event['observed_at']), directory)
        if st.button('Acknowledge', key='notice_ack_'+event['id']):
            mark_events(directory, [event['id']], acknowledge=True)
            st.rerun()
    parts, _ = refresh_notices(directory, funds, demo=demo)
    for i,message in enumerate(parts):
        _card(Notice('etf_'+str(i), 'ETF data', message, 'warning', Destination('sources'), 'Review ETF sources'), directory)
    # Transient progress is a caption, separate from actionable/unread notices.
    render_refresh_status(directory, funds, revision, demo=demo, include_notices=False)
    action = []
    def follow_notice(destination, directory):
        navigate(destination, directory)
        action.append(True)
    @st.fragment(run_every=2 if journal and journal.positions and not demo else None)
    def settlement_cards():
        if action:
            action.clear()
            st.rerun(scope='app')
        messages, _ = settlement_notices(directory, journal)
        for i, message in enumerate(messages):
            _card(Notice('settlement_'+str(i), 'Futures data', message, 'warning',
                Destination('settlements'), 'Review settlement history'), directory, on_navigate=follow_notice)
    settlement_cards()
    if prices_pending:
        st.caption('Updating market prices…')
    manual = int(valued.price_status.eq('manual').sum())
    if manual:
        _card(Notice('manual', 'Manually maintained prices', f'{manual} dated manual/snapshot prices · Update these manually or connect live prices.',
                    'information', Destination('pricing'), 'Review manual prices'), directory)
    if cache_warning:
        _card(Notice('cache', 'Price cache needs attention', cache_warning, 'warning', Destination('pricing'), 'Review pricing'), directory)
    if status.estimated_costs:
        _card(Notice('estimated', 'Estimated acquisition FX', f'{status.estimated_costs} positions use estimated FX costs. Performance can change when acquisition conversions are completed.',
                    'information', Destination('costs'), 'Review acquisition costs'), directory)
    for i,issue in enumerate(dict.fromkeys(exposure_issues)):
        _card(Notice('exposure_'+str(i), 'Exposure data needs attention', issue, 'warning', Destination('exposure'), 'Review exposure sources'), directory)
    if status.missing_costs or status.missing_prices or status.stale_quotes:
        st.markdown('**Prices & performance**')
        st.dataframe(status.problems, hide_index=True, width='stretch')
        st.button('Complete buy-ins' if status.missing_costs else 'Review pricing',
            on_click=navigate, args=(Destination('costs' if status.missing_costs else 'pricing'), directory))
