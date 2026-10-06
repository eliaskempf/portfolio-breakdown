"""Lightweight status and preferences for the app-owned background updater."""
from pathlib import Path
import streamlit as st

from portfolio_app.display_names import compact_fund_name
from portfolio_app.etf import snapshot_age_days, matching_fund
from portfolio_app.etf_refresh import coordinator, preferences, save_preferences, read_json, supported, discovery_candidates


def refresh_revision(data_dir):
    try:
        return (Path(data_dir) / '.cache' / 'etf-refresh' / 'status.json').stat().st_mtime_ns
    except OSError:
        return 0


def render_refresh_status(data_dir, funds, revision, *, demo=False):
    if demo:
        return  # Offline fixtures have invented dates, not stale provider data.
    running = not demo and coordinator.running(data_dir)

    @st.fragment(run_every=2 if running else None)
    def status():
        pending = not demo and coordinator.running(data_dir)
        if not pending and (running or refresh_revision(data_dir) != revision):
            st.rerun()
        if pending:
            st.caption('Discovering and updating ETF holdings · Using saved snapshots')
        else:
            records = read_json(Path(data_dir) / '.cache' / 'etf-refresh' / 'status.json') if not demo else {}
            failed = sum(records.get(f.isin, {}).get('status') == 'failed' for f in funds)
            old = sum(snapshot_age_days(f) > 7 for f in funds)
            parts = ([f'{old} outdated ETF snapshot(s)'] if old else []) + ([f'{failed} refresh(es) failed; saved data retained'] if failed else [])
            if coordinator.error(data_dir) and not demo:
                parts.append('ETF refresh unavailable')
            if parts:
                st.caption(' · '.join(parts))
    status()


def render_refresh_controls(data_dir, holdings, funds, *, demo=False):
    prefs = preferences(data_dir)
    context = str(Path(data_dir).resolve())
    enabled = st.checkbox('Automatically refresh ETF holdings', help='Refresh stale fund snapshots in the background when supported sources are available.', value=prefs['enabled'], disabled=demo,
                          key=f'etf_auto_{context}')
    days = st.number_input('Refresh snapshots at least this many days old', help='Minimum snapshot age before an automatic refresh is requested.', min_value=1, max_value=30,
                           value=prefs['minimum_age_days'], disabled=demo, key=f'etf_age_{context}')
    if not demo and (enabled != prefs['enabled'] or days != prefs['minimum_age_days']):
        try:
            save_preferences(data_dir, enabled=enabled, minimum_age_days=days)
            coordinator.schedule(data_dir, holdings, funds)
        except OSError as exc:
            st.error(f'Could not save refresh preferences: {exc}')
    st.caption('Checked at startup and while the app is in use; at most once per fund per 24 hours. Offline demos do not download data.')
    refreshable = [f for f in funds if supported(f) and any(row.get('shares', 0) > 0 and matching_fund(row, [f]) for row in holdings.to_dict('records'))]
    candidates = discovery_candidates(holdings, funds)
    if st.button('Refresh ETF holdings now', help='Request fresh holdings from configured fund providers; failed downloads retain saved snapshots.', disabled=demo or coordinator.running(data_dir) or not (refreshable or candidates)):
        coordinator.schedule(data_dir, holdings, funds, force=True)
        st.rerun()
    records = read_json(Path(data_dir) / '.cache' / 'etf-refresh' / 'status.json') if not demo else {}
    for key, row in candidates.items():
        record = records.get(key, {})
        st.markdown(f"**{row.get('name', key)}**")
        st.caption(f"{key} · {record.get('status', 'Awaiting discovery')}")
        if record.get('error'):
            st.info(record['error'])
    unidentified = [row for row in holdings.to_dict('records') if row.get('shares', 0) > 0
                    and row.get('instrument_type') == 'etf' and not any(row.get(k) for k in ('isin', 'wkn', 'ticker'))]
    if unidentified:
        st.info('Some funds have no identifier. Use Positions → Connect live prices to select their ISIN; snapshot prices can be retained.')
    from portfolio_app.etf_setup_ui import render_setup
    render_setup(data_dir, holdings, funds, demo=demo)
    for fund in funds:
        record = records.get(fund.isin, {})
        st.markdown(f'**{compact_fund_name(fund.name)}**')
        st.caption(f'Provider holdings date: {fund.as_of} · {snapshot_age_days(fund)} day(s) old')
        if record.get('checked_at'):
            st.caption(f'Last successful check: {record["checked_at"]}')
        if record.get('attempted_at'):
            st.caption(f'Last attempt: {record["attempted_at"]}')
        if record.get('error'):
            st.warning(f'Update failed; saved holdings retained. {record["error"]}')
        if fund.proxy_source:
            st.info(f'Proxy: {fund.proxy_source}')
        if not supported(fund):
            st.caption('Manual snapshot · No automatic provider configured')
    if error := coordinator.error(data_dir):
        st.warning(error)
    from portfolio_app.etf_ui import render_fund_details
    held = [fund for fund in funds if any(row.get('shares', 0) > 0 and matching_fund(row, [fund])
                                         for row in holdings.to_dict('records'))]
    render_fund_details(held, holdings, holdings=holdings, key_prefix='settings_', percentages_only=True)
