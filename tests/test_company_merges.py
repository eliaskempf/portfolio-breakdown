"""Invented issuers only: cross-fund grouping, uncertainty and persistent undo."""
from dataclasses import replace
from datetime import date

import pandas as pd
import pytest

from portfolio_app.company_merges import MergeSettings, build_plan, load_company_names, load_settings, save_settings
from portfolio_app.etf import FundSnapshot, expand_etfs, effective_exposure_table, fund_classifications
from portfolio_app.exposures import normalize_exposures
from portfolio_app.holdings import DataError, parse_holdings
from portfolio_app.stock_exposure import stock_exposure
from portfolio_app.targets import target_exposures


def universe(names=('Invented Photon NV', 'INVENTED PHOTON'), isins=('ZZ1111111111', ''), third=False):
    holdings = parse_holdings('id,name,ticker,isin,shares,instrument_type,target_allocation\n'
                             'fund-a,Invented Blue Fund,BLUE,ZZ2222222222,1,etf,0.4\n'
                             'fund-b,Invented Green Fund,GREEN,ZZ3333333333,1,etf,0.6\n')
    holdings['current_value_eur'] = [80., 120.]
    funds = []
    for i, row in enumerate(holdings.to_dict('records')):
        constituents = pd.DataFrame([{'constituent_id': f'provider-{i}', 'name': names[i], 'isin': isins[i],
                                     'ticker': '', 'weight': .75, 'instrument_type': 'equity'}])
        funds.append(FundSnapshot(row['id'], row['name'], row['isin'], (row['ticker'],), date(2026, 1, 1),
                                  'https://example.invalid/invented', constituents, equity_fund=True))
    if third:
        direct = holdings.iloc[[0]].copy()
        direct['id'], direct['name'], direct['ticker'], direct['isin'], direct['instrument_type'] = 'direct', 'Invented Photon', 'PHOTON', isins[0], 'equity'
        direct['position_id'], direct['current_value_eur'], direct['target_allocation'] = 'direct-pos', 50., 0.
        holdings = pd.concat([holdings, direct], ignore_index=True)
    return holdings, funds


def totals(holdings, funds):
    return effective_exposure_table(expand_etfs(normalize_exposures(holdings), funds, holdings))


@pytest.mark.parametrize('kind', ['exact', 'reviewed', 'estimated'])
def test_cross_fund_merge_without_direct_position_conserves_exposure_and_targets(kind):
    rows, funds = universe(isins=('ZZ1111111111', 'ZZ1111111111' if kind == 'exact' else ''))
    reviewed = {'security:ZZ1111111111': 'invented-issuer', 'instrument:provider-1': 'invented-issuer'} if kind == 'reviewed' else {}
    plan = build_plan(rows, funds, reviewed, MergeSettings(set()), {'provider-0': {'theme': (('Invented', 'Devices'),)}})
    assert len(plan.groups) == 1
    group = plan.groups[0]
    assert group.basis == {'exact': 'Same security identity', 'reviewed': 'Reviewed company mapping', 'estimated': 'Estimated name match'}[kind]
    linked, snapshots, labels = plan.apply(rows, funds, {'provider-0': {'theme': (('Invented', 'Devices'),)}})
    merged = totals(linked, snapshots)
    company = merged.loc[~merged.Asset.str.contains('/ Other')]
    assert len(company) == 1 and company['Total (EUR)'].iloc[0] == 150.
    assert company.Asset.iloc[0].endswith(' *') == (kind == 'estimated')
    assert merged['Total (EUR)'].sum() == 200.
    assert snapshots[1].constituents['isin'].tolist() == funds[1].constituents['isin'].tolist()
    assert labels[group.asset_id]['theme'] == (('Invented', 'Devices'),)
    targets = target_exposures(linked, snapshots, lookthrough=True, holdings=linked)
    assert targets.known.loc[targets.known.asset_id == group.asset_id, 'value'].sum() == pytest.approx(.75)
    stocks = stock_exposure(linked, snapshots)
    assert stocks.companies['Total (EUR)'].tolist() == [150.]
    assert stocks.sources['Source instrument'].nunique() == 2
    assert 'analysis_asset_id' not in rows and 'analysis_asset_id' not in funds[0].constituents


def test_undo_persists_across_reload_changed_members_and_refresh(tmp_path):
    rows, funds = universe()
    plan = build_plan(rows, funds, {}, MergeSettings(set()))
    settings = MergeSettings({m['node'] for m in plan.groups[0].members})
    path = tmp_path / 'company-merges.yaml'
    save_settings(path, settings)
    settings = load_settings(path)
    plan = build_plan(rows, funds, {}, settings)
    assert not plan.groups[0].enabled
    linked, snapshots, labels = plan.apply(rows, funds, {'provider-0': {'sector': (('Own sector',),)}})
    assert len(totals(linked, snapshots).loc[lambda x: ~x.Asset.str.contains('/ Other')]) == 2
    assert len(stock_exposure(linked, snapshots).companies) == 2
    classified = fund_classifications(labels, snapshots, linked)
    separate = snapshots[0].constituents.analysis_asset_id.iloc[0]
    assert classified[separate]['sector'] == (('Own sector',),)
    # A fresh export and a newly added direct position do not re-enable a split.
    with_direct, fresh = universe(third=True)
    assert not build_plan(with_direct, fresh, {}, settings).groups[0].enabled
    save_settings(path, replace(settings, disabled=set()))
    assert build_plan(rows, funds, {}, load_settings(path)).groups[0].enabled
    assert list((tmp_path / '.backups').iterdir())
    with pytest.raises(DataError, match='changed'):
        save_settings(path, settings)


