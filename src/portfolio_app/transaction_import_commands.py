"""Revision-checked transaction evidence and atomic batch activation."""
from dataclasses import replace
from datetime import date
from io import StringIO
from pathlib import Path
from uuid import uuid4

import pandas as pd

from portfolio_app.account_journal import load_journal, save_journal
from portfolio_app.actual_portfolio import freeze_position_keys
from portfolio_app.holdings import DataError, parse_holdings
from portfolio_app.positions import EMPTY_CSV, read_snapshot
from portfolio_app.security_history import HistoryReview, effective_holdings, selected_events, affected_positions
from portfolio_app.storage import save_document
from portfolio_app.transaction_imports import prepare_import
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
        raw = pd.read_csv(StringIO(path.read_text(encoding='utf-8-sig') if path.exists() else EMPTY_CSV), dtype=str, keep_default_na=False)
        if 'position_key' not in raw:
            raw['position_key'] = snapshot.holdings.position_id.tolist()
        existing = set(raw.position_key)
        for item in additions:
            allowed = {'position_key','id','name','account','instrument_type'}
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
        content = raw.to_csv(index=False)
        parse_holdings(content)
        save_document(path,content,snapshot.revision)
        return read_snapshot(path)


def save_transaction_import(directory, rows, mapping, *, expected_journal_revision,
                            expected_holdings_revision, accept_reconciliation=False,
                            accept_checkpoints=False,
                            comparison_date='', expected_observations_revision=...,
                            expected_reporting_currency=None, **options):
    """Recompute under the lock; evidence and all activated legs publish together."""
    directory = Path(directory)
    rows = tuple(rows)
    with workspace_lock(directory):
        journal, revision = load_journal(directory)
        snapshot = read_snapshot(directory/'holdings.csv')
        if revision != expected_journal_revision or snapshot.revision != expected_holdings_revision:
            raise DataError('History or holdings changed. Reload and review again.')
        selected = tuple(options.get('selected_ids', ()))
        if selected:
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
        if selected and ((expected_observations_revision is not ... and observation_revision != expected_observations_revision)
                         or (expected_reporting_currency is not None and expected_reporting_currency != target)):
            raise DataError('Valuation evidence or reporting currency changed. Review reconciliation again.')
        preview = prepare_import(journal, snapshot.holdings, rows, mapping,
            target=target, historical=ObservationFX(observations), **options)
        proposed = preview.journal
        if selected:
            touched = {c['position_id'] for c in preview.comparisons}
            if any(e.date > comparison_date for b in proposed.securities for e in selected_events(b)
                   if affected_positions(b,e).intersection(touched)):
                raise DataError('The comparison date must cover all active movements in these positions.')
        if selected and preview.applied:
            touched = {c['position_id'] for c in preview.comparisons}
            books = []
            for book in proposed.securities:
                if book.position_id in touched:
                    receipt = HistoryReview(uuid4().hex, book.active_ids, comparison_date, 'accept')
                    book = replace(book,reviews=(*book.reviews,receipt))
                books.append(book)
            proposed = replace(proposed,securities=tuple(books))
        if proposed == journal:
            return replace(preview,journal=journal)
        snapshot = freeze_position_keys(directory,snapshot.revision)
        effective_holdings(snapshot.holdings,proposed.securities)
        save_journal(directory,proposed,revision)
        return replace(preview,journal=proposed)
