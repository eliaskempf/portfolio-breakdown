"""Lightweight status and preferences for the app-owned background updater."""
from dataclasses import dataclass
from pathlib import Path
import streamlit as st

from portfolio_app.interface.shared.render_sections import render_sections
from portfolio_app.interface.shared.background_ui import refresh_allowed, status_fragment
from portfolio_app.interface.shared.input_cache import cached_input, file_stamp, fund_sources, loaded_fund_sources

from portfolio_app.interface.shared.display_names import compact_fund_name
from portfolio_app.funds.etf import snapshot_age_days, group_fund_positions
from portfolio_app.funds.etf_refresh import coordinator, preferences, save_preferences, read_json, supported, discovery_candidates


REFRESH_PROGRESS = 'Discovering and updating ETF holdings · Using saved snapshots'


@dataclass(frozen=True)
class RefreshRevision:
    status_file: tuple
    failures: tuple
    error: str

    @property
    def issues(self):
        return self.failures, self.error


def refresh_revision(data_dir):
    path = Path(data_dir) / '.cache' / 'etf-refresh' / 'status.json'
    try:
        stamp = file_stamp(path)
    except OSError:
        stamp = (str(path), None)

    def issues():
        return tuple(sorted((key, record['status'], record.get('error', ''))
                            for key, record in read_json(path).items() if isinstance(record, dict)
                            and record.get('status') in {'failed', 'unavailable'}))
    failures = cached_input('etf_refresh_failures', stamp, issues, immutable=True)
    return RefreshRevision(stamp, failures, coordinator.error(data_dir))


def refresh_notices(data_dir, funds, *, demo=False, revision=None):
    """Messages and stable issue identities, shared with the unread indicator."""
    if demo:
        return [], ()
    revision = revision or refresh_revision(data_dir)
    old = [(f.isin, str(f.as_of)) for f in funds if snapshot_age_days(f) > 7]
    fund_ids = {f.isin for f in funds}
    failed = [(key, error) for key, status, error in revision.failures if status == 'failed' and key in fund_ids]
    unavailable = [(key, error) for key, status, error in revision.failures if status == 'unavailable']
    error = revision.error
    parts = ([f'{len(old)} outdated ETF snapshot(s)'] if old else []) + (
        [f'{len(failed)} refresh(es) failed; saved data retained'] if failed else [])
    if unavailable:
        parts.append(f'{len(unavailable)} ETF breakdown(s) unavailable · See Exposure → Data & settings → ETF refresh & snapshots to retry')
    if error:
        parts.append('ETF refresh unavailable')
    identities = tuple((kind, *item) for kind, items in [('old', old), ('failed', failed), ('unavailable', unavailable)] for item in items)
    if error:
        identities += (('refresh-error', str(error)),)
    return parts, identities


def render_refresh_status(data_dir, funds, revision, *, demo=False, show_messages=True,
                          render_notice=None, include_notices=True, render_indicator=None, periodic=False):
    if demo and render_indicator is None:
        return
    if revision is None and not demo:
        revision = refresh_revision(data_dir)
    observed_pending = not demo and coordinator.running(data_dir)
    source_revision = loaded_fund_sources()

    @status_fragment(run_every=2 if periodic else None)
    def status(poll):
        nonlocal observed_pending, revision
        if demo:
            render_indicator(None)
            return
        pending = coordinator.running(data_dir)
        observed_pending |= pending
        current = refresh_revision(data_dir)
        completed = observed_pending or current != revision
        pending_render = False
        if not pending and completed:
            pending_render = source_revision is not None and fund_sources(Path(data_dir)) != source_revision
            if st.session_state.get('main_tabs') == 'Exposure' and st.session_state.get('exposure_refresh_open'):
                observation = st.session_state.get('etf_settings_observation')
                # Another window can finish a check while this panel has no active timer.
                if observation and observation[0] == str(Path(data_dir).resolve()):
                    _, shown, polling = observation
                    pending_render |= not polling and shown != current
            if pending_render and refresh_allowed():
                st.rerun()
            if not pending_render:
                observed_pending = False
                revision = current
        if (pending or pending_render) and not periodic:
            poll(2)
        if render_indicator is not None:
            render_indicator(revision if pending else current)
        if not show_messages:
            return
        sections = render_sections('etf_refresh_', 'progress', 'notices')
        notice = render_notice or st.caption
        with sections['progress']:
            if pending:
                notice(REFRESH_PROGRESS)
        if not include_notices:
            return
        with sections['notices']:
            parts, _ = refresh_notices(data_dir, funds, revision=current)
            if render_notice is not None:
                for part in parts:
                    notice(part)
            elif parts:
                notice(' · '.join(parts))
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
    st.caption('Successful checks: at most daily. Failed attempts can retry after five minutes, or immediately after provider fixes. Offline demos do not download data.')
    rows = holdings.to_dict('records')
    groups = group_fund_positions(funds, (row for row in rows if row.get('shares', 0) > 0))
    held = [fund for fund, positions in zip(funds, groups, strict=True) if positions]
    candidates = discovery_candidates(holdings, funds)
    can_refresh = bool(candidates) or any(supported(fund) for fund in held)

    @status_fragment()
    def source_status(poll):
        pending = not demo and coordinator.running(data_dir)
        if not demo:
            st.session_state['etf_settings_observation'] = context, refresh_revision(data_dir), pending
        if pending:
            poll(2)
        if st.button('Refresh ETF holdings now', help='Request fresh holdings from configured fund providers; failed downloads retain saved snapshots.', disabled=demo or pending or not can_refresh):
            coordinator.schedule(data_dir, holdings, funds, force=True)
            st.rerun()
        records = read_json(Path(data_dir) / '.cache' / 'etf-refresh' / 'status.json') if not demo else {}
        for key, row in candidates.items():
            record = records.get(key, {})
            st.markdown(f"**{row.get('name', key)}**")
            st.caption(f"{key} · {record.get('status', 'Awaiting discovery')}")
            if record.get('error'):
                st.info(record['error'])
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

    source_status()
    unidentified = [row for row in rows if row.get('shares', 0) > 0
                    and row.get('instrument_type') == 'etf' and not any(row.get(k) for k in ('isin', 'wkn', 'ticker'))]
    if unidentified:
        st.info('Some funds have no identifier. Use Positions → Connect live prices to select their ISIN; snapshot prices can be retained.')
    from portfolio_app.interface.views.funds.etf_setup_ui import render_setup
    render_setup(data_dir, holdings, funds, demo=demo)
    from portfolio_app.interface.views.funds.etf_ui import render_fund_details
    render_fund_details(held, holdings, holdings=holdings, key_prefix='settings_', percentages_only=True)
