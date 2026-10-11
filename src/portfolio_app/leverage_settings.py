"""Legacy EUR debt and currency-independent, documented fund exposure factors."""
from dataclasses import asdict, dataclass, replace
from datetime import date
from hashlib import sha256
import json

from portfolio_app.holdings import DataError
from portfolio_app.portfolio_exposure import finite
from portfolio_app.storage import save_document

FILE = 'leverage-settings.json'


@dataclass(frozen=True)
class MarginLiability:
    id: str
    name: str
    balance_eur: float | None
    account: str = ''

    def __post_init__(self):
        if not isinstance(self.id, str) or not self.id.strip() or not isinstance(self.name, str) or not self.name.strip() or (self.balance_eur is not None and (not finite(self.balance_eur) or self.balance_eur < 0)):
            raise DataError('Liabilities need an ID, name and nonnegative EUR balance (blank means unknown).')


@dataclass(frozen=True)
class FundFactor:
    position_id: str
    factor: float | None
    method: str = ''
    as_of: str = ''
    benchmark: str = ''
    proxy_position_id: str = ''
    reset_convention: str = 'daily'

    def __post_init__(self):
        if not isinstance(self.position_id, str) or not self.position_id.strip():
            raise DataError('Fund factors require a source position ID.')
        if (not all(isinstance(value, str) for value in (self.method, self.as_of, self.benchmark, self.proxy_position_id))
                or self.reset_convention not in ('daily', 'other', 'unknown')):
            raise DataError('Fund factors require text provenance and an explicit reset convention.')
        if self.proxy_position_id == self.position_id:
            raise DataError('A leveraged fund cannot be its own unlevered reference proxy.')
        if self.factor is not None:
            if not finite(self.factor) or not isinstance(self.method, str) or not self.method.strip():
                raise DataError('A daily equity factor requires a finite signed value and documented source/method.')
        if self.factor is not None or self.as_of:
            try:
                stamp = date.fromisoformat(self.as_of)
                if stamp > date.today():
                    raise ValueError('future date')
            except (ValueError, TypeError) as exc:
                raise DataError('Fund factor date must be YYYY-MM-DD, no later than today.') from exc


@dataclass(frozen=True)
class LeverageSettings:
    liabilities: tuple[MarginLiability, ...] = ()
    fund_factors: tuple[FundFactor, ...] = ()
    version: int = 2
    enabled: bool | None = None
    active_basis: str = 'capital'

    def __post_init__(self):
        if type(self.version) is not int or self.version != 2 or len({x.id for x in self.liabilities}) != len(self.liabilities) or len({x.position_id for x in self.fund_factors}) != len(self.fund_factors):
            raise DataError('Leverage settings require version 2 and unique liability/position IDs.')
        if self.enabled is None:
            object.__setattr__(self, 'enabled', requires_leverage(self))
        if type(self.enabled) is not bool or self.active_basis not in {'capital', 'exposure'}:
            raise DataError('Leverage requires a boolean enabled setting and capital or exposure policy basis.')
        if not self.enabled and (requires_leverage(self) or self.active_basis != 'capital'):
            raise DataError('Leverage cannot be disabled while leverage records or an active exposure policy require it.')


def requires_leverage(settings, future=None):
    # Unknown balances/factors require the controls too. A closed funded future
    # remains a distinct capital/exposure asset, even without open contracts.
    return bool(settings.liabilities or settings.fund_factors or
                (future is not None and (future.contracts or future.reference_capital_eur is not None or future.pnl)))


def requires_legacy_eur(settings, future=None):
    """Dimensionless fund factors do not give a portfolio an EUR restriction."""
    return bool(settings.liabilities or
                (future is not None and (future.contracts or future.reference_capital_eur is not None or future.pnl)))


def load_leverage_settings(directory, *, future=None):
    path = directory / FILE
    if not path.exists():
        return LeverageSettings(enabled=requires_leverage(LeverageSettings(), future)), None
    try:
        data = path.read_bytes()
        raw = json.loads(data)
        if not isinstance(raw, dict) or type(raw.get('version')) is not int or raw['version'] not in (1, 2):
            raise DataError('Unsupported leverage settings version.')
        if raw['version'] == 1:
            raw = {**raw, 'version': 2, 'enabled': None, 'active_basis': 'capital'}
        elif type(raw.get('enabled')) is not bool:
            raise DataError('Version 2 requires an explicit boolean enabled setting.')
        settings = LeverageSettings(**{**raw, 'liabilities': tuple(MarginLiability(**x) for x in raw['liabilities']),
                                      'fund_factors': tuple(FundFactor(**x) for x in raw['fund_factors'])})
        # A future can be created after settings were saved. Keep its accounting
        # visible; no migration writes are performed during loading.
        if requires_leverage(settings, future) and not settings.enabled:
            settings = replace(settings, enabled=True)
        return settings, sha256(data).hexdigest()
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise DataError(f'Invalid leverage settings: {exc}') from exc


def save_leverage_settings(directory, settings, expected_revision):
    from portfolio_app.workspace_lock import workspace_lock
    with workspace_lock(directory):
        from portfolio_app.portfolio_settings import load_settings
        from portfolio_app.futures_workspace import load_sandbox
        sandbox = load_sandbox(directory)
        from portfolio_app.account_journal import load_journal
        journal, _ = load_journal(directory)
        remaining = replace(settings, liabilities=tuple(x for x in settings.liabilities if x.id not in journal.adopted_liability_ids))
        legacy_future = sandbox[0] if sandbox and not journal.accounts else None
        if requires_legacy_eur(remaining, legacy_future) and load_settings(directory).reporting_currency != 'EUR':
            raise DataError('Legacy EUR debt and sandbox futures require EUR reporting.')
        if not settings.enabled and requires_leverage(settings, sandbox[0] if sandbox else None):
            raise DataError('Close/remove leverage records before disabling leverage; funded futures still require it.')
        save_document(directory / FILE, json.dumps(asdict(settings), indent=2) + '\n', expected_revision)
