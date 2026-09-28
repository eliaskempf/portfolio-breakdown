"""Local company-country classifications, independent of valuation and rendering.

countries.py contains public UN M49 country/area groupings (retrieved 2026-09-27,
https://unstats.un.org/unsd/methodology/m49/) and ISO codes. US is separated from
Northern America; Taiwan is retained separately under Asia. No runtime download.
"""
from collections import defaultdict
from dataclasses import dataclass
from functools import lru_cache

import pandas as pd

from portfolio_app.aggregation import classify_exposures
from portfolio_app.countries import COUNTRIES
from portfolio_app.etf import FundSnapshot, constituent_resolver
from portfolio_app.taxonomy import Classifications, TaxonomyPath

UNKNOWN = 'Unknown geography'
SPECIAL = {'Gold', 'Crypto', 'Cash'}
REGIONS = {'United States', 'Other North America', 'Latin America & Caribbean',
           'Europe', 'Asia', 'Africa', 'Oceania'}
COUNTRY_UNSPECIFIED = 'Country unspecified'


def _text(value) -> str:
    return value.strip() if isinstance(value, str) else ''


@lru_cache(maxsize=1)
def country_lookup() -> dict[str, TaxonomyPath]:
    """Accept exact names and ISO codes, never exchange or currency guesses."""
    result = {}
    display = {'USA': 'United States', 'GBR': 'United Kingdom', 'KOR': 'South Korea',
               'PRK': 'North Korea', 'HKG': 'Hong Kong', 'MAC': 'Macao',
               'RUS': 'Russia', 'TUR': 'Turkey', 'VNM': 'Vietnam',
               'NLD': 'Netherlands', 'BOL': 'Bolivia', 'VEN': 'Venezuela',
               'TZA': 'Tanzania', 'IRN': 'Iran', 'SYR': 'Syria', 'LAO': 'Laos',
               'MDA': 'Moldova', 'PSE': 'Palestine', 'BRN': 'Brunei'}
    for country, alpha2, alpha3, region in COUNTRIES:
        name = display.get(alpha3, country)
        path = (region, name)
        for alias in (name, country, alpha2, alpha3):
            result[alias.casefold()] = path
    for alias, code in {'UK': 'GB', 'U.K.': 'GB', 'U.S.': 'US', 'U.S.A.': 'US',
                        'Korea': 'KR', 'Korea, Republic of': 'KR', 'Republic of Korea': 'KR',
                        'Taiwan, Province of China': 'TW', 'Taiwan, China': 'TW',
                        'Czech Republic': 'CZ', 'Hong Kong SAR': 'HK', 'Macau': 'MO',
                        'United Kingdom of Great Britain and Northern Ireland': 'GB'}.items():
        result[alias.casefold()] = result[code.casefold()]
    return result


def geography_path(path: TaxonomyPath) -> TaxonomyPath:
    """Normalize local country-only/region-country paths and terminal buckets."""
    leaf = path[-1].strip()
    special = {name.casefold(): name for name in SPECIAL | {UNKNOWN}}
    if leaf.casefold() in special:
        return (special[leaf.casefold()],)
    if leaf == COUNTRY_UNSPECIFIED and len(path) == 2 and path[0] in REGIONS:
        return path
    country = country_lookup().get(leaf.casefold())
    if country is not None:
        return country
    region = next((r for r in REGIONS if r.casefold() == leaf.casefold()), None)
    if region:
        return (region, COUNTRY_UNSPECIFIED)
    return (UNKNOWN,)


@dataclass(frozen=True)
class Geography:
    paths: dict[str, tuple[TaxonomyPath, ...]]
    sources: dict[str, str]
    reasons: dict[str, str]

    def classifications(self, asset_ids) -> Classifications:
        return {asset: {'geography': self.paths.get(asset, ((UNKNOWN,),))} for asset in asset_ids}


