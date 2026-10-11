"""Project the private account journal into ordinary valuation and exposure units.

Cash links replace existing cash contributions. Futures contribute only unsettled
claims to wealth; their signed notional is a separate measure.
"""
from dataclasses import replace
from decimal import Decimal
from io import StringIO

import pandas as pd

from portfolio_app.holdings import DataError, parse_holdings
from portfolio_app.fx import current_rate
from portfolio_app.storage import save_document
from portfolio_app.positions import read_snapshot
from portfolio_app.workspace_lock import workspace_lock


def validate_links(journal, holdings):
    indexed = holdings.set_index('position_id')
    from portfolio_app.account_journal import effective_events
    openings = {(e.account_id,e.currency):e.amount for e in effective_events(journal) if e.kind == 'cash_open'}
    for link in journal.cash_links:
        if link.position_id not in indexed.index:
            raise DataError('An adopted cash holding is missing. Reconcile the account binding before continuing.')
        row = indexed.loc[link.position_id]
        unit = row.get('manual_price', 1)
        unit = unit if pd.notna(unit) and unit != '' else 1
        source_amount = Decimal(str(row.shares)) * Decimal(str(unit))
        if source_amount.quantize(Decimal('.01')) != openings.get((link.account_id,link.currency)):
            raise DataError('An adopted cash holding changed outside Accounts. Restore its opening snapshot or reconcile the binding before continuing.')
        if row.get('instrument_type') != 'cash':
            raise DataError('An adopted cash holding is no longer cash. Reconcile its account binding.')
        currency = row.get('manual_price_currency', '')
        currency = currency if isinstance(currency, str) and currency else row.get('acquisition_currency', '')
        if isinstance(currency, str) and currency and currency != link.currency:
            raise DataError('An adopted cash holding changed currency. Reconcile its account binding.')
    for link in journal.holding_links:
        if link.position_id not in indexed.index:
            raise DataError('An account-linked holding is missing. Reconcile its account binding.')


def freeze_position_keys(directory, expected_revision):
    """Identity-only save; existing effective IDs and financial data are retained."""
    path = directory / 'holdings.csv'
    with workspace_lock(directory):
        snapshot = read_snapshot(path)
        if snapshot.revision != expected_revision:
            raise DataError('Holdings changed. Reload before linking an account.')
        if 'position_key' in snapshot.holdings or not path.exists():
            return snapshot
        raw = pd.read_csv(StringIO(path.read_text(encoding='utf-8-sig')), dtype=str, keep_default_na=False)
        raw['position_key'] = snapshot.holdings.position_id.tolist()
        content = raw.to_csv(index=False)
        parse_holdings(content)
        save_document(path, content, expected_revision)
        return read_snapshot(path)


def assert_cash_editable(path, position_ids):
    from portfolio_app.account_journal import load_journal
    journal, _ = load_journal(path.parent)
    adopted = {x.position_id for x in journal.cash_links}
    if adopted.intersection(position_ids):
        raise DataError('This cash balance is maintained in Accounts. Record a dated reconciliation there.')


