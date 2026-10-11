"""Durable, versioned settlement observations, separate from accounting facts.

The first downloaded price remains canonical until a user accepts a correction.
Retrieval never rewrites a journal event, valuation mark, or previously seen price.
"""
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime, timezone
from hashlib import sha256
import json
from pathlib import Path

from portfolio_app.account_journal import effective_events
from portfolio_app.atomic_file import staged_text
from portfolio_app.derivative_types import ContractSpec, PriceObservation, decimal, iso_date
from portfolio_app.holdings import DataError
from portfolio_app.locking import write_lock
from portfolio_app.storage import revision
from portfolio_app.workspace_lock import workspace_lock

FILE = 'settlement-archive.json'


@dataclass
class _Day:
    versions: list[PriceObservation]
    canonical: int = 0
    latest: int = 0


@dataclass
class _History:
    contract: ContractSpec
    days: dict[str, _Day]


def _validate(contract, observation):
    if not isinstance(contract, ContractSpec) or not isinstance(observation, PriceObservation):
        raise DataError('An exact contract and validated settlement observation are required.')
    if (observation.contract_id != contract.id or observation.kind != 'daily_settlement'
            or observation.date >= (contract.last_trade_date or contract.expiry)
            or observation.date > date.today().isoformat()):
        raise DataError('Settlement identity, date or price type does not match the contract.')
    if not isinstance(observation.observed_at, str):
        raise DataError('Settlement retrieval time must be text.')


def _economics(contract):
    # Display names and metadata provenance may be refreshed without changing the
    # contract. Clearing dates and return convention are economically material.
    return (contract.id, contract.currency, contract.multiplier, contract.tick,
            contract.last_trade_date or contract.expiry,
            contract.final_settlement_date or contract.expiry, contract.return_convention)


def _same_contract(stored, requested):
    if not isinstance(requested, ContractSpec) or _economics(stored) != _economics(requested):
        raise DataError('Contract specifications differ from the saved settlement archive.')


def _unique_pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError('Duplicate archive field')
        result[key] = value
    return result


class SettlementArchive:
    """Private workspace history; root placement includes it in normal backups."""

    def __init__(self, directory):
        self.directory = Path(directory)
        self.path = self.directory / FILE

    def revision(self) -> str | None:
        return revision(self.path)

    def _read(self) -> dict[str, _History]:
        try:
            if not self.path.exists():
                return {}
            raw = json.loads(self.path.read_text(), object_pairs_hook=_unique_pairs)
            if set(raw) != {'version', 'contracts'} or type(raw['version']) is not int or raw['version'] != 1:
                raise ValueError('Unsupported archive version')
            histories = {}
            for identity, item in raw['contracts'].items():
                if set(item) != {'contract', 'days'}:
                    raise ValueError('Invalid contract history')
                contract = ContractSpec(**item['contract'])
                if identity != contract.id:
                    raise ValueError('Contract identity differs')
                days = {}
                for stamp, saved in item['days'].items():
                    iso_date(stamp)
                    if set(saved) != {'versions', 'canonical', 'latest'} or not isinstance(saved['versions'], list):
                        raise ValueError('Invalid settlement history')
                    observations = [PriceObservation(**value) for value in saved['versions']]
                    for observed in observations:
                        _validate(contract, observed)
                        if observed.date != stamp:
                            raise ValueError('Settlement date differs')
                    pairs = {(item.price, item.source) for item in observations}
                    if not observations or len(pairs) != len(observations):
                        raise ValueError('Empty or repeated settlement versions')
                    if any(type(saved[field]) is not int or not 0 <= saved[field] < len(observations)
                           for field in ('canonical', 'latest')):
                        raise ValueError('Invalid settlement version selection')
                    days[stamp] = _Day(observations, saved['canonical'], saved['latest'])
                histories[identity] = _History(contract, days)
            return histories
        except (OSError, ValueError, TypeError, KeyError, AttributeError, DataError) as exc:
            raise DataError('Invalid settlement archive; existing history has been retained.') from exc

    def _write(self, histories, expected):
        contracts = {identity: {'contract': asdict(history.contract), 'days': {
            stamp: {'versions': [asdict(item) for item in day.versions],
                    'canonical': day.canonical, 'latest': day.latest}
            for stamp, day in sorted(history.days.items())}}
            for identity, history in sorted(histories.items())}
        content = json.dumps({'version': 1, 'contracts': contracts}, default=str, indent=2) + '\n'
        # Unlike editor documents this archive already retains every distinct
        # price/source version. Per-download full-file backups would grow without
        # bound merely from refreshed timestamps. Normal workspace backups still
        # include the archive, while atomic publication protects interrupted work.
        with write_lock(self.path.with_name(f'.{self.path.name}.lock')):
            if self.revision() != expected:
                raise DataError('Settlement archive changed. Retry the collection.')
            with staged_text(self.path, lambda handle: handle.write(content), sync=True) as temporary:
                if self.revision() != expected:
                    raise DataError('Settlement archive changed during save. Retry the collection.')
                temporary.replace(self.path)

    @staticmethod
    def _history(histories, contract):
        if not isinstance(contract, ContractSpec):
            raise DataError('A validated contract specification is required.')
        history = histories.get(contract.id)
        if history is not None:
            _same_contract(history.contract, contract)
        return history

    def _day(self, contract, day):
        stamp = iso_date(day)
        history = self._history(self._read(), contract)
        return history.days.get(stamp) if history else None

    def load(self, contract, day) -> PriceObservation | None:
        saved = self._day(contract, day)
        return saved.versions[saved.canonical] if saved else None

    def latest(self, contract, day) -> PriceObservation | None:
        saved = self._day(contract, day)
        return saved.versions[saved.latest] if saved else None

    def versions(self, contract, day) -> tuple[PriceObservation, ...]:
        saved = self._day(contract, day)
        return tuple(saved.versions) if saved else ()

    def entries(self, contract) -> dict[str, PriceObservation]:
        history = self._history(self._read(), contract)
        return {stamp: day.versions[day.canonical] for stamp, day in sorted(history.days.items())} if history else {}

    def record(self, contract, observation) -> None:
        _validate(contract, observation)
        if not observation.observed_at:
            observation = replace(observation, observed_at=datetime.now(timezone.utc).isoformat())
        with workspace_lock(self.directory):
            expected = self.revision()
            histories = self._read()
            history = self._history(histories, contract)
            if history is None:
                history = histories[contract.id] = _History(contract, {})
            saved = history.days.get(observation.date)
            if saved is None:
                history.days[observation.date] = _Day([observation])
            else:
                version = next((index for index, item in enumerate(saved.versions)
                                if (item.price, item.source) == (observation.price, observation.source)), None)
                if version is None:
                    version = len(saved.versions)
                    saved.versions.append(observation)
                else:
                    if saved.versions[version] == observation and saved.latest == version:
                        return
                    saved.versions[version] = observation
                saved.latest = version
            self._write(histories, expected)

    def accept_latest(self, contract, day, expected_price) -> None:
        """Accept the reviewed source price in this archive only, never in cash."""
        stamp, price = iso_date(day), decimal(expected_price, 'Reviewed price')
        with workspace_lock(self.directory):
            expected = self.revision()
            histories = self._read()
            history = self._history(histories, contract)
            saved = history.days.get(stamp) if history else None
            if saved is None or saved.versions[saved.latest].price != price:
                raise DataError('The latest settlement changed. Reload before accepting it.')
            if saved.canonical != saved.latest:
                saved.canonical = saved.latest
                self._write(histories, expected)


