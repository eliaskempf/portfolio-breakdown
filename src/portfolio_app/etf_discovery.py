"""Exact-identity discovery using official provider catalogues and product metadata."""
from dataclasses import replace
from functools import partial
from datetime import datetime
import json
import math
import re
from urllib.parse import urlparse
import xml.etree.ElementTree as ET

from portfolio_app.holdings import DataError
from portfolio_app import ishares, dws, amundi, vanguard, spdr

ISHARES_CATALOG = ('https://www.ishares.com/varnish-api/blk-product-screener-server/api/v1/'
                   'product-screener/product-data?country=gb&language=en&siteName=ishares-uk&userType=individual')
ISHARES_GERMAN_CATALOG = ISHARES_CATALOG.replace('country=gb', 'country=de').replace('language=en', 'language=de').replace('siteName=ishares-uk', 'siteName=de-ishares-v2')
DWS_SITEMAP = 'https://etf.dws.com/en-gb/sitemap.xml'


def walk(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


def public_date(value):
    try:
        return datetime.strptime(str(value), '%Y%m%d').date().isoformat()
    except ValueError:
        return ''


def ishares_metadata(product_id, fetch, *, german=False):
    url = ('https://www.blackrock.com/varnish-api/uk-retail01-product-data/product-data/api/v2/get-product-data'
           '?appSubType=ISHARES&appType=PRODUCT_PAGE&component=keyFundFacts,fundamentalsAndRisk,exposureBreakdowns,listings'
           f'&locale=en_GB&portfolioId={product_id}&targetSite=ishares-uk&userType=individual')
    if german:
        url = url.replace('locale=en_GB', 'locale=de_DE').replace('targetSite=ishares-uk', 'targetSite=de-ishares-v2')
    doc = json.loads(fetch(url))
    components = doc['componentsByNameMap']
    facts = {item['name']: item.get('value') for item in walk(components.get('keyFundFacts', {}))
             if 'name' in item and 'value' in item}
    listings = {item['name']: item.get('value') for item in walk(components.get('listings', {}))
                if 'name' in item and 'value' in item}
    suffixes = {'Deutsche Boerse Xetra': '.DE', 'Xetra': '.DE', 'London Stock Exchange': '.L',
                'Euronext Amsterdam': '.AS', 'Borsa Italiana': '.MI', 'SIX Swiss Exchange': '.SW'}
    facts['listing_tickers'] = [ticker + suffixes[exchange] for ticker, exchange in
                                zip(listings.get('ticker', []) or [], listings.get('exchange', []) or [])
                                if isinstance(ticker, str) and exchange in suffixes]
    from portfolio_app.instruments import CATALOG
    facts['listing_tickers'] = list(dict.fromkeys([
        *facts['listing_tickers'], *(item.ticker for item in CATALOG if item.isin == facts.get('isin'))]))
    summaries = {}
    for item in walk(components.get('fundamentalsAndRisk', {})):
        if item.get('name') in {'effectiveDuration', 'weightedAverageMaturity', 'weightedAverageYieldToMaturity',
                                'weightedAverageCoupon', 'yieldToWorst', 'modelOad', 'weightedAvgLife', 'weightedAvgCoupon'} and isinstance(item.get('value'), (int, float)):
            stamp = public_date(item.get('asOfDate'))
            if stamp and math.isfinite(item['value']):
                summaries[item['name']] = {'value': item['value'], 'as_of': stamp, 'source': url}
    for key, title in [('rating', 'Credit quality'), ('geography', 'Country')]:
        node = components.get('exposureBreakdowns', {}).get('containersByNameMap', {}).get(key, {})
        for child in walk(node):
            points = child.get('dataPointsByNameMap', {})
            values = {k: v.get('value') for k, v in points.items()}
            labels = values.get('type') or values.get('names') or values.get('label') or values.get('name')
            weights = values.get('fund')
            stamp = public_date(values.get('asOf'))
            if isinstance(labels, list) and isinstance(weights, list) and len(labels) == len(weights) and stamp:
                if all(isinstance(w, (int, float)) and math.isfinite(w) and 0 <= w <= 100 for w in weights):
                    total = math.fsum(weights)
                    if 0 < total <= 100 + 1e-8:
                        rows = [{'label': str(n), 'percentage': w} for n, w in zip(labels, weights, strict=True)]
                        if total < 100 - 1e-8:
                            rows.append({'label': 'Unknown / rounding remainder', 'percentage': 100 - total})
                        summaries[title] = {'rows': rows, 'as_of': stamp, 'source': url}
    return str(facts.get('isin', '')), str(doc.get('fundName', '')), facts, summaries


def source_from_url(url, fetch, *, expected_isin=''):
    from portfolio_app.etf_sources import Source
    parsed = urlparse(url)
    if parsed.scheme != 'https' or parsed.username or parsed.password:
        raise DataError('Supply an HTTPS official product page.')
    if parsed.hostname in {'www.ishares.com', 'www.blackrock.com'}:
        match = re.search(r'/(?:products|produkte|producten)/(\d+)(?:/|$)', parsed.path)
        if not match:
            raise DataError('Use the official English iShares product page.')
        pid = match[1]
        isin, name, facts, summaries = ishares_metadata(pid, fetch)
        asset_class = {'Equity': 'equity', 'Fixed Income': 'fixed_income', 'Money Market': 'money_market'}.get(facts.get('assetClass'), 'unknown')
        structure = str(facts.get('productStructure', '')).lower()
        if structure != 'physical':
            raise DataError('Economic breakdown is not yet supported for this iShares replication method.')
        parser = partial(ishares.parse_holdings_json, allow_partial_bonds=True) if isin == 'IE00BDBRDM35' else ishares.parse_holdings_json
        source = Source('isin_' + isin.lower(), name, tuple(facts['listing_tickers']), 'iShares', ishares.holdings_url(pid), parser,
                        asset_class=asset_class, replication='physical', product_url=url, summaries=summaries)
    elif parsed.hostname == 'etf.dws.com':
        match = re.fullmatch(r'/(?:en-gb|de-de)/([A-Z]{2}[A-Z0-9]{9}\d-[a-z0-9-]+)/?', parsed.path)
        if not match:
            raise DataError('Use an official Xtrackers product page containing its ISIN.')
        slug = match[1]
        base = 'https://etf.dws.com/api/pdp/en-gb/etf/' + slug + '/'
        doc = json.loads(fetch(base + 'pdpMetaTagsTealium'))['pdpResult']
        header = doc['pageFrame']['productHeader']
        facts = {x['key']: x['value'] for x in walk(doc['pageSections'].get('keyFacts', {})) if 'key' in x and 'value' in x}
        isin = str(facts.get('ISIN', ''))
        name = header['texts']['title']
        method = str(facts.get('Investment methodology', ''))
        replication = 'synthetic' if 'Swap' in method else 'physical' if 'Direct Replication' in method else 'unknown'
        asset_class = 'unknown'
        for item in header.get('tableValues', []):
            if item.get('key') == 'Asset class':
                labels = ' '.join(x.get('text', '') for x in item.get('value', []) if isinstance(x, dict))
                asset_class = 'equity' if 'Equit' in labels else 'fixed_income' if 'Fixed Income' in labels else 'unknown'
        basis = 'holdings'
        if isin == 'LU0290358497' and replication == 'synthetic':
            asset_class, basis = 'money_market', 'economic'
        elif replication != 'physical':
            raise DataError('Economic breakdown is not yet supported for this replication method; fund stays whole.')
        source = Source('isin_' + isin.lower(), name, (), 'Xtrackers', base + 'holdings', partial(dws.parse_holdings, allow_signed=basis == 'economic'),
                        asset_class=asset_class, replication=replication, breakdown_basis=basis, product_url=url,
                        wkn=str(facts.get('WKN', '')))
    elif parsed.hostname in {'www.amundietf.lu', 'www.amundietf.com', 'www.amundietf.de', 'www.amundietf.co.uk'}:
        candidate = parsed.path.rstrip('/').split('/')[-1].upper()
        if not re.fullmatch('[A-Z]{2}[A-Z0-9]{9}[0-9]', candidate):
            raise DataError('Use an official Amundi product page containing its ISIN')
        isin, source = amundi.discover(fetch, candidate, product_url=url)
    elif parsed.hostname == 'www.vanguard.co.uk':
        isin, source = vanguard.source(url, fetch, expected_isin=expected_isin)
    elif parsed.hostname == 'www.ssga.com':
        isin, source = spdr.source(url, fetch, expected_isin=expected_isin)
    else:
        raise DataError('Automatic sources support official iShares, Xtrackers, Amundi, Vanguard and State Street/SPDR product pages.')
    if not re.fullmatch('[A-Z]{2}[A-Z0-9]{9}[0-9]', isin) or (expected_isin and expected_isin != isin):
        raise DataError('Provider identity does not match the requested ISIN.')
    if source.asset_class == 'unknown':
        raise DataError('Provider asset class is not supported; fund stays whole.')
    return isin, source


class Discovery:
    """One catalogue request per provider per batch; no portfolio contents sent."""
    def __init__(self, fetch, *, identity_lookup=None):
        self.fetch = fetch
        self._catalog = None
        self._urls = None
        self._german_catalog = None
        self.identity_lookup = identity_lookup
        self._vanguard_catalog = None
        self._spdr_catalog = None

    def _confirm_wkn(self, isin, source, wkn):
        if source.provider == 'iShares':
            match = re.search(r'/(?:products|produkte|producten)/(\d+)', source.product_url)
            if not match:
                return None
            confirmed, _, facts, _ = ishares_metadata(match[1], self.fetch, german=True)
            if confirmed == isin and facts.get('wkn') == wkn:
                return replace(source, wkn=wkn)
        elif source.provider == 'Xtrackers':
            url = source.url.rsplit('/', 1)[0].replace('/en-gb/', '/de-de/') + '/pdpMetaTagsTealium'
            facts = {x['key']: x['value'] for x in walk(json.loads(self.fetch(url))) if 'key' in x and 'value' in x}
            if facts.get('ISIN') == isin and facts.get('WKN') == wkn:
                return replace(source, wkn=wkn)
        elif source.wkn and source.wkn == wkn:
            return source
        return None

    def _resolve_identifier(self, identifier):
        """Search suggestions are only candidates; the issuer must confirm the identifier."""
        from portfolio_app.instruments import CATALOG_BY_TICKER, lookup_isin_candidates
        wkn, ticker = identifier.get('wkn', ''), identifier.get('ticker', '')
        if not wkn and not re.fullmatch(r'[A-Z0-9^=-]+\.[A-Z]{1,4}', ticker):
            raise DataError('Supply an ISIN or connect an exchange-qualified listing in Positions → Connect live prices.')
        listing = CATALOG_BY_TICKER.get(ticker)
        candidates = [listing.isin] if listing and listing.isin and not wkn else []
        if ticker and not wkn and not candidates:
            catalogues = [self._catalog or {}]
            if ticker.endswith('.DE'):
                if self._german_catalog is None:
                    try:
                        self._german_catalog = json.loads(self.fetch(ISHARES_GERMAN_CATALOG))
                    except (OSError, ValueError):
                        self._german_catalog = {}
                catalogues.append(self._german_catalog)
            candidates = list(dict.fromkeys(row['isin'] for catalogue in catalogues for row in catalogue.values()
                if row.get('localExchangeTicker') == ticker.rsplit('.', 1)[0] and row.get('isin')))
        if not candidates:
            candidates = (self.identity_lookup or lookup_isin_candidates)(wkn or ticker)
        verified = {}
        for candidate in dict.fromkeys(candidates[:3]):
            try:
                isin, source = self.resolve({'isin': candidate})
                if wkn:
                    if confirmed_source := self._confirm_wkn(isin, source, wkn):
                        verified[isin] = confirmed_source
                elif not wkn and ticker in source.tickers:
                    verified[isin] = source
            except (DataError, OSError, ValueError, KeyError, TypeError):
                continue
        if len(verified) != 1:
            raise DataError('No unique provider-confirmed ISIN found. Use Positions → Connect live prices or supply an official product page.')
        return next(iter(verified.items()))

    def resolve(self, identifier, *, product_url=''):
        from portfolio_app.etf_sources import SOURCES
        isin = str(identifier.get('isin', '')).strip().upper()
        wkn = str(identifier.get('wkn', '')).strip().upper()
        ticker = str(identifier.get('ticker', '')).strip().upper()
        if product_url:
            resolved, source = source_from_url(product_url, self.fetch, expected_isin=isin)
            if wkn and not isin:
                source = self._confirm_wkn(resolved, source, wkn)
                if source is None:
                    raise DataError('The official product identity does not confirm this position’s WKN.')
            return resolved, source
        if isin in SOURCES:
            return isin, SOURCES[isin]
        if isin == amundi.ISIN:
            return amundi.discover(self.fetch)
        if self._catalog is None:
            try:
                self._catalog = json.loads(self.fetch(ISHARES_CATALOG))
            except (OSError, ValueError):
                self._catalog = {}
        matches = [row for row in self._catalog.values() if isinstance(row, dict) and 'etf' in row.get('productView', [])
                   and ((row.get('isin') == isin) if isin else (wkn and row.get('wkn') == wkn))]
        if len(matches) == 1:
            row = matches[0]
            result_isin, source = source_from_url('https://www.ishares.com' + row['productPageUrl'], self.fetch,
                                                  expected_isin=isin or row['isin'])
            return result_isin, replace(source, wkn=row.get('wkn', ''))
        if len(matches) > 1:
            raise DataError('Ambiguous provider identity. Select an exact product page.')
        if not isin:
            return self._resolve_identifier({'wkn': wkn, 'ticker': ticker})
        errors = []
        if self._urls is None:
            try:
                root = ET.fromstring(self.fetch(DWS_SITEMAP))
                self._urls = [node.text for node in root.iter() if node.tag.endswith('}loc') and node.text]
            except (OSError, ValueError, ET.ParseError) as exc:
                errors.append(str(exc))
                self._urls = []
        urls = [u for u in self._urls if re.search('/' + re.escape(isin) + '-', u)]
        if len(urls) == 1:
            return source_from_url(urls[0], self.fetch, expected_isin=isin)
        # A catalogue outage must not hide a match at a different provider.
        for provider, attribute in [(spdr, '_spdr_catalog'), (vanguard, '_vanguard_catalog')]:
            try:
                catalogue = getattr(self, attribute)
                if catalogue is None:
                    catalogue = provider.catalog(self.fetch)
                    setattr(self, attribute, catalogue)
            except (OSError, ValueError, KeyError, TypeError, ET.ParseError) as exc:
                errors.append(str(exc))
                continue
            if provider is spdr:
                urls = {url for identifiers, url in catalogue if isin in identifiers}
            else:
                urls = {url for profile, url in catalogue if vanguard.identity(profile).get('ISIN') == isin}
            if len(urls) > 1:
                raise DataError('Ambiguous provider identity. Select an exact product page.')
            if urls:
                return source_from_url(urls.pop(), self.fetch, expected_isin=isin)
        try:
            result = amundi.lookup(isin, self.fetch)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            errors.append(str(exc))
        else:
            if result:
                return result
        detail = ' Some provider catalogues were unavailable or unsupported; try an official product page.' if errors else ''
        raise DataError('No supported official source found. Supply a product page or a normalized holdings CSV.' + detail)
