"""Provider sources, validated snapshot publication, and an ISIN discovery CLI.

Legacy integrations remain explicit. New providers resolve into the same Source
interface and never change saved positions. Files stay in the private workspace.
"""

import argparse
import json
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import date
from hashlib import sha256
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.request import Request, urlopen

import pandas as pd
import yaml

from portfolio_app import dws, ishares
from portfolio_app.etf import FundSnapshot, load_funds, validate_constituents
from portfolio_app.holdings import DataError


@dataclass(frozen=True)
class Source:
    fund_id: str
    name: str
    tickers: tuple[str, ...]
    provider: str
    url: str
    parse: Callable[[bytes], tuple[date, pd.DataFrame, str]]
    proxy_source: str = ''
    asset_class: str = 'equity'
    replication: str = 'physical'
    breakdown_basis: str = 'holdings'
    product_url: str = ''
    wkn: str = ''
    summaries: dict = field(default_factory=dict)
    request_json: dict | None = None
    load_content: Callable | None = None


SOURCES = {
    'IE00BJ0KDQ92': Source('xtrackers_world', 'Xtrackers MSCI World UCITS ETF 1C', ('XDWD.L', 'XDWD.DE'),
                          'Xtrackers', dws.SOURCE, dws.parse_holdings),
    'IE00BKM4GZ66': Source('ishares_em_imi', 'iShares Core MSCI EM IMI UCITS ETF', ('EIMI.L', 'IS3N.DE'),
                          'iShares', ishares.download_url('264659'), ishares.parse_holdings),
    'LU1681041460': Source('amundi_europe_momentum', 'Amundi MSCI Europe Momentum UCITS ETF', ('MCEU.PA',),
                          'iShares', ishares.download_url('272019'), ishares.parse_holdings,
                          'iShares Edge MSCI Europe Momentum Factor UCITS ETF; same-index proxy, not Amundi holdings'),
}


def download(url: str, *, json_body: dict | None = None, request_headers: dict | None = None) -> bytes:
    headers = {'User-Agent': 'Mozilla/5.0 (Portfolio breakdown)'}
    headers.update(request_headers or {})
    body = None
    if json_body is not None:
        body = json.dumps(json_body).encode('utf-8')
        headers['Content-Type'] = 'application/json'
    with urlopen(Request(url, data=body, headers=headers), timeout=25) as response:
        content = response.read(15 * 1024 * 1024 + 1)
    if len(content) > 15 * 1024 * 1024:
        raise DataError('ETF export exceeds the expected size')
    return content


def source_for(fund: FundSnapshot, fetch=download) -> Source | None:
    # An explicit manual source must not fall back to the legacy ISIN registry.
    if fund.provider == 'manual':
        return None
    if fund.provider and fund.product_url:
        from portfolio_app.etf_discovery import source_from_url
        return source_from_url(fund.product_url, fetch, expected_isin=fund.isin)[1]
    return SOURCES.get(fund.isin)


def publish_snapshot(fund: FundSnapshot, frame: pd.DataFrame, stamp: date, *, source: Source | None = None,
                     notes: str = '', basket: pd.DataFrame | None = None, prior: bytes | None = None,
                     today: date | None = None) -> FundSnapshot:
    """Publish validated CSVs before switching the manifest; preserve prior files."""
    if fund.manifest_path is None:
        raise DataError('A private snapshot destination is required')
    if stamp < fund.as_of or stamp > (today or date.today()):
        raise DataError('Provider returned an older or future snapshot; keeping the previous holdings')
    manifest = fund.manifest_path
    prior_ids = {r.isin: r.constituent_id for r in fund.constituents.itertuples() if r.isin}
    frame = frame.copy()
    frame['constituent_id'] = [prior_ids.get(r.isin, r.constituent_id) if r.isin else r.constituent_id for r in frame.itertuples()]
    from portfolio_app.fund_summary import with_maturity
    frame = with_maturity(validate_constituents(frame), stamp)
    updated = replace(fund, as_of=stamp, constituents=frame, notes=notes, basket=basket)
    if source:
        if fund.wkn and source.wkn and fund.wkn != source.wkn:
            raise DataError('Provider WKN conflicts with the saved fund identity; review the snapshot.')
        updated = replace(updated, source=source.url, equity_fund=source.asset_class == 'equity',
                          proxy_source=source.proxy_source, provider=source.provider, product_url=source.product_url,
                          asset_class=source.asset_class, replication=source.replication,
                          breakdown_basis='proxy' if source.proxy_source else source.breakdown_basis,
                          summaries=source.summaries, wkn=source.wkn or fund.wkn,
                          tickers=tuple(dict.fromkeys((*fund.tickers, *source.tickers))))
    raw = yaml.safe_load(prior) if prior else {
        'fund_id': fund.fund_id, 'name': fund.name, 'isin': fund.isin, 'tickers': list(fund.tickers),
    }
    if raw['isin'].upper() != fund.isin:
        raise DataError('ETF configuration changed; reload before updating')
    payloads = []
    for key, table in [('holdings_file', frame), ('basket_file', basket)]:
        if table is None:
            raw.pop(key, None)
            continue
        csv = validate_constituents(table, allow_signed=key == 'basket_file').to_csv(index=False)
        filename = f'{manifest.stem}-{key}-{stamp}-{sha256(csv.encode()).hexdigest()[:12]}.csv'
        raw[key] = filename
        payloads.append((manifest.parent / filename, csv))
    raw.update(as_of=stamp.isoformat(), tickers=list(updated.tickers))
    for key in ('source', 'equity_fund', 'proxy_source', 'notes', 'provider', 'product_url', 'asset_class',
                'replication', 'breakdown_basis', 'wkn', 'summaries'):
        raw[key] = getattr(updated, key)
    payloads.append((manifest, yaml.safe_dump(raw, sort_keys=False)))
    manifest.parent.mkdir(parents=True, exist_ok=True)
    staged = []
    try:
        for destination, text in payloads:
            with NamedTemporaryFile(mode='w', encoding='utf-8', dir=manifest.parent, suffix='.tmp', delete=False) as handle:
                staged.append(Path(handle.name))
                handle.write(text)
            if destination == manifest and (manifest.read_bytes() if manifest.exists() else None) != prior:
                raise DataError('ETF configuration changed during download; reload before updating')
            staged[-1].replace(destination)
    finally:
        for path in staged:
            path.unlink(missing_ok=True)
    return updated


