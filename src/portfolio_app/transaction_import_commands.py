"""Revision-checked transaction evidence and atomic batch activation."""
from dataclasses import replace
from datetime import date
from io import StringIO
from pathlib import Path

import pandas as pd

from portfolio_app.account_journal import load_journal, save_journal
from portfolio_app.actual_portfolio import freeze_position_keys
from portfolio_app.holdings import DataError, parse_holdings
from portfolio_app.positions import EMPTY_CSV, read_snapshot
from portfolio_app.security_history import effective_holdings, selected_events, affected_positions
from portfolio_app.storage import save_document, revision as file_revision
from portfolio_app.csv_transaction_imports import prepare_import
from portfolio_app.workspace_lock import workspace_lock


def prepare_positions(directory, additions, *, expected_holdings_revision):
    """Explicit identity preparation only: new positions always have zero units.

    Repeating the same prepared IDs is harmless. Financial activation is a later
    journal publication; a failed publication can leave these empty placeholders.
    """
    directory = Path(directory)
    with workspace_lock(directory):
        path = directory/'holdings.csv'
        snapshot = read_snapshot(path)
        if snapshot.revision != expected_holdings_revision:
            raise DataError('Holdings changed. Review the mappings again.')
        content = positions_content(path.read_text(encoding='utf-8-sig') if path.exists() else '', snapshot, additions)
        _validate_categories(directory, parse_holdings(content))
        save_document(path,content,snapshot.revision)
        return read_snapshot(path)


def save_transaction_import(directory, rows, mapping, *, expected_journal_revision,
                            expected_holdings_revision, accept_reconciliation=False,
                            accept_checkpoints=False,
                            comparison_date='', additions=(), listing_changes=None, expected_observations_revision=...,
                            expected_reporting_currency=None, expected_allocation_revision=..., **options):
    """Recompute under the lock; evidence and all activated legs publish together."""
    directory = Path(directory)
    rows = tuple(rows)
    with workspace_lock(directory):
        journal, revision = load_journal(directory)
        snapshot = read_snapshot(directory/'holdings.csv')
        if revision != expected_journal_revision or snapshot.revision != expected_holdings_revision:
            raise DataError('History or holdings changed. Reload and review again.')
        if expected_allocation_revision is not ... and file_revision(directory/'allocation.yaml') != expected_allocation_revision:
            raise DataError('Categories or targets changed. Reload and review the import again.')
        selected = tuple(options.get('selected_ids', ()))
        excluded = tuple(options.get('excluded_ids', ()))
        if selected or excluded:
            if not accept_reconciliation:
                raise DataError('Review and accept the reconstructed balances and cash changes first.')
            if any(r.identity in selected and r.facts['event_type'] == 'checkpoint' for r in rows) and not accept_checkpoints:
                raise DataError('Explicitly accept balance checkpoints and their unresolved differences.')
            try:
                compared = date.fromisoformat(comparison_date)
                if compared > date.today():
                    raise ValueError
            except ValueError:
                raise DataError('Supply the actual comparison date, no later than today.') from None
            if any(r.date > comparison_date for r in rows if r.identity in selected):
                raise DataError('The comparison date must cover the selected transactions.')
        from portfolio_app.history_observations import ObservationFX, load_observations
        from portfolio_app.portfolio_settings import load_settings
        observations, observation_revision = load_observations(directory)
        target = load_settings(directory).reporting_currency
        if (selected or excluded) and ((expected_observations_revision is not ... and observation_revision != expected_observations_revision)
                         or (expected_reporting_currency is not None and expected_reporting_currency != target)):
            raise DataError('Valuation evidence or reporting currency changed. Review reconciliation again.')
        content = None
        preview_holdings = snapshot.holdings
        if (selected or excluded) and (additions or listing_changes):
            path = directory/'holdings.csv'
            content = positions_content(path.read_text(encoding='utf-8-sig') if path.exists() else '', snapshot, additions, listing_changes=listing_changes)
            preview_holdings = parse_holdings(content)
        preview = prepare_import(journal, preview_holdings, rows, mapping,
            target=target, historical=ObservationFX(observations), **options)
        proposed = preview.journal
        if content is not None:
            # Rejected new positions remain reserved evidence identities, not
            # zero-unit holdings or unused price-listing changes.
            applied_positions = {c['position_id'] for c in preview.comparisons}
            kept_additions = tuple(a for a in additions if a['position_key'] in applied_positions)
            applied_assets = set(preview_holdings.loc[preview_holdings.position_id.isin(applied_positions), 'id'])
            kept_listings = {a: listing for a, listing in (listing_changes or {}).items() if a in applied_assets}
            content = (positions_content((directory/'holdings.csv').read_text(encoding='utf-8-sig')
                if (directory/'holdings.csv').exists() else '', snapshot, kept_additions, listing_changes=kept_listings)
                if kept_additions or kept_listings else None)
            preview_holdings = parse_holdings(content) if content is not None else snapshot.holdings
        if selected:
            touched = {c['position_id'] for c in preview.comparisons}
            if any(e.date > comparison_date for b in proposed.securities for e in selected_events(b)
                   if affected_positions(b,e).intersection(touched)):
                raise DataError('The comparison date must cover all active movements in these positions.')
        if selected and preview.applied:
            touched = {c['position_id'] for c in preview.comparisons}
            from portfolio_app.history_commands import record_reviews
            proposed = record_reviews(proposed, touched, comparison_date)
        if proposed == journal:
            return replace(preview,journal=journal)
        effective_holdings(preview_holdings, proposed.securities)
        _validate_categories(directory, preview_holdings)
        if content is not None:
            from portfolio_app.history_publication import publish_positions_and_history
            publish_positions_and_history(directory, content, proposed, snapshot.revision, revision)
        else:
            snapshot = freeze_position_keys(directory, snapshot.revision)
            save_journal(directory, proposed, revision)
        return replace(preview,journal=proposed)


