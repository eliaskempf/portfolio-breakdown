"""Explicit integrations for the requested World, EM IMI and momentum funds.

Provider exports remain in the private workspace. Importing never creates or
changes positions. The Amundi integration is explicitly an iShares proxy.
"""

import argparse
from collections.abc import Callable
from dataclasses import dataclass, replace
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


SOURCES = {
    'IE00BJ0KDQ92': Source('xtrackers_world', 'Xtrackers MSCI World UCITS ETF 1C', ('XDWD.L', 'XDWD.DE'),
                          'Xtrackers', dws.SOURCE, dws.parse_holdings),
    'IE00BKM4GZ66': Source('ishares_em_imi', 'iShares Core MSCI EM IMI UCITS ETF', ('EIMI.L', 'IS3N.DE'),
                          'iShares', ishares.download_url('264659'), ishares.parse_holdings),
    'LU1681041460': Source('amundi_europe_momentum', 'Amundi MSCI Europe Momentum UCITS ETF', ('MCEU.PA',),
                          'iShares', ishares.download_url('272019'), ishares.parse_holdings,
                          'iShares Edge MSCI Europe Momentum Factor UCITS ETF; same-index proxy, not Amundi holdings'),
}


def download(url: str) -> bytes:
    with urlopen(Request(url, headers={'User-Agent': 'Mozilla/5.0 (Portfolio breakdown)'}), timeout=25) as response:
        content = response.read(15 * 1024 * 1024 + 1)
    if len(content) > 15 * 1024 * 1024:
        raise DataError('ETF export exceeds the expected size')
    return content


def refresh_snapshot(fund: FundSnapshot, *, fetch: Callable[[str], bytes] | None = None, today: date | None = None) -> FundSnapshot:
    source = SOURCES.get(fund.isin)
    if source is None or fund.manifest_path is None:
        raise DataError('No configured provider integration for this ETF')
    manifest = fund.manifest_path
    prior = manifest.read_bytes() if manifest.exists() else None
    stamp, frame, notes = source.parse((fetch or download)(source.url))
    if stamp < fund.as_of or stamp > (today or date.today()):
        raise DataError('Provider returned an older or future snapshot; keeping the previous holdings')
    prior_ids = {r.isin: r.constituent_id for r in fund.constituents.itertuples() if r.isin}
    frame['constituent_id'] = [prior_ids.get(r.isin, r.constituent_id) if r.isin else r.constituent_id for r in frame.itertuples()]
    frame = validate_constituents(frame)
    raw = yaml.safe_load(prior) if prior else {
        'fund_id': fund.fund_id, 'name': fund.name, 'isin': fund.isin, 'tickers': list(fund.tickers),
    }
    if raw['isin'].upper() != fund.isin:
        raise DataError('ETF configuration changed; reload before updating')
    csv = frame.to_csv(index=False)
    filename = f'{manifest.stem}-{stamp}-{sha256(csv.encode()).hexdigest()[:12]}.csv'
    raw.update(as_of=stamp.isoformat(), holdings_file=filename, source=source.url,
               equity_fund=True, proxy_source=source.proxy_source, notes=notes)
    manifest.parent.mkdir(parents=True, exist_ok=True)
    staged = []
    try:
        for destination, text in [(manifest.parent / filename, csv), (manifest, yaml.safe_dump(raw, sort_keys=False))]:
            with NamedTemporaryFile(mode='w', encoding='utf-8', dir=manifest.parent, suffix='.tmp', delete=False) as handle:
                staged.append(Path(handle.name))
                handle.write(text)
            if destination == manifest and (manifest.read_bytes() if manifest.exists() else None) != prior:
                raise DataError('ETF configuration changed during download; reload before updating')
            staged[-1].replace(destination)
    finally:
        for path in staged:
            path.unlink(missing_ok=True)
    return replace(fund, as_of=stamp, source=source.url, constituents=frame, equity_fund=True,
                   proxy_source=source.proxy_source, notes=notes)


def install_snapshot(directory: Path, isin: str, *, fetch: Callable[[str], bytes] | None = None) -> FundSnapshot:
    """Install a requested snapshot, preserving existing configuration and files."""
    existing = next((f for f in load_funds(directory) if f.isin == isin), None)
    source = SOURCES[isin]
    path = directory / f'{source.fund_id}.yaml'
    if existing is None and path.exists():
        raise DataError('A different fund already uses this manifest name')
    fund = existing or FundSnapshot(source.fund_id, source.name, isin, source.tickers, date.min, source.url,
                                    pd.DataFrame(columns=['isin', 'constituent_id']), path)
    return refresh_snapshot(fund, fetch=fetch)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('isin', choices=sorted(SOURCES))
    parser.add_argument('--data-dir', type=Path, required=True)
    args = parser.parse_args()
    fund = install_snapshot(args.data_dir / 'etfs', args.isin)
    print(f'{fund.name}: {len(fund.constituents)} components, dated {fund.as_of}' + (' (proxy)' if fund.proxy_source else ''))


if __name__ == '__main__':
    main()
