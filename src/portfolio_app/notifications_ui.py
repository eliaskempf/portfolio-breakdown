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


def notification_bell(slot, directory, funds, valued, status, *, demo=False, cache_warning='', exposure_issues=(), disabled=False):
    _, refresh_issues = refresh_notices(directory, funds, demo=demo)
    current = notification_fingerprints(valued, status, refresh_issues=refresh_issues,
        cache_warning=cache_warning, exposure_issues=exposure_issues)
    seen = st.session_state.setdefault('notification_seen', {})
    workspace = str(directory.resolve())
    unread, _ = unread_notifications(current, seen.get(workspace, ()))
    with slot:
        panel = st.popover('Notifications', icon=':material/notifications:',
            help='Unread notifications' if unread else 'Notifications',
            key='workspace_notifications', on_change='rerun', width='stretch', disabled=disabled)
    unread, seen[workspace] = unread_notifications(current, seen.get(workspace, ()), opened=panel.open)
    if unread:
        st.html('<style>.st-key-header_notifications [data-testid="stPopoverButton"]::after {content:"";}</style>')
    return panel


def _render_notice(message):
    with st.container(border=True):
        st.markdown(message)


def render_notifications(directory, funds, revision, valued, status, *, demo=False,
                         cache_warning='', exposure_issues=(), prices_pending=False):
    st.subheader('Notifications')
    render_refresh_status(directory, funds, revision, demo=demo, render_notice=_render_notice)
    if prices_pending:
        _render_notice('Updating market prices…')
    manual = int(valued.price_status.eq('manual').sum())
    if manual:
        _render_notice(f'{manual} dated manual/snapshot prices · Update these manually or connect live prices.')
    if cache_warning:
        _render_notice(cache_warning)
    if status.estimated_costs:
        _render_notice(f'{status.estimated_costs} positions use estimated FX costs.')
    for issue in dict.fromkeys(exposure_issues):
        _render_notice(issue)
    if status.missing_costs or status.missing_prices or status.stale_quotes:
        st.markdown('**Prices & performance**')
        st.dataframe(status.problems, hide_index=True, width='stretch')
        st.button('Complete buy-ins' if status.missing_costs else 'Review pricing',
            on_click=lambda: st.session_state.update(main_tabs='Positions', positions_workflow_request='Update balances'))
