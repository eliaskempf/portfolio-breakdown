"""Invented issuers only: cross-fund grouping, uncertainty and persistent undo."""
from dataclasses import replace
from datetime import date

import pandas as pd
import pytest

from portfolio_app.exposure.company_merges import MergeSettings, build_plan, load_company_names, load_settings, save_settings
from portfolio_app.funds.etf import FundSnapshot, expand_etfs, effective_exposure_table, fund_classifications
from portfolio_app.exposure.exposures import normalize_exposures
from portfolio_app.portfolio.holdings import DataError, parse_holdings
from portfolio_app.exposure.stock_exposure import stock_exposure
from portfolio_app.portfolio.targets import target_exposures


def universe(names=('Invented Photon NV', 'INVENTED PHOTON'), isins=('ZZ1111111111', ''), third=False):
    holdings = parse_holdings('id,name,ticker,isin,shares,instrument_type,target_allocation\n'
                             'fund-a,Invented Blue Fund,BLUE,ZZ2222222222,1,etf,0.4\n'
                             'fund-b,Invented Green Fund,GREEN,ZZ3333333333,1,etf,0.6\n')
    holdings['current_value_reporting'] = [80., 120.]
    funds = []
    for i, row in enumerate(holdings.to_dict('records')):
        constituents = pd.DataFrame([{'constituent_id': f'provider-{i}', 'name': names[i], 'isin': isins[i],
                                     'ticker': '', 'weight': .75, 'instrument_type': 'equity'}])
        funds.append(FundSnapshot(row['id'], row['name'], row['isin'], (row['ticker'],), date(2026, 1, 1),
                                  'https://example.invalid/invented', constituents, equity_fund=True))
    if third:
        direct = holdings.iloc[[0]].copy()
        direct['id'], direct['name'], direct['ticker'], direct['isin'], direct['instrument_type'] = 'direct', 'Invented Photon', 'PHOTON', isins[0], 'equity'
        direct['position_id'], direct['current_value_reporting'], direct['target_allocation'] = 'direct-pos', 50., 0.
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
    assert len(company) == 1 and company['Total'].iloc[0] == 150.
    assert company.Asset.iloc[0].endswith(' *') == (kind == 'estimated')
    assert merged['Total'].sum() == 200.
    assert snapshots[1].constituents['isin'].tolist() == funds[1].constituents['isin'].tolist()
    assert labels[group.asset_id]['theme'] == (('Invented', 'Devices'),)
    targets = target_exposures(linked, snapshots, lookthrough=True, holdings=linked)
    assert targets.known.loc[targets.known.asset_id == group.asset_id, 'value'].sum() == pytest.approx(.75)
    stocks = stock_exposure(linked, snapshots)
    assert stocks.companies['Total'].tolist() == [150.]
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
    assert totals(linked, snapshots)['Total'].sum() == 250.


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
        assert stock_exposure(linked, funds).companies['Total'].tolist() == [150.]
        disabled.update(m['node'] for m in plan.groups[0].members)
        assert not build_plan(rows, snapshots, reviewed, MergeSettings(disabled)).groups[0].enabled


def test_inventory_formats_source_labels_once_per_held_fund(monkeypatch):
    from portfolio_app.exposure import company_merges
    holdings, funds = universe()
    frames = []
    for fund in funds:
        constituents = pd.concat([fund.constituents.assign(constituent_id=f'row-{i}') for i in range(20)])
        frames.append(replace(fund, constituents=constituents))
    original = company_merges.display_name
    labels = []
    def counted(name):
        labels.append(name)
        return original(name)
    monkeypatch.setattr(company_merges, 'display_name', counted)
    items = company_merges.inventory(holdings, frames, {})
    assert len(items) == 40
    assert labels == [fund.name for fund in funds]


