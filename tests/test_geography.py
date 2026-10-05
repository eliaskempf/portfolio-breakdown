"""Deliberately synthetic portfolios; no working data or network access."""
from datetime import date

import pandas as pd
import pytest

from portfolio_app.etf import FundSnapshot, expand_etfs, fund_classifications
from portfolio_app.exposures import normalize_exposures
from portfolio_app.geography import (
    UNKNOWN, country_lookup, geography_allocations, geography_path, geography_table, resolve_geography,
)
from portfolio_app.label_comparison import Label, compare_labels
from portfolio_app.company_merges import MergeSettings, build_plan


def holdings():
    return pd.DataFrame([
        dict(id='company', name='Invented Company', isin='ZZ1111111111', ticker='SYN-A', instrument_type='equity',
             position_id='p1', current_value_eur=100.),
        dict(id='fund', name='Invented Fund', isin='ZZ2222222222', ticker='SYN-F', instrument_type='etf',
             position_id='p2', current_value_eur=200.),
    ])


def fund(country='DE', *, asset='constituent', kind='equity', fund_id='fund', isin='ZZ2222222222'):
    frame = pd.DataFrame([dict(constituent_id=asset, name='Invented Company', isin='ZZ1111111111',
                               ticker='SYN-A', instrument_type=kind, country=country, weight=.75)])
    return FundSnapshot(fund_id, 'Invented Fund', isin, ('SYN-F',), date(2026, 1, 1),
                        'https://example.invalid/synthetic', frame)


@pytest.mark.parametrize(('raw', 'expected'), [
    ('DEU', ('Europe', 'Germany')), ('United Kingdom', ('Europe', 'United Kingdom')),
    ('UK', ('Europe', 'United Kingdom')), ('US', ('United States', 'United States')),
    ('Taiwan', ('Asia', 'Taiwan')), ('Hong Kong', ('Asia', 'Hong Kong')),
    ('Korea (South)', ('Asia', 'South Korea')),
    ('MX', ('Latin America & Caribbean', 'Mexico')), ('Canada', ('Other North America', 'Canada')),
    ('Europe', ('Europe', 'Country unspecified')), ('Gold', ('Gold',)),
    ('Invented territory', (UNKNOWN,)),
])
def test_country_and_region_normalization(raw, expected):
    assert geography_path((raw,)) == expected
    assert geography_path(('Ignored legacy parent', raw)) == expected


def test_country_mapping_is_packaged_and_complete():
    lookup = country_lookup()
    assert len({key for key in lookup if len(key) == 3}) >= 249
    assert lookup['gbr'] == lookup['gb'] == lookup['uk']


def test_direct_and_indirect_country_and_residual_conserve_value():
    held, funds = holdings(), [fund()]
    geography = resolve_geography(held, funds, {})
    exposures = expand_etfs(normalize_exposures(held), funds, held)
    rows = geography_allocations(exposures, geography)
    assert rows.value.sum() == 300
    assert rows.loc[rows.asset_id.eq('company'), 'value'].sum() == 250
    for level in ['Regions', 'Countries']:
        table = geography_table(rows, level=level, denominator=300, complete=True)
        assert table['EUR value'].sum() == 300
        assert table['% of selected portfolio'].sum() == pytest.approx(100)
        assert table.set_index('Category').loc[UNKNOWN, 'EUR value'] == 50
    assert geography.sources['company'].startswith('Invented Fund')
    whole = geography_allocations(normalize_exposures(held), geography)
    assert geography_table(whole, denominator=300, complete=True).set_index('Category').loc[UNKNOWN, 'EUR value'] == 200


def test_manual_override_deduplicates_aliases_and_preserves_other_taxonomies():
    held = holdings()
    manual = {'company': {'labels': (('Invented AI', 'Compute'),),
                           'geography': (('Europe', 'UK'), ('GB',), ('US',))}}
    before = repr(manual)
    geography = resolve_geography(held, [fund()], manual)
    assert geography.paths['company'] == (('Europe', 'United Kingdom'), ('United States', 'United States'))
    assert geography.sources['company'] == 'Local geography classification'
    assert repr(manual) == before
    rows = geography_allocations(normalize_exposures(held.iloc[:1]), geography)
    assert rows.value.tolist() == [50, 50]
    assert geography_table(rows, root=('Europe',), level='Countries', denominator=100, complete=True)['EUR value'].tolist() == [50]


def test_provider_conflicts_do_not_depend_on_source_order():
    held = holdings()
    first, second = fund('Germany'), fund('UK', fund_id='other', isin='ZZ3333333333')
    for funds in [[first, second], [second, first]]:
        geo = resolve_geography(held, funds, {})
        assert geo.paths['company'] == ((UNKNOWN,),)
        assert 'Conflicting' in geo.reasons['company']
        assert resolve_geography(held, funds, {'company': {'geography': (('US',),)}}).paths['company'] == (
            ('United States', 'United States'),)
    assert resolve_geography(held, [first, fund('DEU')], {}).paths['company'] == (('Europe', 'Germany'),)
    assert resolve_geography(held, [first, fund('Unknown')], {}).paths['company'] == (('Europe', 'Germany'),)