def _validate_categories(directory, holdings):
    from portfolio_app.allocation import load_allocation
    allocation = load_allocation(Path(directory)/'allocation.yaml', holdings)
    if allocation is None and 'bucket_id' in holdings and holdings.bucket_id.ne('').any():
        raise DataError('Categories are unavailable. Choose Unassigned or restore the categories before applying.')


def positions_content(content, snapshot, additions, *, listing_changes=None):
    raw = pd.read_csv(StringIO(content or EMPTY_CSV), dtype=str, keep_default_na=False)
    if 'position_key' not in raw:
        raw['position_key'] = snapshot.holdings.position_id.tolist()
    if 'account' not in raw:
        raw['account'] = snapshot.holdings.account.tolist()
    existing = set(raw.position_key)
    for item in additions:
        allowed = {'position_key','id','name','account','instrument_type','ticker','isin','bucket_id'}
        if set(item) - allowed or any(not item.get(k) for k in ('position_key','id','name','account')):
            raise DataError('Supply an explicit instrument, custody account and stable position identity.')
        if item['position_key'] in existing:
            old = raw.loc[raw.position_key.eq(item['position_key'])].iloc[0]
            if any(str(old.get(k,'')) != v for k,v in item.items()):
                raise DataError('Prepared identity already has different metadata.')
            continue
        if ((raw.id == item['id']) & (raw.account == item['account'])).any():
            raise DataError('This custody position already exists. Map it instead of creating another.')
        for key in item:
            if key not in raw:
                raw[key] = ''
        raw.loc[len(raw)] = {c: '' for c in raw.columns}
        for k,v in {**item,'shares':'0'}.items():
            raw.at[len(raw)-1,k] = v
        existing.add(item['position_key'])
    content = raw.to_csv(index=False, lineterminator='\n')
    from portfolio_app.imports import listing_content
    for instrument, listing in (listing_changes or {}).items():
        content = listing_content(content, instrument, listing)
    parse_holdings(content)
    return content