def test_each_estimated_name_checks_only_its_identity_components(monkeypatch):
    from portfolio_app.exposure import company_merges
    holdings, funds = universe()
    count = 150
    snapshots = []
    for index, fund in enumerate(funds):
        rows = pd.DataFrame([dict(constituent_id=f'{index}-{n}', name=f'Invented issuer {n}',
                                  isin='', ticker='', weight=1/count, instrument_type='equity')
                             for n in range(count)])
        snapshots.append(replace(fund, constituents=rows))
    # Count full-universe index scans. Looking up every other issuer for each
    # estimated name makes a pair of broad funds quadratic.
    full_scans = []
    def observed_range(*args):
        if args == (2 * count,):
            full_scans.append(1)
        return range(*args)
    monkeypatch.setattr(company_merges, 'range', observed_range, raising=False)
    plan = build_plan(holdings, snapshots, {}, MergeSettings(set()))
    assert len(plan.groups) == count
    assert all(g.basis == 'Estimated name match' and len(g.members) == 2 for g in plan.groups)
    assert len(full_scans) <= 3


def test_aliased_component_retains_its_origin_and_estimated_status(monkeypatch):
    from portfolio_app.exposure import company_merges
    # The exact security Y has two names and two origins. Its alias in C must
    # prevent merging the second, different security from C through that name.
    records = [('A', 'X', 'Invented north'), ('B', 'Y', 'Invented north'),
               ('C', 'Y', 'Invented south'), ('C', 'Z', 'Invented south')]
    items = [dict(node=f'n{i}', asset_id=identity, isin=identity, name=name, origin=origin,
                  source=origin, fund=origin, date='', ticker='')
             for i, (origin, identity, name) in enumerate(records)]
    monkeypatch.setattr(company_merges, 'inventory', lambda *args: items)
    plan = build_plan(pd.DataFrame(), [], {}, MergeSettings(set()))
    assert len(plan.groups) == 1
    assert plan.groups[0].basis == 'Estimated name match'
    assert [m['node'] for m in plan.groups[0].members] == ['n0', 'n1', 'n2']


def test_merge_annotations_use_source_local_ids_and_preserve_provider_columns(monkeypatch):
    from portfolio_app.exposure import company_merges

    holdings, funds = universe()
    for i, fund in enumerate(funds):
        frame = fund.constituents.assign(constituent_id='vendor:shared', isin='ZZ1111111111',
                                         provider_note='Invented metadata', analysis_asset_id='old')
        unrelated = frame.assign(constituent_id='vendor:bond', instrument_type='bond', weight=.1)
        funds[i] = replace(fund, constituents=pd.concat([frame, unrelated]))
    funds.append(replace(funds[0], isin='ZZ9999999999'))

    read_records = company_merges.constituent_records
    def projected(frame):
        assert 'provider_note' not in frame
        return read_records(frame)
    monkeypatch.setattr(company_merges, 'constituent_records', projected)
    # Walking whole rows would extract every provider field merely to look up an ID.
    original_tuples = pd.DataFrame.itertuples
    def check_columns(frame, *args, **kwargs):
        assert 'provider_note' not in frame
        return original_tuples(frame, *args, **kwargs)
    monkeypatch.setattr(pd.DataFrame, 'itertuples', check_columns)
    plan = build_plan(holdings, funds, {}, MergeSettings({'fund:ZZ2222222222:vendor:shared'}))
    assert len(plan.groups) == 1 and not plan.groups[0].enabled
    _, linked, _ = plan.apply(holdings, funds, {})
    for i, fund in enumerate(linked):
        expected = f'separate:fund:{fund.isin}:vendor:shared' if i < 2 else ''
        assert fund.constituents.analysis_asset_id.tolist() == [expected, '']
        assert fund.constituents.index.tolist() == [0, 0]
        assert fund.constituents.provider_note.tolist() == ['Invented metadata'] * 2
        fund.constituents['provider_note'] = 'Edited copy'
        assert funds[i].constituents.provider_note.tolist() == ['Invented metadata'] * 2
        assert funds[i].constituents.analysis_asset_id.tolist() == ['old'] * 2