def test_non_geographic_assets_and_no_inference_from_currency_names_or_isin_prefix():
    held = pd.DataFrame([
        dict(id=asset, name=name, ticker=ticker, isin=isin, instrument_type=kind)
        for asset, name, ticker, isin, kind in [
            ('crypto', 'Invented token', 'TOKEN-EUR', '', 'crypto'),
            ('cash', 'Invented cash', '', '', 'cash'),
            ('physical', 'Gold sounding name', '', '', 'physical'),
            ('gold', 'Invented bar', '', '', 'physical'),
            ('etc', 'Invented ETC', '', '', 'etc'),
            ('verified', 'Instrument with public gold identifier', '', 'DE000EWG2LD7', 'etc'),
            ('miner', 'Gold Mining Synthetic', 'SYN.DE', 'US0000000000', 'equity'),
        ]
    ])
    geo = resolve_geography(held, [], {'gold': {'asset_class': (('Commodities', 'Gold'),)}})
    for asset, expected in [('crypto', 'Crypto'), ('cash', 'Cash'), ('gold', 'Gold'), ('verified', 'Gold'),
                            ('physical', UNKNOWN), ('etc', UNKNOWN), ('miner', UNKNOWN)]:
        assert geo.paths[asset] == ((expected,),)


def test_constituent_type_never_inherits_parent_and_fund_domicile_is_not_exposure():
    held = holdings()
    held.loc[held.id.eq('fund'), 'instrument_type'] = 'cash'  # Parent metadata must never reach the equity.
    geo = resolve_geography(held, [fund()], {})
    assert geo.paths['company'] == (('Europe', 'Germany'),)
    nested = fund('Ireland', kind='etf')
    held = holdings().iloc[1:]
    assert resolve_geography(held, [nested], {}).paths['constituent'] == ((UNKNOWN,),)
    cash = fund('US', kind='cash')
    assert resolve_geography(held, [cash], {}).paths['constituent'] == (('Cash',),)


def test_missing_values_region_only_and_search_denominator():
    held = holdings()
    held.loc[0, 'current_value_eur'] = float('nan')
    # Complete exposure rows include unpriced positions for visibility.
    exposures = pd.DataFrame([dict(asset_id='company', asset_name='Invented Company', value=float('nan')),
                              dict(asset_id='fund', asset_name='Invented Fund', value=200.)])
    geo = resolve_geography(held, [], {'company': {'geography': (('UK',),)}, 'fund': {'geography': (('Europe',),)}})
    rows = geography_allocations(exposures, geo)
    table = geography_table(rows, level='Countries', denominator=200, complete=False).set_index('Category')
    assert pd.isna(table.loc['United Kingdom', 'EUR value'])
    assert table.loc['United Kingdom', 'Missing valuations'] == 1
    assert table.loc['Europe / Country unspecified', 'EUR value'] == 200
    assert table['% of selected portfolio'].isna().all()
    searched = geography_table(rows.loc[rows.asset_id.eq('fund')], denominator=800, complete=True)
    assert searched['% of selected portfolio'].tolist() == [25]


def test_company_merge_and_undo_follow_same_geography_identity():
    held = holdings()
    snapshot = fund()
    # A distinct provider identity with a reviewed company equivalence.
    snapshot.constituents.loc[0, ['isin', 'ticker']] = ['', '']
    identities = {'security:ZZ1111111111': 'invented-company', 'instrument:constituent': 'invented-company'}
    plan = build_plan(held, [snapshot], identities, MergeSettings(set()), {})
    linked, funds, classes = plan.apply(held, [snapshot], {})
    geo = resolve_geography(linked, funds, classes)
    assert geo.paths['company'] == (('Europe', 'Germany'),)
    for group in plan.groups:
        group.enabled = False
    linked, funds, classes = plan.apply(held, [snapshot], {})
    geo = resolve_geography(linked, funds, classes)
    assert geo.paths['company'] == ((UNKNOWN,),)
    assert any(key.startswith('separate:') and paths == (('Europe', 'Germany'),) for key, paths in geo.paths.items())


def test_geography_does_not_change_ai_membership_values_targets_or_percentages():
    held, funds = holdings(), [fund()]
    manual = {'company': {'labels': (('Invented AI', 'Compute'),)}}
    labels = [Label('labels', ('Invented AI',))]
    enriched = fund_classifications(manual, funds, held)
    geo = resolve_geography(held, funds, manual)
    for asset, entry in geo.classifications(enriched).items():
        enriched[asset] = {**enriched[asset], **entry}
    current = expand_etfs(normalize_exposures(held), funds, held)
    # Same pipeline for current exposure and synthetic target measures.
    for exposures in [current, current.assign(value=current.value * 1.5), current.assign(value=0.)]:
        old = compare_labels(exposures, manual, labels, overlap='split')
        new = compare_labels(exposures, enriched, labels, overlap='split')
        pd.testing.assert_frame_equal(old.table, new.table)
        pd.testing.assert_frame_equal(old.allocations, new.allocations)


def test_overnight_economic_exposure_is_non_geographic_and_basket_is_excluded():
    from dataclasses import replace
    held = holdings().iloc[[1]].copy()
    rate = pd.DataFrame([dict(constituent_id='invented-rate', name='Invented overnight rate',
                             ticker='', isin='', weight=1., instrument_type='overnight_rate', country='LU')])
    snapshot = replace(fund(), constituents=rate, asset_class='money_market', breakdown_basis='economic',
                       basket=fund('Germany').constituents)
    geography = resolve_geography(held, [snapshot], {})
    assert geography.paths['fund'] == (('Money market',),)
    assert geography.paths['invented-rate'] == (('Money market',),)
    assert 'constituent' not in geography.paths
    assert resolve_geography(held, [snapshot], {'fund': {'geography': (('Cash',),)}}).paths['fund'] == (('Cash',),)
    # An ordinary money-market-labelled fund without verified economic holdings
    # does not establish a country or overnight-rate exposure.
    ordinary = replace(snapshot, breakdown_basis='holdings')
    assert resolve_geography(held, [ordinary], {}).paths['fund'] == ((UNKNOWN,),)
    partial = replace(snapshot, constituents=rate.assign(weight=.5))
    assert resolve_geography(held, [partial], {}).paths['fund'] == ((UNKNOWN,),)
