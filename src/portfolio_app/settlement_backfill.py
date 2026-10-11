"""Reviewable exact-contract settlement backfill, independent of Streamlit."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, timedelta
from uuid import uuid4

from portfolio_app import account_journal as ledger
from portfolio_app.derivative_types import PriceObservation, iso_date
from portfolio_app.derivative_market import fetch_settlement
from portfolio_app.holdings import DataError


@dataclass(frozen=True)
class SettlementDay:
    date: str
    status: str
    observation: PriceObservation | None = None
    message: str = ''


def settlement_days(journal, position_id, start, end):
    """Weekday eligibility at each day's close, after that day's recorded trades.

    Snapshots already include their day's clearing balance. Cash reconciliations
    may include missing settlements, so earlier gaps require manual reconciliation.
    Weekdays without source data remain gaps, not assumed exchange holidays.
    """
    first, last = date.fromisoformat(iso_date(start)), date.fromisoformat(iso_date(end))
    if first > last or (last-first).days > 30:
        raise DataError('Choose an ordered range of at most 31 calendar days.')
    if last >= date.today():
        raise DataError('Retrieve completed business dates, ending before today.')
    state = ledger.replay(journal)
    if position_id not in state.positions:
        raise DataError('Select an existing contract position.')
    position = state.positions[position_id]
    events = ledger.effective_events(journal)
    snapshots = [e.date for e in events if e.position_id == position_id and e.kind == 'snapshot']
    recorded = {e.date for e in events if e.position_id == position_id and e.kind in {'settlement', 'final_settlement'}}
    reconciled = [e for e in events if e.account_id == position.spec.account_id
                  and e.currency == position.contract.currency and e.kind in {'cash_open', 'cash_adjust'}]
    rows = []
    for offset in range((last-first).days+1):
        day = first+timedelta(days=offset)
        if day.weekday() >= 5:
            continue
        stamp = day.isoformat()
        status = 'Ready to retrieve'
        if stamp in recorded:
            status = 'Already recorded'
        elif snapshots and stamp <= max(snapshots):
            status = 'Included in snapshot'
        elif stamp >= (position.contract.last_trade_date or position.contract.expiry):
            status = 'Expiry: enter manually'
        elif not ledger.replay(journal, as_of=stamp).positions[position_id].quantity:
            status = 'No open contracts'
        elif any(stamp < e.date or (stamp == e.date and e.kind == 'cash_adjust') for e in reconciled):
            status = 'Cash reconciliation: review manually'
        rows.append(SettlementDay(stamp, status))
    return tuple(rows)


def retrieve_settlements(journal, position_id, start, end, *, fetch=fetch_settlement):
    days = settlement_days(journal, position_id, start, end)
    contract = ledger.replay(journal).positions[position_id].contract

    def retrieve(day):
        if day.status != 'Ready to retrieve':
            return day
        try:
            observed = fetch(contract, day.date)
            if (observed.contract_id != contract.id or observed.date != day.date
                    or observed.kind != 'daily_settlement'):
                raise DataError('Source returned a different contract, date or price type.')
            return SettlementDay(day.date, 'Available', observed)
        except (DataError, OSError, ValueError) as exc:
            return SettlementDay(day.date, 'Unavailable', message=str(exc))

    with ThreadPoolExecutor(max_workers=4) as pool:
        return tuple(pool.map(retrieve, days))


def propose_settlements(journal, position_id, start, end, observations, *, automatic=False):
    """Append validated daily end-of-day postings; callers control persistence."""
    eligible = {day.date for day in settlement_days(journal, position_id, start, end)
                if day.status == 'Ready to retrieve'}
    position = ledger.replay(journal).positions[position_id]
    observations = sorted(observations, key=lambda item: item.date)
    if not observations:
        raise DataError('No available settlements to record.')
    seen, entries = set(), []
    sequence = max((event.sequence for event in journal.events), default=0)
    for observed in observations:
        if (observed.contract_id != position.contract.id or observed.kind != 'daily_settlement'
                or observed.date not in eligible or observed.date in seen):
            raise DataError('Settlement is duplicated, already recorded or outside the eligible history.')
        seen.add(observed.date)
        sequence += 1
        entries.append(ledger.Event(uuid4().hex, observed.date, 'settlement',
            position_id=position_id, account_id=position.spec.account_id,
            currency=position.contract.currency, price=observed.price, sequence=sequence,
            source=observed.source, note=('Automatic' if automatic else 'Reviewed')+' daily settlement; cash posted on business date'))
    return ledger.append_events(journal, entries)