def resolve_geography(holdings: pd.DataFrame, funds: list[FundSnapshot],
                      manual: Classifications) -> Geography:
    """Resolve analytical identities before combining direct and indirect values.

    Manual geography is authoritative. Provider disagreements never use file
    order. Metadata is read from original instruments/constituents, not expanded
    rows which may carry parent fund fields. Missing data is not a disagreement.
    """
    records = defaultdict(list)
    for row in holdings.to_dict('records'):
        asset = _text(row.get('analysis_asset_id')) or row['id']
        records[asset].append((row, 'Instrument metadata'))
    resolve = constituent_resolver(holdings)
    for fund in funds:
        source = f'{fund.name} · {fund.as_of}' + (' (proxy)' if fund.proxy_source else '')
        for row in fund.constituents.to_dict('records'):
            asset, _ = resolve(row)
            records[asset].append((row, source))
    paths, sources, reasons = {}, {}, {}
    for asset in records.keys() | manual.keys():
        entry = manual.get(asset, {})
        configured = entry.get('geography', ())
        if configured:
            normalized = tuple(dict.fromkeys(geography_path(path) for path in configured))
            paths[asset] = normalized
            sources[asset] = 'Local geography classification'
            if (UNKNOWN,) in normalized:
                reasons[asset] = 'Local geography is unknown or unrecognized'
            continue
        candidates = set()
        evidence = set()
        for path in entry.get('asset_class', ()):
            if path[-1].casefold() in {s.casefold() for s in SPECIAL}:
                candidates.add(geography_path(path))
                evidence.add('Local asset class')
        country_candidates, country_sources = set(), set()
        for row, source in records[asset]:
            kind = _text(row.get('instrument_type')).casefold()
            if kind in {'crypto', 'cash'}:
                candidates.add((kind.title(),))
                evidence.add(source)
            # Exact public issuer identity already supported by the listing catalog:
            # https://www.euwax-gold.de/ewg2ld/ . Physical/ETC alone is insufficient.
            if _text(row.get('isin')).upper() == 'DE000EWG2LD7':
                candidates.add(('Gold',))
                evidence.add('Verified gold instrument (ISIN)')
            country = _text(row.get('country'))
            if country and kind not in {'etf', 'etc', 'cash', 'crypto', 'physical', 'non_equity'}:
                normalized = country_lookup().get(country.casefold())
                if normalized:
                    country_candidates.add(normalized)
                    country_sources.add(source)
        if not candidates:
            candidates, evidence = country_candidates, country_sources
        if len(candidates) == 1:
            paths[asset] = tuple(candidates)
        else:
            paths[asset] = ((UNKNOWN,),)
            reasons[asset] = ('Conflicting local metadata' if candidates else
                              'No supported local country or asset classification')
        sources[asset] = '; '.join(sorted(evidence)) or 'No local metadata'
    return Geography(paths, sources, reasons)


def geography_allocations(exposures: pd.DataFrame, geography: Geography) -> pd.DataFrame:
    """Use the common equal-path allocation semantics, retaining missing values."""
    return classify_exposures(exposures, geography.classifications(exposures.asset_id), 'geography')


def geography_table(allocations: pd.DataFrame, *, level: str = 'Regions',
                    root: TaxonomyPath = (), denominator: float, complete: bool) -> pd.DataFrame:
    """Summarize assigned amounts without dropping unknown or unpriced assets."""
    rows = allocations.loc[allocations.path.map(lambda path: path[:len(root)] == root)].copy()
    rows['Category'] = rows.path.map(lambda path: path[0] if level == 'Regions' else
                                    path[-1] if path[-1] != COUNTRY_UNSPECIFIED else
                                    f'{path[0]} / {COUNTRY_UNSPECIFIED}')
    grouped = rows.groupby('Category', sort=False).value
    table = grouped.sum(min_count=1).rename('EUR value').to_frame()
    table['Missing valuations'] = grouped.size() - grouped.count()
    table['% of selected portfolio'] = (100 * table['EUR value'] / denominator
                                        if complete and denominator > 0 else float('nan'))
    return table.reset_index().sort_values(['EUR value', 'Category'], ascending=[False, True],
                                           na_position='last', ignore_index=True)
