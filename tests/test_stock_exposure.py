"""Invented fund weights and balances for coverage and provenance checks."""
from datetime import date
from dataclasses import replace

import pandas as pd
import pytest

from portfolio_app.etf import FundSnapshot, resolve_constituent_asset
from portfolio_app.holdings import DataError, parse_holdings
from portfolio_app.stock_exposure import stock_exposure


def sources():
    frame = parse_holdings('id,name,shares,isin,ticker,bucket_id,instrument_type\ndirect,Invented company,1,TEST-STOCK-1,EXAMPLE,active,equity\nfund1,Invented broad fund,1,TEST-FUND-01,FUND1,core,etf\nfund2,Invented narrow fund,1,TEST-FUND-02,FUND2,active,etf\nmetal,Invented metal,1,,,reserve,physical\ntoken,Invented token,1,,TOKEN-EUR,reserve,crypto\n')
    frame['current_value_eur'] = [100., 200., 100., 40., 60.]
    return frame


def funds():
    constituents = pd.DataFrame([{'constituent_id': 'company', 'name': 'Invented company', 'isin': 'TEST-STOCK-1', 'ticker': 'EXAMPLE', 'weight': .25}])
    return [FundSnapshot('fund1', 'Invented broad fund', 'TEST-FUND-01', ('FUND1',), date(2026, 1, 1), 'Synthetic', constituents, equity_fund=True),
            FundSnapshot('fund2', 'Invented narrow fund', 'TEST-FUND-02', ('FUND2',), date(2026, 1, 1), 'Synthetic', constituents, equity_fund=True)]


def test_source_exclusion_preserves_core_company_and_global_denominator():
    all_sources = sources()
    result = stock_exposure(all_sources, funds())
    assert result.companies['Total (EUR)'].tolist() == [175.]
    assert result.stock_value == 400.
    assert result.unresolved['EUR value'].sum() == 225.
    assert result.sources['Source position'].nunique() == 3
    without = stock_exposure(all_sources, funds(), excluded_buckets=['active'])
    row = without.companies.iloc[0]
    assert row['Total (EUR)'] == 50.
    assert row['Direct (EUR)'] == 0.
    assert row['Selected stock-universe %'] == 25.
    assert row['Whole-portfolio %'] == 10.
    assert without.whole_value == 500.
    assert without.unresolved['EUR value'].sum() == 150.
    assert all_sources.current_value_eur.sum() == 500.


def test_unknown_composition_and_missing_values_do_not_become_zero():
    snapshots = funds()
    snapshots[0] = replace(snapshots[0], equity_fund=False)
    result = stock_exposure(sources(), snapshots)
    assert result.stock_value is None
    assert result.companies['Selected stock-universe %'].isna().all()
    assert 'Unknown composition' in set(result.unresolved.Status)
    unsupported = stock_exposure(sources(), [])
    assert unsupported.stock_value is None
    assert unsupported.unresolved['EUR value'].sum() == 300.
    rows = sources()
    rows.loc[0, 'current_value_eur'] = float('nan')
    missing = stock_exposure(rows, funds())
    assert missing.whole_value is None
    assert missing.companies['Whole-portfolio %'].isna().all()


def test_explicit_company_identity_and_conflicting_listing_are_distinct():
    rows = sources()
    rows.loc[0, 'isin'] = 'TEST-ADR-001'
    separate = stock_exposure(rows, funds())
    assert len(separate.companies) == 2
    merged = stock_exposure(rows, funds(), identities={'security:TEST-ADR-001': 'invented-company', 'security:TEST-STOCK-1': 'invented-company'})
    assert merged.companies['Total (EUR)'].tolist() == [175.]
    constituent = funds()[0].constituents.iloc[0].to_dict()
    assert resolve_constituent_asset(constituent, rows)[0] == 'company'
    constituent['constituent_id'] = 'direct'
    with pytest.raises(DataError, match='conflicts'):
        resolve_constituent_asset(constituent, rows)


def test_known_non_equity_component_is_excluded_from_stock_denominator():
    fund = funds()[0]
    constituents = fund.constituents.copy()
    constituents['weight'] = .8
    constituents['instrument_type'] = 'equity'
    constituents = pd.concat([constituents, pd.DataFrame([{'constituent_id': 'cash', 'name': 'Cash component', 'ticker': '', 'isin': '', 'weight': .2, 'instrument_type': 'cash'}])])
    result = stock_exposure(sources(), [replace(fund, constituents=constituents), funds()[1]])
    assert result.stock_value == 360.
    assert result.companies['Total (EUR)'].sum() == 285.


def test_zero_value_unknown_positions_do_not_reduce_coverage():
    rows = sources()
    rows.loc[0, 'instrument_type'] = 'unknown'
    rows.loc[0, 'current_value_eur'] = 0.
    rows.loc[0, 'shares'] = 0.
    result = stock_exposure(rows, funds())
    assert result.stock_value == 300.


def test_declared_non_equity_fund_and_unresolved_equity_are_distinct():
    rows = sources()
    rows['exposure_kind'] = ['equity', 'equity', 'non_equity', 'non_equity', 'non_equity']
    result = stock_exposure(rows, [])
    assert result.stock_value == 300.
    assert result.unresolved['EUR value'].sum() == 200.
    assert result.whole_value == 500.
    assert set(result.unresolved.Status) == {'Unresolved equity'}


def test_other_instrument_type_remains_unresolved():
    rows = sources()
    rows.loc[0, 'instrument_type'] = 'other'
    result = stock_exposure(rows, funds())
    assert result.stock_value is None
    assert result.unresolved['EUR value'].sum() == 325.