def journal_positions(valued, journal, prices, *, reporting_currency='EUR', marks=None, fx_rates=None):
    from portfolio_app.account_journal import replay
    validate_links(journal, valued)
    state = replay(journal)
    marks = marks or {}
    accounts = {a.id: a.name for a in journal.accounts}
    linked = {(x.account_id, x.currency): x.position_id for x in journal.cash_links}
    original = valued.set_index('position_id')
    result = valued.loc[~valued.position_id.isin(linked.values())].copy()
    for binding in journal.holding_links:
        result.loc[result.position_id.eq(binding.position_id), 'account'] = accounts[binding.account_id]
        result.loc[result.position_id.eq(binding.position_id), 'account_id'] = binding.account_id
    rates = {}
    def converted(amount, currency):
        if currency not in rates:
            from portfolio_app.account_observations import manual_rate
            manual = manual_rate(fx_rates or {}, currency, reporting_currency)
            quote = current_rate(prices, currency, reporting_currency) if manual is None else None
            rates[currency] = manual if manual is not None else float(quote.quote.price) if quote.quote else None
        return float(amount) * rates[currency] if amount is not None and rates[currency] is not None else float('nan')
    rows = []
    def row(identity, account_id, currency, amount, name, kind, **extra):
        base = dict(position_id=identity, id=identity, name=name, short_name='', ticker='', isin='',
            shares=1., instrument_type=kind, exposure_kind='non_equity',
            quote_currency=currency, acquisition_currency=currency, reporting_currency=reporting_currency,
            current_value_reporting=converted(amount, currency), current_price=float(amount) if amount is not None else float('nan'),
            current_value_native=float(amount) if amount is not None else float('nan'),
            current_fx_rate=rates.get(currency),
            account=accounts[account_id], account_id=account_id, portfolio='', bucket_id='',
            within_bucket_target=float('nan'), target_allocation=float('nan'), acquisition_price=float('nan'),
            cost_basis=float('nan'), cost_basis_reporting=float('nan'), unrealized_gain=float('nan'),
            unrealized_gain_reporting=float('nan'), return_pct=float('nan'), cost_estimated=False,
            price_status='manual', fx_status='fresh' if rates.get(currency) is not None else 'unavailable',
            valuation_note='Recorded account balance; notional is not wealth.',
            performance_note='Account transaction history is shown in Accounts; pre-snapshot returns may be unknown.',
            journal_managed=True)
        base.update(extra)
        if kind == 'cash':
            from portfolio_app.performance import cash_performance_note
            base['performance_note'] = cash_performance_note(currency, reporting_currency)
        rows.append(base)
    for (account, currency), amount in state.cash.items():
        identity = linked.get((account, currency), f'account-cash:{account}:{currency}')
        extra = {}
        if identity in original.index:
            prior = original.loc[identity]
            extra = {key: prior.get(key, '') for key in ('bucket_id', 'portfolio')}
        row(identity, account, currency, amount, f'{accounts[account]} · {currency} cash',
            'cash' if amount >= 0 else 'liability', **extra)
    for (account, currency, loan), amount in state.loans.items():
        if amount:
            row(f'account-loan:{account}:{currency}:{loan}', account, currency, -amount,
                f'{accounts[account]} · {loan}', 'liability')
    for identity, balance in state.positions.items():
        contract, spec = balance.contract, balance.spec
        observation = marks.get(identity)
        mark = observation.price if observation is not None else balance.last_price
        pending = sum((p.amount for p in state.pending.values() if p.position_id == identity and p.currency == contract.currency), Decimal(0))
        amount = balance.unsettled_pnl(mark) + pending if mark is not None else (pending if balance.quantity == 0 else None)
        notional = balance.notional(mark) if mark is not None else (Decimal(0) if not balance.quantity else None)
        row(identity, spec.account_id, contract.currency, amount, f'{contract.product} {contract.expiry}', 'future',
            exposure_kind='equity', bucket_id=spec.bucket_id, contracts=balance.quantity,
            signed_notional_reporting=converted(notional, contract.currency),
            unit_exposure_reporting=converted(contract.multiplier * mark, contract.currency) if mark is not None else float('nan'),
            point_multiplier=float(contract.multiplier), return_convention=contract.return_convention,
            product=contract.product, current_fx_rate=rates.get(contract.currency), contract_currency=contract.currency,
            proxy_position_id=spec.proxy_position_id, contract_id=contract.id,
            mark_price=float(mark) if mark is not None else float('nan'),
            price_source=observation.source if observation else 'Last recorded clearing basis',
            quote_date=observation.date if observation else '',
            valuation_note='Unsettled P&L and outstanding cash claims only; settled P&L is in account cash.')
    # Delayed internal transfers are wealth in transit, counted at neither endpoint.
    for identity, pending in state.pending.items():
        if not pending.position_id or pending.currency != state.positions[pending.position_id].contract.currency:
            row(f'account-pending:{identity}', pending.account_id, pending.currency, pending.amount,
                f'{accounts[pending.account_id]} · cash in transit', 'cash')
    if rows:
        result = pd.concat([result, pd.DataFrame(rows)], ignore_index=True)
    result.attrs = dict(valued.attrs, reporting_currency=reporting_currency, actual_accounts=True, account_fx_rates=rates)
    return result
