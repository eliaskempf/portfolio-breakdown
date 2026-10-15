"""Background exact-contract history collection and idempotent daily cash settlement."""
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
from threading import Lock, Thread

from portfolio_app import account_journal as ledger
from portfolio_app.derivative_market import (SettlementCache, _known_product, fetch_settlement,
    SettlementNotPublished, SettlementUnavailable)
from portfolio_app.futures_clock import business_date, instant, settlement_reference, utc_now
from portfolio_app.holdings import DataError
from portfolio_app.locking import write_lock
from portfolio_app.storage import revision, save_document
from portfolio_app.workspace_lock import workspace_lock
from portfolio_app.settlement_archive import SettlementArchive

FILE = 'settlement-collection.json'


def read_status(directory):
    path = Path(directory)/FILE
    if not path.exists():
        return {'version':1, 'contracts':{}}
    try:
        raw = json.loads(path.read_text())
        if raw.get('version') != 1 or not isinstance(raw.get('contracts'),dict):
            raise ValueError('Unsupported collection status')
        for record in raw['contracts'].values():
            if not isinstance(record, dict) or not isinstance(record.get('dates', {}), dict):
                raise ValueError('Invalid contract collection status')
            for day, result in record.get('dates', {}).items():
                date.fromisoformat(day)
                if not isinstance(result, dict) or result.get('status') not in {'checked', 'unavailable', 'not_published', 'failed'}:
                    raise ValueError('Invalid date collection status')
        return raw
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        raise DataError('Cannot read settlement collection status; saved history is retained.') from exc


def _save_status(directory, contract_id, record):
    def attempted(item):
        try:
            return instant(item['attempted_at'])
        except (KeyError, ValueError, TypeError):
            return datetime.min.replace(tzinfo=timezone.utc)
    with workspace_lock(directory):
        raw = read_status(directory)
        prior = raw['contracts'].get(contract_id, {})
        dates = dict(prior.get('dates', {}))
        for day, result in record.get('dates', {}).items():
            if attempted(result) >= attempted(dates.get(day, {})):
                dates[day] = result
        if attempted(record) >= attempted(prior):
            prior = {**prior, **record}
        raw['contracts'][contract_id] = {**prior, 'dates': dates}
        save_document(Path(directory)/FILE, json.dumps(raw,indent=2)+'\n', revision(Path(directory)/FILE))


def weekdays(start, end, contract, *, today=None, include_today=False):
    start, end = date.fromisoformat(start), date.fromisoformat(end)
    today = today or date.today()
    if start > end or (end-start).days > 30 or end > today or (end == today and not include_today):
        raise DataError('Choose up to 31 calendar days ending before today.')
    return [(start+timedelta(days=i)).isoformat() for i in range((end-start).days+1)
            if (start+timedelta(days=i)).weekday()<5 and
            (start+timedelta(days=i)).isoformat() < (contract.last_trade_date or contract.expiry)]


def fetch_archived(directory, contract, day, *, refresh=False, fetch=fetch_settlement):
    """Backfill reads canonical archive values; source corrections need review."""
    archive = SettlementArchive(directory)
    saved = archive.load(contract,day)
    if saved is None:
        cached = SettlementCache(Path(directory)/'.cache'/'derivative-settlements').load(contract,day)
        if cached is not None:
            archive.record(contract,cached)
            saved = archive.load(contract,day)
    if refresh or saved is None:
        archive.record(contract,fetch(contract,day))
        saved = archive.load(contract,day)
    latest = archive.latest(contract,day)
    if latest and saved and latest.price != saved.price:
        raise DataError('Source price differs from archived history. Review History checks before recording this date.')
    return saved


