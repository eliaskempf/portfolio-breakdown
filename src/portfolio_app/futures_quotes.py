"""Durable automatic marks and bounded refresh, separate from manual/account facts."""
from contextvars import copy_context
from dataclasses import asdict
from datetime import timedelta
import json
from pathlib import Path
from threading import Lock, Thread

from portfolio_app import account_journal as ledger
from portfolio_app.atomic_file import staged_text
from portfolio_app.derivative_market import _known_product
from portfolio_app.derivative_types import PriceObservation
from portfolio_app.eurex_quotes import fetch_quote
from portfolio_app.futures_clock import EXCHANGE_ZONE, business_date, instant, utc_now
from portfolio_app.holdings import DataError
from portfolio_app.locking import write_lock
from portfolio_app.workspace_lock import workspace_lock

FILE = 'futures-quotes.json'


def read_quotes(directory):
    path = Path(directory) / FILE
    if not path.exists():
        return {'version': 1, 'contracts': {}}
    try:
        raw = json.loads(path.read_text())
        if raw['version'] != 1 or not isinstance(raw['contracts'], dict):
            raise ValueError('Unsupported quotes format')
        for cid, item in raw['contracts'].items():
            for value in item['observations']:
                quote = PriceObservation(**value)
                if quote.contract_id != cid or quote.kind != 'mark' or not quote.quote_at:
                    raise ValueError('Invalid quote identity or time')
                if business_date(instant(quote.quote_at)).isoformat() != quote.date:
                    raise ValueError('Quote business date differs')
            if item.get('attempted_at'):
                instant(item['attempted_at'])
            if item.get('next_attempt_at'):
                instant(item['next_attempt_at'])
        return raw
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        raise DataError('Cannot read saved futures quotes; records are retained.') from exc


def latest_quotes(raw):
    result = {}
    for cid, item in raw['contracts'].items():
        observations = [PriceObservation(**value) for value in item['observations']]
        if observations:
            # A same-time conflicting price is retained in history, but cannot
            # silently replace the first accepted observation at that instant.
            result[cid] = max(observations, key=lambda quote: instant(quote.quote_at))
    return result


def disputed_quotes(raw):
    """Only a conflict at the latest market instant affects current freshness."""
    latest = latest_quotes(raw)
    return {cid for cid, quote in latest.items() if any(
        instant(value['quote_at']) == instant(quote.quote_at)
        and PriceObservation(**value).price != quote.price
        for value in raw['contracts'][cid]['observations'])}


def record_result(directory, contract, quote, error, *, attempted_at, now):
    if quote is not None and (quote.contract_id != contract.id or quote.kind != 'mark'
            or not quote.quote_at or instant(quote.quote_at) > now + timedelta(seconds=5)
            or business_date(instant(quote.quote_at)).isoformat() != quote.date):
        raise DataError('Refresh returned an invalid exact-contract quote.')
    path = Path(directory) / FILE
    with workspace_lock(directory):
        raw = read_quotes(directory)
        item = raw['contracts'].setdefault(contract.id, {'observations': [], 'failures': 0})
        if quote is not None:
            identity = (quote.quote_at, str(quote.price), quote.source)
            if not any((v['quote_at'], str(v['price']), v['source']) == identity for v in item['observations']):
                item['observations'].append(asdict(quote))
        if not item.get('attempted_at') or instant(item['attempted_at']) <= attempted_at:
            failures = item.get('failures', 0) + 1 if error else 0
            local = now.astimezone(EXCHANGE_ZONE)
            minutes = (5, 15, 60)[min(failures - 1, 2)] if failures else (
                5 if local.weekday() < 5 and 1 <= local.hour < 22 else 60)
            item.update(attempted_at=attempted_at.isoformat(), checked_at=now.isoformat(),
                        next_attempt_at=(now + timedelta(minutes=minutes)).isoformat(),
                        failures=failures, error=error)
        # The file retains observations itself. Avoid a full-file backup on every
        # poll; workspace backups still include this durable history.
        with staged_text(path, lambda f: f.write(json.dumps(raw, default=str, indent=2) + '\n'), sync=True) as temp:
            temp.replace(path)


def quote_due(item, now):
    try:
        return now >= instant(item['next_attempt_at']) or now < instant(item['attempted_at'])
    except (KeyError, ValueError, TypeError):
        return True


def active_contracts(journal, now):
    result = {}
    for balance in ledger.replay(journal).positions.values():
        if not balance.quantity or business_date(now).isoformat() > (balance.contract.last_trade_date or balance.contract.expiry):
            continue
        try:
            _known_product(balance.contract)
        except DataError:
            continue
        result[balance.contract.id] = balance.contract
    return result


class QuoteCollector:
    def __init__(self, *, fetch=fetch_quote, now=utc_now):
        self.fetch, self.now = fetch, now
        self._lock, self._workers, self._errors, self._failed_at = Lock(), {}, {}, {}

    def running(self, directory):
        with self._lock:
            worker = self._workers.get(str(Path(directory).resolve()))
            return bool(worker and worker.is_alive())

    def error(self, directory):
        with self._lock:
            return self._errors.get(str(Path(directory).resolve()), '')

    def schedule(self, directory, journal, *, demo=False, force=False):
        if demo or not journal:
            return False
        directory = Path(directory).resolve()
        key = str(directory)
        try:
            saved = read_quotes(directory)['contracts']
            if not any(force or quote_due(saved.get(cid, {}), self.now())
                       for cid in active_contracts(journal, self.now())):
                return False
        except (DataError, OSError) as exc:
            with self._lock:
                self._errors[key] = str(exc)
            return False
        with self._lock:
            if self._workers.get(key) and self._workers[key].is_alive():
                return False
            if not force and key in self._failed_at and self.now() - self._failed_at[key] < timedelta(minutes=5):
                return False
            self._errors.pop(key, None)
            worker = Thread(target=copy_context().run, args=(self._run, directory, force), daemon=True)
            self._workers[key] = worker
            worker.start()
        return True

    def _run(self, directory, force):
        try:
            with write_lock(directory / '.futures-quotes.lock', blocking=False):
                journal, _ = ledger.load_journal(directory)
                saved = read_quotes(directory)['contracts']
                for cid, contract in active_contracts(journal, self.now()).items():
                    if not force and not quote_due(saved.get(cid, {}), self.now()):
                        continue
                    attempted = self.now()
                    quote, error = None, ''
                    try:
                        quote = self.fetch(contract)
                    except (DataError, OSError, ValueError) as exc:
                        error = str(exc)
                    record_result(directory, contract, quote, error, attempted_at=attempted, now=self.now())
        except BlockingIOError:
            pass
        except Exception as exc:
            with self._lock:
                self._errors[str(directory)] = str(exc)
                self._failed_at[str(directory)] = self.now()


collector = QuoteCollector()
