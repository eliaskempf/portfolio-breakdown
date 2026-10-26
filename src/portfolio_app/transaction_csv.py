"""Versioned, offline transaction evidence. No portfolio or market-data reads."""
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from io import StringIO
import csv
import json
import re

from portfolio_app.holdings import DataError
from portfolio_app.derivative_types import CURRENCIES

POLICY = 'fifo-carrying-fees-v1'
FIELDS = ('schema_version', 'source_id', 'event_id', 'timestamp_utc', 'sequence', 'event_type',
          'account_id', 'asset_id', 'quantity', 'received_asset_id', 'received_quantity',
          'counter_account_id', 'gross_amount', 'currency', 'fee_amount', 'fee_currency',
          'fee_asset_id', 'valuation_amount', 'valuation_currency', 'group_id')
FIELDS_V2 = (*FIELDS, 'effective_date', 'acquisition_cost', 'acquisition_currency', 'cost_quantity',
             'cost_quality', 'cost_source', 'valuation_quality', 'valuation_source',
             'flow_classification', 'note')
KINDS = {'buy', 'sell', 'swap', 'transfer', 'stake', 'unstake', 'reward', 'fee'}
INVENTORY_KINDS = {'opening', 'checkpoint', 'inventory_in', 'inventory_out'}
NUMBERS = ('quantity', 'received_quantity', 'gross_amount', 'fee_amount', 'valuation_amount')


def digest(value):
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


@dataclass(frozen=True)
class TransactionRow:
    raw: dict
    facts: dict
    issues: tuple[str, ...] = ()
    row_number: int = 0

    @property
    def key(self):
        return self.facts['source_id'], self.facts['event_id']

    @property
    def identity(self):
        return 'csv:' + digest(self.facts)

    @property
    def incomplete(self):
        f = self.facts
        reasons = []
        if not f['fee_amount']:
            reasons.append('Fee amount unknown')
        if f['event_type'] in {'buy', 'sell'} and not f['gross_amount']:
            reasons.append('Consideration unknown')
        if f['event_type'] in {'swap', 'reward'} and not f['valuation_amount']:
            reasons.append('Monetary valuation unknown')
        if f['event_type'] in {'opening', 'checkpoint', 'inventory_in'} and f['quantity'] != '0' and not f.get('acquisition_cost'):
            reasons.append('Acquisition cost unknown')
        if f.get('cost_quantity'):
            try:
                if Decimal(f['cost_quantity']) < Decimal(f['quantity']):
                    reasons.append('Acquisition cost unknown for remaining units')
            except InvalidOperation:
                pass
        return tuple(reasons)

    @property
    def date(self):
        return self.facts.get('effective_date') or self.facts['timestamp_utc'][:10]

    @property
    def pairs(self):
        f = self.facts
        pairs = [(f['account_id'], f['asset_id'])]
        if f['event_type'] == 'swap':
            pairs.append((f['account_id'], f['received_asset_id']))
        if f['counter_account_id']:
            pairs.append((f['counter_account_id'], f['asset_id']))
        if f['fee_asset_id']:
            pairs.append((f['account_id'], f['fee_asset_id']))
        return tuple(dict.fromkeys(pair for pair in pairs if all(pair)))


@dataclass(frozen=True)
class ImportReceipt:
    """Private immutable evidence and the interpretation used to apply it."""
    id: str
    raw: dict
    facts: dict
    issues: tuple[str, ...] = ()
    positions: tuple[tuple[str, str, str], ...] = ()
    replaces: str = ''
    policy: str = POLICY

    def __post_init__(self):
        if self.policy != POLICY or not isinstance(self.raw, dict) or not isinstance(self.facts, dict):
            raise DataError('Unsupported transaction evidence policy or fields.')
        if any(not isinstance(k, str) or not isinstance(v, str) for d in (self.raw, self.facts) for k, v in d.items()):
            raise DataError('Transaction evidence fields must remain text.')
        object.__setattr__(self, 'issues', tuple(self.issues))
        object.__setattr__(self, 'positions', tuple(tuple(p) for p in self.positions))
        if any(len(p) != 3 or any(not isinstance(v, str) or not v for v in p) for p in self.positions):
            raise DataError('Invalid transaction position mapping.')
        expected = FIELDS_V2 if self.facts.get('schema_version') == '2' else FIELDS
        if set(self.facts) != set(expected) or any(not isinstance(issue,str) for issue in self.issues):
            raise DataError('Invalid transaction evidence schema or issue text.')
        object.__setattr__(self,'raw',deepcopy(self.raw))
        object.__setattr__(self,'facts',deepcopy(self.facts))
        if self.id != TransactionRow(self.raw, self.facts).identity:
            raise DataError('Transaction evidence identity differs from its facts.')