def reconciliation_rows(journal, archive, position_ids=None) -> list[dict]:
    """Read-only price differences for effective daily settlements and sources.

    Source changes compare latest retrieval with the accepted archive price.
    Recorded settlements compare against that accepted price. Pending source
    corrections therefore remain distinct from corrections to recorded cash.
    """
    histories = archive._read()
    contracts = {item.id: item for item in journal.contracts}
    investments = {item.id: item.name for item in journal.investments}
    selected = set(position_ids) if position_ids is not None else None
    recorded = {(item.position_id, item.date): item.price for item in effective_events(journal)
                if item.kind == 'settlement'}
    rows = []
    for position in journal.positions:
        if selected is not None and position.id not in selected:
            continue
        contract = contracts[position.contract_id]
        history = archive._history(histories, contract)
        days = history.days if history else {}
        stamps = set(days) | {stamp for identity, stamp in recorded if identity == position.id}
        for stamp in sorted(stamps):
            saved = days.get(stamp)
            canonical = saved.versions[saved.canonical] if saved else None
            latest = saved.versions[saved.latest] if saved else None
            posted = recorded.get((position.id, stamp))
            statuses = []
            if canonical and latest.price != canonical.price:
                statuses.append('Source changed')
            if posted is not None:
                if canonical is None:
                    statuses.append('No archived price')
                elif posted != canonical.price:
                    statuses.append('Recorded settlement differs')
            if not statuses:
                continue
            row = {'Position': investments.get(position.investment_id, contract.name or position.id),
                   'Contract': contract.product, 'Maturity': contract.expiry, 'Date': stamp,
                   'Archived price': str(canonical.price) if canonical else '',
                   'Retrieved price': str(latest.price) if latest else '',
                   'Recorded price': str(posted) if posted is not None else '',
                   'Status': ' · '.join(statuses), 'position_id': position.id, 'contract_id': contract.id}
            # Ignore retrieval timestamps: repeated downloads do not create a
            # fresh unread notification for an unchanged price discrepancy.
            row['identity'] = sha256(json.dumps([position.id, contract.id, stamp,
                row['Archived price'], row['Retrieved price'], row['Recorded price'], row['Status']]).encode()).hexdigest()
            rows.append(row)
    return rows
