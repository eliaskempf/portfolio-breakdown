"""Pure selection and freshness of absolute futures prices, independent of cash."""
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from portfolio_app.account_journal import effective_events, replay
from portfolio_app.account_observations import BASIS_KINDS, current_marks
from portfolio_app.derivative_types import PriceObservation
from portfolio_app.futures_clock import EXCHANGE_ZONE, business_date, instant, settlement_reference, utc_now


@dataclass(frozen=True)
class Valuation:
    observation: PriceObservation | None
    status: str
    label: str
    detail: str
    qualified: bool


def price_time(observation):
    if observation.quote_at:
        return instant(observation.quote_at)
    day = date.fromisoformat(observation.date)
    if observation.kind == 'daily_settlement':
        return settlement_reference(day)
    return datetime.combine(day, time.max, EXCHANGE_ZONE)


def freshness(observation, *, now=None, fallback=False, error='', closed=False, disputed=False):
    now = now or utc_now()
    if closed:
        return Valuation(observation, 'closed', 'Closed', 'Recorded closed-contract performance.', False)
    if observation is None:
        return Valuation(None, 'unavailable', 'No price', error or 'No recorded valuation price.', True)
    detail = f'{observation.contract_id} · {observation.source}. Business date {observation.date}.'
    if observation.quote_at:
        stamp = instant(observation.quote_at)
        minutes = max(0, int((now - stamp).total_seconds() // 60))
        age = f'{minutes}m' if minutes < 60 else f'{minutes // 60}h' if minutes < 1440 else f'{minutes // 1440}d'
        status = 'delayed' if now - stamp <= timedelta(minutes=30) else 'stale'
        label = ('Delayed' if status == 'delayed' else 'Stale') + f' · {age}'
        detail += f' Quote time {observation.quote_at}; declared delay {observation.delay_minutes or 0} minutes.'
        if disputed:
            status, label = 'revised', f'Revised · {age}'
            detail += ' Conflicting source prices at this quote time; first observation retained pending a newer quote or manual mark.'
    else:
        status = 'basis' if fallback else 'settlement' if observation.kind == 'daily_settlement' else 'manual'
        label = {'basis': 'Recorded', 'settlement': 'Settlement', 'manual': 'Manual'}[status] + ' · ' + date.fromisoformat(observation.date).strftime('%d %b')
        detail += ' Date-only valuation; not a current intraday quote.'
    if observation.observed_at:
        detail += f' Retrieved/entered {observation.observed_at}.'
    if error:
        detail += ' Refresh unavailable: ' + error
    return Valuation(observation, status, label, detail, status != 'delayed')


def select_valuations(journal, manual=None, automatic=None, settlements=None, *, now=None, errors=None, disputed=None):
    """A settlement changes basis, not the independent absolute market price.

    Same-day date-only manual marks win ambiguous comparisons. Accepted source
    settlements cannot override recorded corrections/voids on that date.
    """
    now = now or utc_now()
    manual = current_marks(journal, manual or {}, today=business_date(now))
    automatic, settlements, errors = automatic or {}, settlements or {}, errors or {}
    events = effective_events(journal)
    result = {}
    for pid, balance in replay(journal).positions.items():
        history = [e for e in events if e.position_id == pid and e.kind in BASIS_KINDS]
        last = history[-1] if history else None
        recorded = None
        if last is not None:
            recorded = PriceObservation(balance.contract.id, last.date, last.price,
                kind='daily_settlement' if last.kind == 'settlement' else 'manual', source=last.source)
        candidates = [recorded] if recorded else []
        quote = automatic.get(balance.contract.id)
        if (quote and quote.contract_id == balance.contract.id and quote.quote_at
                and instant(quote.quote_at) <= now + timedelta(seconds=5)
                and (last is None or quote.date >= last.date)):
            # Trade/snapshot times are date-only. The explicitly dated market
            # quote is useful even on that day; it is not a claim about fill time.
            if last is None or last.kind not in {'settlement', 'final_settlement'} or price_time(quote) > price_time(recorded):
                candidates.append(quote)
        touched = {e.date for e in journal.events if e.position_id == pid and e.kind in {'settlement', 'final_settlement'}}
        for settlement in settlements.get(balance.contract.id, ()):
            if (settlement.date not in touched and settlement.date <= business_date(now).isoformat()
                    and (last is None or settlement.date >= last.date)):
                candidates.append(settlement)
        chosen = None
        if candidates:
            # A date-only execution fallback is weaker than independently dated
            # market observations from that date, unlike an actual settlement.
            chosen = max(candidates, key=lambda q: (q.date,
                0 if q is recorded and last.kind in {'trade', 'snapshot'} else 1, price_time(q)))
        mark = manual.get(pid)
        if mark and (chosen is None or mark.date > chosen.date or (
                mark.date == chosen.date and (not mark.quote_at or price_time(mark) >= price_time(chosen)))):
            chosen = mark
        result[pid] = freshness(chosen, now=now,
            fallback=chosen is recorded and last is not None and last.kind != 'settlement',
            error=errors.get(balance.contract.id, ''), closed=not balance.quantity,
            disputed=chosen is quote and balance.contract.id in (disputed or ()))
    return result


def load_valuations(directory, journal, *, now=None):
    from portfolio_app.account_observations import load_observations
    from portfolio_app.futures_quotes import read_quotes, latest_quotes, disputed_quotes, collector
    from portfolio_app.settlement_archive import SettlementArchive
    from portfolio_app.holdings import DataError
    manual, rates, _ = load_observations(directory)
    try:
        raw = read_quotes(directory)
        automatic = latest_quotes(raw)
        disputed = disputed_quotes(raw)
        errors = {cid: item.get('error', '') for cid, item in raw['contracts'].items()}
    except DataError as exc:
        automatic, disputed, errors = {}, set(), {c.id: str(exc) for c in journal.contracts}
    if collector.error(directory):
        errors.update({c.id: collector.error(directory) for c in journal.contracts})
    archive, settlements = SettlementArchive(directory), {}
    try:
        for contract in journal.contracts:
            settlements[contract.id] = [q for day, q in archive.entries(contract).items()
                                       if archive.latest(contract, day).price == q.price]
    except DataError as exc:
        errors.update({c.id: str(exc) for c in journal.contracts})
    return select_valuations(journal, manual, automatic, settlements, now=now, errors=errors, disputed=disputed), rates


def valuation_fields(valuation):
    observation = valuation.observation
    return dict(price_status=valuation.status, valuation_status=valuation.status,
        valuation_label=valuation.label, valuation_detail=valuation.detail,
        valuation_qualified=valuation.qualified, quote_date=observation.date if observation else '',
        quote_at=observation.quote_at if observation else '',
        price_source=observation.source if observation else '')


def summarize_freshness(rows):
    """Scope-local summary; investment rows already summarize only their open legs."""
    if 'valuation_qualified' not in rows:
        return '', '', False
    relevant = rows.loc[rows.valuation_label.fillna('').ne('') & rows.valuation_status.fillna('').ne('closed')]
    affected = relevant.loc[relevant.valuation_qualified.eq(True)]
    if relevant.empty:
        return '', '', False
    label = f'{len(affected)} dated futures valuation' + ('s' if len(affected) != 1 else '') if len(affected) else 'Delayed futures quotes'
    details = '\n'.join(f'{r.name}: {r.valuation_label}. {r.valuation_detail}' for r in relevant.itertuples())
    return label, details, not affected.empty
