"""Anonymous Eurex website delayed quotes; no orders, user credentials or postings.

The public widget's session token is used only in memory. The protocol and
normalization are isolated here because this website is not a guaranteed API.
"""
from datetime import date, timedelta
from decimal import Decimal
import json
import re
from time import monotonic
from urllib.parse import urlencode

from websockets.sync.client import connect
from websockets.exceptions import WebSocketException

from portfolio_app.derivative_market import _known_product, _rows, _url, download, statistics_request
from portfolio_app.derivative_types import PriceObservation
from portfolio_app.futures_clock import business_date, instant, utc_now
from portfolio_app.holdings import DataError

HOST = 'eurex-api.factsetdigitalsolutions.com'


class WebsiteSession:
    """Bounded, non-streaming reads using the public widget's own protocol."""
    def __init__(self, token):
        self.token = token
        self.connection = None
        self.job = 0

    def __enter__(self):
        self.connection = connect('wss://' + HOST + '/ws',
            subprotocols=['v2.ws-jsjson.mdgms.com'], origin='https://www.eurex.com',
            open_timeout=10, close_timeout=2, max_size=2**20)
        try:
            self.connection.send(json.dumps(dict(Message='AuthenticationByTokenRequest', Version=1,
                token={'value': {'b64': self.token}}, software='Portfolio public delayed quotes', os='',
                feature_flags_wanted={'value': 0}, maximum_idle_interval=60000000,
                maximum_receivable_message_size=1048576, flags=0,
                cache_authentication_salt={'value': []},
                cache_authentication_encrypted_secret={'encrypted_secret': []})))
            reply = json.loads(self.connection.recv(timeout=10))
            if reply.get('Message') != 'Foundation::AuthenticationByTokenResponse':
                raise DataError('Public quote session is unavailable.')
            return self
        except BaseException:
            self.connection.close()
            raise

    def __exit__(self, *_):
        self.connection.close()
        self.token = ''

    def get(self, path, **params):
        self.job += 1
        self.connection.send(json.dumps(dict(Message='HighLevelRequest', Version=3,
            header=dict(dataset={'id_dataset': 0}, id_job=self.job, flags_r2=0,
                resend_counter=0, timeout=6000,
                authentication_identifiers={'id_application': -2, 'id_user': -2},
                cache_key={'value': []}, previous_response_hash={'value': []},
                tracing={'value': {'value': ''}}),
            accept='application/json', content_type='application/json', body={'value': []},
            query=urlencode(params), path='/api/v3/' + path, method={'value': 1})))
        deadline = monotonic() + 10
        for _ in range(12):
            remaining = deadline - monotonic()
            if remaining <= 0:
                break
            raw = json.loads(self.connection.recv(timeout=remaining))
            if raw.get('Message') == 'Foundation::ErrorResponse':
                raise DataError('Public quote request failed; saved prices are retained.')
            if raw.get('header', {}).get('id_job') != self.job:
                continue
            if raw.get('status_code', {}).get('value') != 200:
                raise DataError('Public quote request was not accepted.')
            result = json.loads(raw['body']['value'])
            return result['data']
        raise DataError('Public quote response timed out.')


def monthly_notation(listing, contract):
    """The quote vendor labels MSCI by settlement date, statistics by last trade.

    Only a unique monthly series with the same year/month can bridge those two
    labels, after the canonical exact expiry was verified in Eurex statistics.
    Daily/flexible/rolling contracts never qualify.
    """
    candidates = [row for row in listing['contracts']
                  if row.get('idMaturityInterval') == 1
                  and row.get('dateMaturity', '')[:7] == contract.expiry[:7]]
    if len(candidates) != 1:
        raise DataError('Exact monthly quote series is missing or ambiguous.')
    return str(candidates[0]['idNotation'])