def record_aliases(fund: FundSnapshot, source: Source) -> FundSnapshot:
    """Attach issuer-verified identifiers to an existing snapshot, without changing its composition."""
    if fund.wkn and source.wkn and fund.wkn != source.wkn:
        raise DataError('Provider WKN conflicts with the saved fund identity; review the snapshot.')
    tickers = tuple(dict.fromkeys((*fund.tickers, *source.tickers)))
    wkn = source.wkn or fund.wkn
    if tickers == fund.tickers and wkn == fund.wkn:
        return fund
    prior = fund.manifest_path.read_bytes()
    updated = replace(fund, tickers=tickers, wkn=wkn)
    return publish_snapshot(updated, fund.constituents, fund.as_of, notes=fund.notes, basket=fund.basket, prior=prior)


def refresh_snapshot(fund: FundSnapshot, *, fetch: Callable[[str], bytes] | None = None,
                     today: date | None = None, source: Source | None = None) -> FundSnapshot:
    fetch = fetch or download
    if fund.manifest_path is None:
        raise DataError('No configured provider integration for this ETF')
    prior = fund.manifest_path.read_bytes() if fund.manifest_path.exists() else None
    source = source or source_for(fund, fetch)
    if source is None:
        raise DataError('No configured provider integration for this ETF')
    stamp, frame, notes, basket = retrieve_snapshot(fund.isin, source, fetch)
    return publish_snapshot(fund, frame, stamp, source=source, notes=notes, basket=basket, prior=prior, today=today)


def retrieve_snapshot(isin: str, source: Source, fetch=download):
    if source.load_content is not None:
        content = source.load_content(fetch)
    else:
        content = fetch(source.url) if source.request_json is None else fetch(source.url, json_body=source.request_json)
    stamp, frame, notes = source.parse(content)
    basket = None
    if source.breakdown_basis == 'economic':
        economic = {
            'LU0290358497': ('overnight:eur-estr-plus-8.5bp', 'EUR overnight rate · Solactive €STR +8.5 Daily Total Return Index'),
            'LU1190417599': ('overnight:eur-estr', 'EUR overnight rate · ESTR Compounded Index'),
        }
        if isin not in economic:
            raise DataError('No verified economic interpretation for this synthetic ETF')
        basket = frame if not frame.empty else None
        identity, name = economic[isin]
        frame = pd.DataFrame([dict(constituent_id=identity,
            name=name, ticker='', isin='', weight=1.,
            instrument_type='overnight_rate', exposure_kind='non_equity', market_currency='EUR')])
        notes = ('Economic exposure represents the overnight-rate benchmark, not a deposit or a portfolio of bonds. '
                 'Substitute basket securities are excluded from portfolio allocation. ' + notes)
    return stamp, frame, notes, basket


def install_snapshot(directory: Path, isin: str, *, fetch: Callable[[str], bytes] | None = None,
                     source: Source | None = None, today: date | None = None, product_url: str = '') -> FundSnapshot:
    """Discover and install a fund without changing any source position."""
    isin = isin.strip().upper()
    existing = next((f for f in load_funds(directory) if f.isin == isin), None)
    if existing and not product_url and source is None:
        return refresh_snapshot(existing, fetch=fetch, today=today)
    if source is None:
        from portfolio_app.etf_discovery import Discovery
        isin, source = Discovery(fetch or download).resolve({'isin': isin}, product_url=product_url)
    path = directory / f'{source.fund_id}.yaml'
    if existing is None and path.exists():
        raise DataError('A different fund already uses this manifest name')
    fund = existing or FundSnapshot(source.fund_id, source.name, isin, source.tickers, date.min, source.url,
                                    pd.DataFrame(columns=['isin', 'constituent_id']), path)
    return refresh_snapshot(fund, fetch=fetch, today=today, source=source)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('isin')
    parser.add_argument('--product-url', default='')
    parser.add_argument('--data-dir', type=Path, required=True)
    args = parser.parse_args()
    from portfolio_app.etf_setup import snapshot_writer
    with snapshot_writer(args.data_dir):
        fund = install_snapshot(args.data_dir / 'etfs', args.isin, product_url=args.product_url)
    print(f'{fund.name}: {len(fund.constituents)} components, dated {fund.as_of}' + (' (proxy)' if fund.proxy_source else ''))


if __name__ == '__main__':
    main()