def test_exact_identity_can_be_split_even_in_stock_view():
    rows, funds = universe(isins=('ZZ1111111111', 'ZZ1111111111'), third=True)
    plan = build_plan(rows, funds, {}, MergeSettings(set()))
    disabled = {m['node'] for m in plan.groups[0].members}
    plan = build_plan(rows, funds, {}, MergeSettings(disabled))
    linked, snapshots, _ = plan.apply(rows, funds, {})
    assert len(stock_exposure(linked, snapshots).companies) == 3
    assert totals(linked, snapshots)['Total (EUR)'].sum() == 250.


@pytest.mark.parametrize('names', [
    ('Invented Photon', 'Invented Photon India'),
    ('Invented Photon Class A', 'Invented Photon Class B'),
    ('Invented Photon', 'Invented Photonics'),
])
def test_no_substring_subsidiary_or_share_class_guess(names):
    rows, funds = universe(names=names)
    assert not build_plan(rows, funds, {}, MergeSettings(set())).groups


def test_conflicting_reviews_and_ambiguous_rows_block_name_estimates():
    rows, funds = universe()
    reviewed = {'security:ZZ1111111111': 'issuer-one', 'instrument:provider-1': 'issuer-two'}
    assert not build_plan(rows, funds, reviewed, MergeSettings(set())).groups
    duplicate = funds[0].constituents.copy()
    duplicate['constituent_id'], duplicate['isin'], duplicate['weight'] = 'second-share', 'ZZ4444444444', .1
    funds[0] = replace(funds[0], constituents=pd.concat([funds[0].constituents, duplicate]))
    assert not build_plan(rows, funds, {}, MergeSettings(set())).groups


def test_canonical_identity_survives_fund_order_and_exclusions():
    rows, funds = universe()
    labels = {'provider-1': {'theme': (('Local classification',),)}}
    plan = build_plan(rows, funds, {}, MergeSettings(set()), labels)
    reverse = build_plan(rows, list(reversed(funds)), {}, MergeSettings(set()), labels)
    assert plan.groups[0].asset_id == reverse.groups[0].asset_id == 'provider-1'
    linked, snapshots, _ = plan.apply(rows, funds, labels)
    selected = linked.iloc[[1]]
    expanded = expand_etfs(normalize_exposures(selected), snapshots, linked)
    assert expanded.loc[expanded.asset_id == 'provider-1', 'value'].sum() == 90.
    assert expanded.value.sum() == 120.


def test_cash_never_participates_and_invalid_preferences_fail(tmp_path):
    rows, funds = universe()
    funds[1].constituents['instrument_type'] = 'cash'
    assert not build_plan(rows, funds, {}, MergeSettings(set())).groups
    path = tmp_path / 'company-merges.yaml'
    path.write_text('disabled: definitely-not-a-list\n')
    with pytest.raises(DataError):
        load_settings(path)


def test_reviewed_issuer_name_does_not_label_whole_company_as_one_share_class(tmp_path):
    rows, funds = universe(names=('Invented Photon Preferred', 'Invented Photon Ordinary'))
    reviewed = {'security:ZZ1111111111': 'invented-issuer', 'instrument:provider-1': 'invented-issuer'}
    path = tmp_path / 'company-names.yaml'
    assert load_company_names(path) == {}
    path.write_text('invented-issuer: Invented Photon\n')
    plan = build_plan(rows, funds, reviewed, MergeSettings(set()), company_names=load_company_names(path))
    assert plan.groups[0].name == 'Invented Photon'
    assert {m['name'] for m in plan.groups[0].members} == {'Invented Photon Preferred', 'Invented Photon Ordinary'}
    disabled = {m['node'] for m in plan.groups[0].members}
    split = build_plan(rows, funds, reviewed, MergeSettings(disabled), company_names=load_company_names(path))
    linked, snapshots, _ = split.apply(rows, funds, {})
    assert set(stock_exposure(linked, snapshots).companies.Company) == {'Invented Photon Preferred', 'Invented Photon Ordinary'}
    path.write_text('invented-issuer: []\n')
    with pytest.raises(DataError, match='company names'):
        load_company_names(path)


def test_reviewed_links_survive_identifier_enrichment_and_plain_provider_refresh():
    rows, original = universe()
    reviewed = {'security:ZZ1111111111': 'invented-issuer', 'instrument:provider-1': 'invented-issuer'}
    enriched = [replace(f, constituents=f.constituents.copy()) for f in original]
    enriched[1].constituents['isin'] = 'ZZ1111111111'
    disabled = set()
    for snapshots in [original, enriched, original]:
        plan = build_plan(rows, snapshots, reviewed, MergeSettings(set()))
        assert plan.groups[0].basis == 'Reviewed company mapping'
        linked, funds, _ = plan.apply(rows, snapshots, {})
        assert stock_exposure(linked, funds).companies['Total (EUR)'].tolist() == [150.]
        disabled.update(m['node'] for m in plan.groups[0].members)
        assert not build_plan(rows, snapshots, reviewed, MergeSettings(disabled)).groups[0].enabled