def parse_quote(contract, notation, metadata, raw, *, now=None):
    now = now or utc_now()
    product = _known_product(contract)
    try:
        series = metadata['class']
        lifecycle = metadata['lifeCycle']
        expiry = date.fromisoformat(contract.expiry)
        unit = metadata['settlement']['valueUnit']
        if (contract.return_convention != product.return_convention
                or series['isin'] != product.product_isin or series['symbol'] != product.product
                or metadata['market']['name'] != 'Eurex'
                or Decimal(str(metadata['contractSize'])) != contract.multiplier
                or unit['code'] != contract.currency
                or lifecycle['maturity']['interval'] != 'monthly'
                or lifecycle['expiration'] != {'month': expiry.month, 'year': expiry.year}
                or metadata['normalizedSymbol'] != f'{product.product}/{expiry:%y%m}'
                or str(metadata['sourceIdentifier']) != notation
                or str(raw['sourceIdentifier']) != notation or str(raw['idNotation']) != notation
                or raw['currency']['id'] != unit['id']
                or raw['market']['id'] != metadata['market']['id']):
            raise ValueError('Quote identity differs from the exact contract')
        if raw['quality'] != 'DLY':
            raise ValueError('Unverified price quality')
        stamp = instant(raw['latest']['time'])
        if stamp > now + timedelta(seconds=5):
            raise ValueError('Quote time is in the future')
        # The feed can call a settlement a last price (even numberTrades=1).
        # A positive actual trade volume distinguishes the observed trade path.
        if not raw['latest'].get('tradingVolume') or raw['latest']['tradingVolume'] <= 0:
            raise ValueError('No timestamped trade; retain dated settlement/manual valuation')
        if not raw['accumulated'].get('tradingVolume') or raw['accumulated']['tradingVolume'] <= 0:
            raise ValueError('No verified trading activity')
        return PriceObservation(contract.id, business_date(stamp).isoformat(), raw['latest']['price'],
            kind='mark', source=product.source, observed_at=now.isoformat(),
            quote_at=stamp.isoformat(), delay_minutes=15)
    except (KeyError, TypeError, ValueError, ArithmeticError) as exc:
        raise DataError(f'Cannot verify exact-contract trade: {exc}') from exc


def fetch_quote(contract, *, fetch=download, session=WebsiteSession, now=None):
    product = _known_product(contract)
    now = now or utc_now()
    if business_date(now).isoformat() > (contract.last_trade_date or contract.expiry):
        raise DataError('Contract no longer trades; retain its historical valuation.')
    try:
        html = fetch(product.source)
        settings = [json.loads(value) for value in re.findall(
            r'<script\b[^>]*data-product="' + re.escape(product.statistics_id) + r'"[^>]*>(.*?)</script>', html, re.S)]
        settings = [s for s in settings if s.get('isin') == product.product_isin and s.get('factsetHostUrl') == HOST]
        if len(settings) != 1:
            raise ValueError('Public quote configuration is missing or ambiguous')
        day, _ = statistics_request(html, product)
        rows = _rows(json.loads(fetch(_url(product, day))), product, day)
        exact = [r for r in rows if r.get('contractType') == 'M' and r.get('date') == contract.expiry.replace('-', '')]
        monthly = [r for r in rows if r.get('contractType') == 'M' and r.get('date', '')[:6] == contract.expiry.replace('-', '')[:6]]
        if len(exact) != 1 or len(monthly) != 1:
            raise ValueError('Canonical exact monthly expiry could not be verified')
        with session(settings[0]['connectionToken']) as client:
            listing = client.get('customer/contract/get', isin=product.product_isin,
                                 zeroValues='true', _paginationOffset=0, _paginationLimit=500)
            if len(listing['contracts']) >= 500:
                raise ValueError('Quote listing is incomplete')
            notation = monthly_notation(listing, contract)
            identity = dict(identifier=notation, identifierType='idNotation')
            metadata = client.get('future/get', **identity)
            raw = client.get('customer/prices/get', **identity)
        return parse_quote(contract, notation, metadata, raw, now=now)
    except DataError:
        raise
    except (OSError, TimeoutError, WebSocketException, KeyError, ValueError, TypeError) as exc:
        # Never leak the session token or raw provider responses into saved errors.
        raise DataError('Free delayed quotes unavailable; previous dated prices are retained.') from exc