def normalize(raw, *, layout='generic', source_id='', asset_fees=(), row_number=0):
    raw = {str(k): str(v or '').strip() for k, v in raw.items()}
    v2 = layout == 'generic' and raw.get('schema_version') == '2'
    facts = {key: raw.get(key, '') for key in (FIELDS_V2 if v2 else FIELDS)}
    issues = []
    if layout == 'crypto':
        facts.update(schema_version='1', source_id=source_id, fee_amount=raw.get('fees', ''),
                     valuation_amount=raw.get('valuation_eur', ''),
                     valuation_currency='EUR' if raw.get('valuation_eur') else '')
        if facts['event_type'] == 'swap':
            facts.update(received_asset_id=facts['currency'], received_quantity=facts['gross_amount'],
                         gross_amount='', currency='')
        if facts['fee_currency'] in asset_fees:
            facts['fee_asset_id'], facts['fee_currency'] = facts['fee_currency'], ''
    elif layout != 'generic':
        raise DataError('Use the Transaction CSV format.')
    for name in (*NUMBERS, *(('acquisition_cost','cost_quantity') if v2 else ())):
        if not facts[name]:
            continue
        try:
            value = Decimal(facts[name])
            zero_balance = name == 'quantity' and facts['event_type'] in {'opening', 'checkpoint'} and v2
            if not value.is_finite() or value < 0 or (name in {'quantity', 'received_quantity'} and value == 0 and not zero_balance):
                raise ValueError
            # No Decimal.normalize(): it rounds under the caller's context.
            text = format(value, 'f')
            facts[name] = '0' if value == 0 else text.rstrip('0').rstrip('.') if '.' in text else text
        except (InvalidOperation, ValueError):
            issues.append(f'{name}: supply a finite positive quantity or nonnegative monetary amount')
    if facts['schema_version'] not in ('1', '2') or (facts['schema_version'] == '2' and not v2):
        issues.append('Unsupported schema_version; expected 1 or 2')
    if not v2 and any(raw.get(k) for k in set(FIELDS_V2)-set(FIELDS)):
        issues.append('Transaction CSV evidence fields require schema_version=2')
    for name in ('source_id', 'event_id', 'account_id', 'asset_id', 'quantity'):
        if not facts[name]:
            issues.append(f'{name}: required')
    boundary = v2 and facts['event_type'] in {'opening', 'checkpoint'}
    try:
        if boundary and not facts['timestamp_utc']:
            raise LookupError
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})', facts['timestamp_utc']):
            raise ValueError
        stamp = datetime.fromisoformat(facts['timestamp_utc'].replace('Z', '+00:00'))
        if stamp.tzinfo is None or stamp.utcoffset() is None or stamp > datetime.now(timezone.utc):
            raise ValueError
        facts['timestamp_utc'] = stamp.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')
    except LookupError:
        pass
    except ValueError:
        issues.append('timestamp_utc: supply an executed, timezone-aware timestamp')
    if v2:
        from portfolio_app.security_history import day
        if boundary:
            try:
                day(facts['effective_date'])
                if facts['timestamp_utc'] and facts['timestamp_utc'][:10] > facts['effective_date']:
                    raise ValueError
            except (ValueError, DataError):
                issues.append('effective_date: supply a reporting boundary on or after the observation, no later than today')
        elif facts['effective_date']:
            issues.append('Only opening balances and checkpoints use effective_date')
        for prefix, amount in (('cost', 'acquisition_cost'), ('valuation', 'valuation_amount')):
            quality = facts[prefix+'_quality']
            if quality not in ('', 'recorded', 'estimated'):
                issues.append(prefix+'_quality: use recorded or estimated')
            if facts[amount] and (not quality or not facts[prefix+'_source']):
                issues.append(prefix+': supplied amounts require quality and source')
            if not facts[amount] and quality:
                issues.append(prefix+': unknown amounts must not claim recorded or estimated quality')
        if facts['acquisition_currency'] and facts['acquisition_currency'] not in CURRENCIES:
            issues.append('acquisition_currency: use a supported fiat currency')
        if facts['acquisition_cost'] and not facts['acquisition_currency']:
            issues.append('Acquisition cost requires its currency')
        if facts['cost_quantity']:
            try:
                if not facts['acquisition_cost'] or not 0 < Decimal(facts['cost_quantity']) <= Decimal(facts['quantity']):
                    raise ValueError
            except (ValueError, InvalidOperation):
                issues.append('cost_quantity must be positive, no greater than quantity, and have a supplied cost')
        if facts['event_type'] not in {'opening', 'checkpoint', 'inventory_in'} and any(facts[k] for k in ('acquisition_cost','acquisition_currency','cost_quantity','cost_quality','cost_source')):
            issues.append('Acquisition evidence belongs to an opening, checkpoint or inventory receipt')
        if facts['quantity'] == '0' and facts['acquisition_cost'] not in ('', '0'):
            issues.append('An empty balance cannot have positive acquisition cost')
        if facts['event_type'] in {'inventory_in', 'inventory_out'}:
            if facts['flow_classification'] not in ('unresolved', 'external'):
                issues.append('Inventory movements require unresolved or external flow_classification')
        elif facts['flow_classification']:
            issues.append('Only inventory movements use flow_classification')
    if facts['sequence'] and (not facts['sequence'].isdigit() or int(facts['sequence']) >= 1000000):
        issues.append('sequence: use an integer from 0 to 999999')
    elif facts['sequence']:
        facts['sequence'] = str(int(facts['sequence']))
    kind = facts['event_type']
    if kind not in KINDS | (INVENTORY_KINDS if v2 else set()):
        issues.append('Unsupported event_type')
    for name in ('currency', 'fee_currency', 'valuation_currency'):
        if facts[name] and facts[name] not in CURRENCIES:
            issues.append(f'{name}: use a supported fiat currency; assets have separate fields')
    if facts['fee_currency'] and facts['fee_asset_id']:
        issues.append('A fee must use either fee_currency or fee_asset_id')
    if facts['fee_amount'] != '0' and not (facts['fee_currency'] or facts['fee_asset_id']):
        issues.append('Specify the denomination of the additional fee, or explicitly enter zero')
    if kind in {'buy', 'sell'} and not facts['currency']:
        issues.append('Trade consideration requires a fiat currency')
    if kind not in {'buy', 'sell'} and (facts['gross_amount'] or facts['currency']):
        issues.append('Only fiat trades use gross_amount and currency')
    if kind in {'swap', 'reward'} | ({'inventory_in', 'inventory_out'} if v2 else set()):
        if facts['valuation_amount'] and not facts['valuation_currency']:
            issues.append('Supplied valuation requires valuation_currency')
    elif facts['valuation_amount'] or facts['valuation_currency']:
        issues.append('Valuation fields require a swap, reward or inventory movement')
    if boundary and (facts['fee_amount'] != '0' or facts['fee_currency'] or facts['fee_asset_id']):
        issues.append('A balance is not a transaction; specify fee_amount=0 with no fee denomination')
    if kind == 'swap':
        if not facts['received_asset_id'] or not facts['received_quantity']:
            issues.append('Swap requires received_asset_id and received_quantity')
        if facts['asset_id'] == facts['received_asset_id']:
            issues.append('A swap requires different assets')
    elif facts['received_asset_id'] or facts['received_quantity']:
        issues.append('Only swaps have received_asset_id and received_quantity')
    if kind in {'transfer', 'stake', 'unstake'}:
        if not facts['counter_account_id'] or facts['account_id'] == facts['counter_account_id']:
            issues.append('Own transfer requires a different destination custody account')
    elif facts['counter_account_id']:
        issues.append('Only own transfers have counter_account_id')
    return TransactionRow(raw, facts, tuple(issues), row_number)


def read_csv(content, *, layout='generic', source_id='', asset_fees=()):
    try:
        text = content.decode('utf-8-sig') if isinstance(content, bytes) else content
        reader = csv.DictReader(StringIO(text), strict=True)
        if not reader.fieldnames or len(reader.fieldnames) != len(set(reader.fieldnames)):
            raise DataError('CSV needs unique column headers.')
        allowed = set(FIELDS if layout == 'crypto' else FIELDS_V2) | ({'fees', 'valuation_eur'} if layout == 'crypto' else set())
        unknown = set(reader.fieldnames) - allowed
        if unknown:
            raise DataError('Unrecognized columns: ' + ', '.join(sorted(unknown)))
        rows = []
        for line, raw in enumerate(reader, 2):
            if None in raw or any(v is None for v in raw.values()):
                raise DataError(f'Row {line}: column count differs from the header.')
            rows.append(normalize(raw, layout=layout, source_id=source_id, asset_fees=asset_fees, row_number=line))
        if not rows:
            raise DataError('CSV contains no transaction rows.')
        return tuple(rows)
    except (UnicodeError, csv.Error) as exc:
        raise DataError(f'Use UTF-8 comma-separated CSV: {exc}') from exc