def scan_dates(directory, contract, days, *, fetch=fetch_settlement, now=None):
    """Network reads run in parallel; request start orders concurrent results."""
    from dataclasses import replace
    now = now or utc_now()
    archive = SettlementArchive(directory)
    def retrieve(day):
        try:
            quote = fetch(contract, day)
            if quote.contract_id != contract.id or quote.date != day or quote.kind != 'daily_settlement':
                raise DataError('Retrieved settlement identity does not match the request.')
            return day, replace(quote, observed_at=now.isoformat()), 'checked', ''
        except SettlementNotPublished as exc:
            return day, None, 'not_published', str(exc)
        except SettlementUnavailable as exc:
            return day, None, 'unavailable', str(exc)
        except (DataError, OSError, ValueError) as exc:
            return day, None, 'failed', str(exc)
    result = {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        for day, quote, status, error in pool.map(retrieve, days):
            if quote is not None:
                archive.record(contract, quote)
            reference = settlement_reference(date.fromisoformat(day))
            recent = (business_date(now) - date.fromisoformat(day)).days <= 3
            minutes = 15 if reference <= now < reference + timedelta(hours=2) else 60 if recent else 1440
            if status == 'checked':
                minutes = 1440
            result[day] = dict(status=status, error=error, attempted_at=now.isoformat(),
                               next_attempt_at=(now + timedelta(minutes=minutes)).isoformat())
    _save_status(directory, contract.id, {'dates': result})
    return result


def tracked_contracts(journal, today, *, include_today=False):
    """Active contracts and recently closed legs, deduplicated across accounts."""
    state = ledger.replay(journal)
    events = ledger.effective_events(journal)
    cutoff = (today-timedelta(days=30)).isoformat()
    result = {}
    for identity,balance in state.positions.items():
        history = [e for e in events if e.position_id==identity and e.kind in {'snapshot','trade','final_settlement'}]
        if not history or (not balance.quantity and max(e.date for e in history)<cutoff):
            continue
        try:
            _known_product(balance.contract)
        except DataError:
            continue
        first = max(min(e.date for e in history),cutoff)
        last = min((today-timedelta(days=0 if include_today else 1)).isoformat(),max(e.date for e in history) if not balance.quantity else today.isoformat())
        if first>last:
            continue
        contract = balance.contract
        days = set(weekdays(first,last,contract,today=today,include_today=include_today))
        if contract.id in result:
            days |= result[contract.id][1]
        result[contract.id] = contract,days
    return result


def due(record, now):
    """Compatibility with legacy contract-level scheduling records."""
    try:
        checked = instant(record['attempted_at'])
        if business_date(now) != business_date(checked):
            return True
        recent = (business_date(now)-timedelta(days=3)).isoformat()
        failed = any(day >= recent and row.get('status') in {'unavailable', 'failed', 'not_published'}
                     for day, row in record.get('dates', {}).items())
        return now < checked or now-checked >= timedelta(hours=1 if failed else 24)
    except (KeyError, ValueError, TypeError):
        return True


def due_days(record, held_days, now, *, force=False):
    if not record.get('dates') and record.get('attempted_at') and not force and not due(record, now):
        return []
    result = []
    overlap = (business_date(now)-timedelta(days=7)).isoformat()
    for day in sorted(held_days):
        if date.fromisoformat(day) == business_date(now) and now < settlement_reference(business_date(now)):
            continue
        row = record.get('dates', {}).get(day, {})
        if row.get('status') == 'checked' and day < overlap and not force:
            continue
        try:
            checked = instant(row['attempted_at'])
            ready = (now >= instant(row['next_attempt_at']) or now < checked
                     or (row.get('status') == 'checked' and business_date(now) > business_date(checked)))
        except (KeyError, ValueError, TypeError):
            ready = True
        if force or ready:
            result.append(day)
    return result


class SettlementCollector:
    def __init__(self, *, fetch=fetch_settlement, now=None):
        self.fetch=fetch
        self.now=now or (lambda:datetime.now(timezone.utc))
        self._lock=Lock()
        self._workers={}
        self._errors={}
        self._failed_at={}
        self._processed={}

    def running(self,directory):
        with self._lock:
            worker=self._workers.get(str(Path(directory).resolve()))
            return bool(worker and worker.is_alive())

    def error(self,directory):
        with self._lock:
            return self._errors.get(str(Path(directory).resolve()),'')

    def schedule(self,directory,journal,*,demo=False,force=False):
        if demo or not journal or not journal.positions:
            return False
        directory=Path(directory).resolve()
        key=str(directory)
        try:
            records=read_status(directory)['contracts']
            eligible={cid:pair for cid,pair in tracked_contracts(journal,business_date(self.now()),include_today=True).items()
                      if due_days(records.get(cid,{}),pair[1],self.now(),force=force)}
        except (DataError,OSError) as exc:
            with self._lock: self._errors[key]=str(exc)
            return False
        migration = any((directory/'.cache'/'derivative-settlements').glob('*/*.json')) and not (directory/'settlement-archive.json').exists()
        signature=(revision(directory/ledger.FILE), SettlementArchive(directory).revision(), business_date(self.now()))
        with self._lock:
            pending_archive=signature[1] is not None and self._processed.get(key)!=signature
        if not eligible and not migration and not pending_archive:return False
        with self._lock:
            if key in self._workers and self._workers[key].is_alive():return False
            if not force and key in self._failed_at and self.now()-self._failed_at[key]<timedelta(hours=1):return False
            self._errors.pop(key,None)
            worker=Thread(target=copy_context().run,args=(self._run,directory,force),daemon=True)
            self._workers[key]=worker
            worker.start()
        return True

    def _run(self,directory,force):
        try:
            with write_lock(directory/'.settlement-collector.lock',blocking=False):
                journal,_=ledger.load_journal(directory)
                records=read_status(directory)['contracts']
                archive=SettlementArchive(directory)
                legacy=SettlementCache(directory/'.cache'/'derivative-settlements')
                # Preserve all already cached dates, including closed contracts.
                for contract in journal.contracts:
                    from hashlib import sha256
                    cache_dir=legacy.root/sha256(contract.id.encode()).hexdigest()
                    for path in cache_dir.glob('*.json'):
                        cached=legacy.load(contract,path.stem)
                        if cached is not None and archive.load(contract,path.stem) is None:
                            archive.record(contract,cached)
                from portfolio_app.automatic_settlements import apply_archived_settlements
                apply_archived_settlements(directory, today=business_date(self.now()))
                for cid,(contract,held_days) in tracked_contracts(journal,business_date(self.now()),include_today=True).items():
                    prior=records.get(cid,{})
                    days = due_days(prior, held_days, self.now(), force=force)
                    if not days:
                        continue
                    attempted = self.now()
                    scan_dates(directory,contract,days,fetch=self.fetch,now=attempted)
                    _save_status(directory,cid,{'attempted_at':attempted.isoformat(),'dates':{}})
                with workspace_lock(directory):
                    apply_archived_settlements(directory, today=business_date(self.now()))
                    signature=(revision(directory/ledger.FILE),archive.revision(),business_date(self.now()))
                with self._lock:
                    self._processed[str(directory)]=signature
        except BlockingIOError:
            return
        except Exception as exc:
            with self._lock:
                self._errors[str(directory)]=str(exc)
                self._failed_at[str(directory)]=self.now()


collector=SettlementCollector()
