"""Data-quality notices kept out of the portfolio's main views."""
import streamlit as st
from hashlib import sha256
import json

from portfolio_app.interface.views.funds.etf_refresh_ui import REFRESH_PROGRESS, refresh_notices, render_refresh_status
from portfolio_app.funds.etf_refresh import coordinator


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
    from portfolio_app.interface.shared.input_cache import monitoring_document
    from portfolio_app.portfolio.holdings import DataError
    try:
        return monitoring_document(directory)
    except DataError:
        return {'events': [], 'notice_seen': [], 'rules': []}


def notification_bell(slot, directory, funds, valued, status, *, etf_revision=None, demo=False, cache_warning='', exposure_issues=(), disabled=False, journal=None, background_prices=False):
    navigated = False

    def follow_notice(destination):
        nonlocal navigated
        navigate(destination, directory)
        navigated = True

    with slot:
        def indicator(revision):
            nonlocal navigated
            if navigated:
                navigated = False
                st.rerun(scope='app')
            panel = st.popover('Notifications', icon=':material/notifications:',
                help='Notifications', key='workspace_notifications', on_change='rerun',
                width='stretch', disabled=disabled)
            refresh_messages, refresh_issues = refresh_notices(directory, funds, demo=demo, revision=revision)
            from portfolio_app.interface.views.derivatives.settlement_archive_ui import settlement_notices
            settlement_messages, settlement_issues = settlement_notices(directory, journal)
            current = notification_fingerprints(valued, status,
                refresh_issues=(*refresh_issues, *settlement_issues),
                cache_warning=cache_warning, exposure_issues=exposure_issues)
            doc = _monitoring_document(directory)
            seen = st.session_state.setdefault('notification_seen', {})
            workspace = str(directory.resolve())
            saved_seen = set(doc['notice_seen']) | set(seen.get(workspace, ()))
            count = len(current-saved_seen) + sum(not e['read'] for e in doc['events'])
            _, seen[workspace] = unread_notifications(current, saved_seen, opened=panel.open)
            from portfolio_app.monitoring.monitoring_store import remember_notices, mark_events
            from portfolio_app.portfolio.holdings import DataError
            try:
                remember_notices(directory, current, opened=panel.open, previously_seen=doc['notice_seen'])
                if panel.open:
                    mark_events(directory, [e['id'] for e in doc['events'] if not e['read']])
            except (DataError, OSError):
                pass  # The monitoring area provides persistence recovery guidance.
            badge = str(min(count, 99)) if count and not panel.open else ''
            st.html('<style>.st-key-header_notifications [data-testid="stPopoverButton"]::after {'
                + f'content:"{badge}";display:{"block" if badge else "none"};'
                + 'width:auto;min-width:15px;height:15px;padding:0 2px;right:0;top:0;font-size:10px;color:white;line-height:15px;border-radius:8px;text-align:center;}</style>')
            if panel.open:
                from portfolio_app.market.market_data import coordinator as market
                with panel:
                    render_notifications(directory, valued, status, doc, refresh_messages, settlement_messages,
                        etfs_pending=not demo and coordinator.running(directory),
                        prices_pending=background_prices and market.pending(workspace), cache_warning=cache_warning,
                        exposure_issues=exposure_issues, on_navigate=follow_notice)

        render_refresh_status(directory, funds, etf_revision, demo=demo, show_messages=False,
                              render_indicator=indicator, periodic=bool(journal and journal.positions and not demo))


def navigate(destination, directory):
    from portfolio_app.interface.views.derivatives.accounts_ui import _key
    from portfolio_app.interface.views.monitoring.monitoring_ui import open_rule
    from portfolio_app.interface.views.positions.position_ui import request_position
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


def _card(notice, on_navigate):
    with st.container(border=True):
        st.markdown(f'**{notice.title}**')
        st.markdown(notice.message)
        st.caption(notice.severity.title() + (f' · Observed {notice.observed_at}' if notice.observed_at else ''))
        st.button(notice.action, key='notice_action_'+notice.id, on_click=on_navigate, args=(notice.destination,))


def render_notifications(directory, valued, status, doc, refresh_messages, settlement_messages, *,
                         etfs_pending, prices_pending, cache_warning, exposure_issues, on_navigate):
    from portfolio_app.interface.shared.notification_model import Notice, Destination
    from portfolio_app.monitoring.monitoring_store import mark_events
    st.subheader('Notifications')
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
        _card(Notice(event['id'], event['name'], message, event['condition'], Destination('rule',event['rule_id']), 'View rule', event['observed_at']), on_navigate)
        if st.button('Acknowledge', key='notice_ack_'+event['id']):
            mark_events(directory, [event['id']], acknowledge=True)
            st.rerun()
    # Explicitly clear provider status when the last progress message or card goes away.
    provider_status = st.empty()
    if refresh_messages or etfs_pending or settlement_messages:
        with provider_status.container():
            for i, message in enumerate(refresh_messages):
                _card(Notice('etf_'+str(i), 'ETF data', message, 'warning',
                    Destination('sources'), 'Review ETF sources'), on_navigate)
            if etfs_pending:
                st.caption(REFRESH_PROGRESS)
            for i, message in enumerate(settlement_messages):
                _card(Notice('settlement_'+str(i), 'Futures data', message, 'warning',
                    Destination('settlements'), 'Review settlement history'), on_navigate)
    if prices_pending:
        st.caption('Updating market prices…')
    manual = int(valued.price_status.eq('manual').sum())
    if manual:
        _card(Notice('manual', 'Manually maintained prices', f'{manual} dated manual/snapshot prices · Update these manually or connect live prices.',
                    'information', Destination('pricing'), 'Review manual prices'), on_navigate)
    if cache_warning:
        _card(Notice('cache', 'Price cache needs attention', cache_warning, 'warning', Destination('pricing'), 'Review pricing'), on_navigate)
    if status.estimated_costs:
        _card(Notice('estimated', 'Estimated acquisition FX', f'{status.estimated_costs} positions use estimated FX costs. Performance can change when acquisition conversions are completed.',
                    'information', Destination('costs'), 'Review acquisition costs'), on_navigate)
    for i,issue in enumerate(dict.fromkeys(exposure_issues)):
        _card(Notice('exposure_'+str(i), 'Exposure data needs attention', issue, 'warning', Destination('exposure'), 'Review exposure sources'), on_navigate)
    if status.missing_costs or status.missing_prices or status.stale_quotes:
        st.markdown('**Prices & performance**')
        st.dataframe(status.problems, hide_index=True, width='stretch')
        st.button('Complete buy-ins' if status.missing_costs else 'Review pricing',
            on_click=on_navigate, args=(Destination('costs' if status.missing_costs else 'pricing'),))
