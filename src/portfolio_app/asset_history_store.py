"""Explicit private settings for approximate asset history over CSV evidence."""
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
from pathlib import Path

from portfolio_app.holdings import DataError
from portfolio_app.transaction_csv import TransactionRow

FILE = 'asset-performance.json'


@dataclass(frozen=True)
class AssetHistorySettings:
    source_id: str
    owned_accounts: tuple[str, ...]
    anchor_date: str
    anchors: dict
    currency: str = 'EUR'
    note: str = ''
    scope_label: str = 'Selected owned locations'


def parse_settings(raw):
    try:
        if raw['version'] != 1 or raw['method'] != 'closing-balance-daily-dietz-v1':
            raise ValueError
        stamp = date.fromisoformat(raw['anchor_date'])
        if stamp > date.today():
            raise ValueError
        for key in ('source_id', 'currency'):
            if not isinstance(raw[key], str) or not raw[key]:
                raise ValueError
        if len(raw['currency']) != 3 or not raw['currency'].isupper():
            raise ValueError
        accounts = raw['owned_accounts']
        if not isinstance(accounts, list) or not accounts or any(not isinstance(a, str) or not a for a in accounts):
            raise ValueError
        anchors = raw['anchors']
        if not isinstance(anchors, dict) or not anchors:
            raise ValueError
        for position, item in anchors.items():
            if not isinstance(item['quantity'], str):
                raise ValueError
            q = Decimal(item['quantity'])
            if not position or not q.is_finite() or q < 0 or any(not isinstance(item[k], str) or not item[k] for k in ('asset', 'ticker')):
                raise ValueError
        if len({a['asset'] for a in anchors.values()}) != len(anchors):
            raise ValueError
        return AssetHistorySettings(raw['source_id'], tuple(accounts), stamp.isoformat(), anchors,
                                    raw['currency'], str(raw.get('note', '')), str(raw.get('scope_label', 'Selected owned locations')))
    except (ValueError, KeyError, TypeError, InvalidOperation):
        raise DataError('Invalid asset performance settings; keep the original evidence and review the selected locations and ending balances.') from None


def load_settings(directory):
    path = Path(directory)/FILE
    if not path.exists():
        return None, None
    try:
        content = path.read_bytes()
        return parse_settings(json.loads(content)), sha256(content).hexdigest()
    except (ValueError, OSError) as exc:
        raise DataError('Cannot read asset performance settings: '+str(exc)) from exc


def evidence_rows(journal, settings):
    """Last reviewed revision wins; identical imports are not additive."""
    latest = {}
    for receipt in journal.transaction_imports:
        if receipt.facts['source_id'] == settings.source_id:
            latest[receipt.facts['event_id']] = receipt
    return tuple(TransactionRow(r.raw, r.facts, r.issues) for r in latest.values())


def settings_for_scope(directory, scope):
    settings, _ = load_settings(directory)
    if (settings and scope.positions and not scope.accounts and not scope.unresolved
            and set(scope.positions) <= settings.anchors.keys()):
        return settings
    return None


def save_settings(directory, raw, *, expected_revision, expected_journal_revision, expected_holdings_revision):
    from portfolio_app.account_journal import load_journal
    from portfolio_app.positions import read_snapshot
    from portfolio_app.storage import save_document
    from portfolio_app.workspace_lock import workspace_lock
    settings = parse_settings(raw)
    directory = Path(directory)
    with workspace_lock(directory):
        journal, revision = load_journal(directory)
        snapshot = read_snapshot(directory/'holdings.csv')
        if revision != expected_journal_revision or snapshot.revision != expected_holdings_revision:
            raise DataError('Evidence or holdings changed. Review the ending balances again.')
        if not settings.anchors.keys() <= set(snapshot.holdings.position_id):
            raise DataError('An asset history position is missing.')
        if not evidence_rows(journal, settings):
            raise DataError('Save the transaction evidence before enabling asset history.')
        save_document(directory/FILE, json.dumps(raw, indent=2)+'\n', expected_revision)
