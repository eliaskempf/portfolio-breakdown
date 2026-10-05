"""Synthetic currency fixtures; never access live providers or working data."""
from datetime import datetime, timezone
from decimal import Decimal
import json

import pandas as pd
import pytest

from portfolio_app.cost_basis import (FIELD, active_components, aggregate_component, component, encode_components,
    estimate_missing, freeze_historical, resolve_cost, supplied_conversion)
from portfolio_app.fx import current_rate, HistoricalFX
from portfolio_app.holdings import DataError, metadata_dimensions, parse_holdings
from portfolio_app.portfolio import prepare_portfolio
from portfolio_app.portfolio_settings import load_settings, save_settings
from portfolio_app.positions import read_snapshot, save_position
from portfolio_app.prices import PriceService, Quote
from portfolio_app.purchases import save_purchase_batch

NOW = datetime(2026, 9, 7, 12, tzinfo=timezone.utc)


class InventedPrices:
    def price(self, ticker):
        return Quote(120, 'USD', NOW)
    def fx(self, currency):
        return Quote({'USD': .8, 'GBP': 1.2}[currency], 'EUR', NOW)


class InventedHistory:
    def fetch(self, currency, start, end):
        return pd.Series([{'USD': .9, 'GBP': 1.25}[currency]], index=pd.to_datetime(['2026-09-04']))


@pytest.fixture
def prices():
    return PriceService(InventedPrices(), now=lambda: NOW)


@pytest.fixture
def historical(tmp_path):
    return HistoricalFX(InventedHistory(), tmp_path / 'history')


def holding(currency='USD', price=100, shares=1):
    return parse_holdings(f'id,name,ticker,shares,acquisition_price,acquisition_currency\ninvented,Invented instrument,SYNTH,{shares},{price},{currency}\n')


@pytest.mark.parametrize('source,target,expected', [('EUR','EUR',1), ('USD','EUR',.8), ('EUR','USD',1.25),
    ('GBP','EUR',1.2), ('EUR','GBP',1/1.2), ('USD','GBP',2/3), ('GBP','USD',1.5), ('USD','USD',1), ('GBP','GBP',1)])
def test_current_pairs(prices, source, target, expected):
    assert current_rate(prices, source, target).quote.price == pytest.approx(expected)


def test_historical_cost_and_current_value_are_independent(prices, historical):
    frame = holding()
    row = frame.iloc[0]
    parts = [component(1, 100, 'USD', '2026-09-05')]
    frame[FIELD] = encode_components(parts, row)
    eur = prepare_portfolio(frame, prices, historical=historical)
    assert eur.cost_basis_reporting.iloc[0] == 90
    assert eur.current_value_reporting.iloc[0] == 96
    assert eur.unrealized_gain_reporting.iloc[0] == 6
    usd = prepare_portfolio(frame, prices, historical=historical, reporting_currency='USD')
    assert usd.unrealized_gain_reporting.iloc[0] == 20
    pd.testing.assert_frame_equal(eur[['cost_basis_reporting','unrealized_gain_reporting']], prepare_portfolio(frame, prices, historical=historical)[['cost_basis_reporting','unrealized_gain_reporting']])


def test_unknown_cost_excludes_only_gains(prices):
    result = prepare_portfolio(holding(), prices)
    assert result.current_value_reporting.iloc[0] == 96
    assert pd.isna(result.unrealized_gain_reporting.iloc[0])
    assert result.native_return_pct.iloc[0] == 20
    assert pd.isna(result.return_pct.iloc[0])


def test_weekend_common_day_and_no_future_lookup(historical):
    result = historical.get('USD', 'GBP', '2026-09-06')
    assert result.rate == pytest.approx(.72)
    assert result.observed_on == '2026-09-04'
    assert historical.get('USD', 'EUR', '2026-09-03').rate is None
    assert historical.get('USD', 'EUR', '2026-09-12').rate is None


def test_supplied_conversion_precedence_and_target_binding(historical):
    part = component(1, 100, 'USD', '2026-09-04')
    supplied_conversion(part, 'EUR', rate='0.91')
    assert resolve_cost([part], 'EUR', historical).amount == 91
    supplied_conversion(part, 'EUR', amount='92')
    assert resolve_cost([part], 'EUR', historical).amount == 92
    assert resolve_cost([part], 'GBP', historical).amount == 72
    part['date'] = ''
    assert resolve_cost([part], 'GBP', historical).amount is None


def test_estimate_is_fixed_and_remains_identifiable(prices):
    parts = estimate_missing([component(1, 100, 'USD')], 'EUR', prices)
    first = resolve_cost(parts, 'EUR')
    assert first.amount == 80 and first.estimated
    prices.provider.fx = lambda currency: Quote(.5, 'EUR', NOW)
    assert resolve_cost(parts, 'EUR') == first
    assert resolve_cost(parts, 'GBP').amount is None
    assert parts[0]['amount'] == '100'


@pytest.mark.parametrize('amount,currency', [(None,'USD'), (100,'')])
def test_unknown_original_cannot_be_estimated(prices, amount, currency):
    with pytest.raises(DataError):
        estimate_missing([component(1, amount, currency)], 'EUR', prices)


def test_freeze_historical_conversion_survives_provider_changes(historical):
    parts = freeze_historical([component(1, 100, 'USD', '2026-09-04')], 'EUR', historical)
    assert parts[0]['conversions']['EUR']['observed_on'] == '2026-09-04'
    assert resolve_cost(parts, 'EUR').amount == 90


def test_mixed_batches_and_fees_preserve_original_costs(tmp_path, historical, prices):
    path = tmp_path / 'holdings.csv'
    save_purchase_batch(path, dict(name='Invented', ticker='SYNTH'), [dict(shares='1',price='100',fees='2',date='2026-09-04')],
        currency='USD', expected_revision=None, historical=historical)
    before = read_snapshot(path)
    save_purchase_batch(path, {}, [dict(shares='1',price='80',fees='1',date='2026-09-04',fx_rate='1.25')],
        currency='GBP', expected_revision=before.revision, position_id=before.holdings.position_id.iloc[0], historical=historical)
    frame = read_snapshot(path).holdings
    parts = active_components(frame.iloc[0])
    assert [(p['amount'],p['currency']) for p in parts] == [('102','USD'), ('81','GBP')]
    assert frame.shares.iloc[0] == 2
    assert pd.isna(frame.acquisition_price.iloc[0])
    result = prepare_portfolio(frame, prices)
    assert result.cost_basis_reporting.iloc[0] == pytest.approx(91.8 + 101.25)
    assert result.unrealized_gain_reporting.iloc[0] == pytest.approx(192 - 193.05)


def test_metadata_roundtrip_and_cost_replacement(tmp_path):
    path = tmp_path / 'holdings.csv'
    fields = dict(name='Invented', shares='2', acquisition_price='10', acquisition_currency='USD')
    parts = [component(2, 20, 'USD', '2026-09-04')]
    fields[FIELD] = encode_components(parts, fields)
    save_position(path, fields, expected_revision=None)
    snap = read_snapshot(path)
    save_position(path, {'short_name':'Example'}, expected_revision=snap.revision, position_id=snap.holdings.position_id.iloc[0])
    snap = read_snapshot(path)
    assert active_components(snap.holdings.iloc[0]) == parts
    save_position(path, {'shares':'1'}, expected_revision=snap.revision, position_id=snap.holdings.position_id.iloc[0])
    row = read_snapshot(path).holdings.iloc[0]
    assert Decimal(active_components(row)[0]['amount']) == 10
    assert active_components(row)[0]['date'] == ''
    assert FIELD not in metadata_dimensions(read_snapshot(path).holdings)


def test_fractional_csv_roundtrip_preserves_conversions_and_detects_edits(tmp_path, prices):
    from portfolio_app.balances import patch_holdings
    path = tmp_path / 'holdings.csv'
    # An invented fractional quantity has more digits on disk than the
    # numeric holdings parser retains. Saving FX must preserve the original.
    quantity = '449.63269999999994'
    fields = dict(name='Invented fractional holding', shares=quantity,
                  acquisition_price='87.4', acquisition_currency='EUR')
    save_position(path, fields, expected_revision=None)
    snap = read_snapshot(path)
    row = snap.holdings.iloc[0]
    parts = estimate_missing(active_components(row), 'USD', prices)
    patch_holdings(path, {row.position_id: {FIELD: encode_components(parts, row)}},
                   expected_revision=snap.revision)
    snap = read_snapshot(path)
    assert resolve_cost(active_components(snap.holdings.iloc[0]), 'USD').estimated
    assert pd.read_csv(path, dtype=str).shares.iloc[0] == quantity
    save_position(path, {'short_name': 'Invented alias'}, expected_revision=snap.revision,
                  position_id=row.position_id)
    snap = read_snapshot(path)
    assert active_components(snap.holdings.iloc[0]) == parts
    # Actual quantity changes must still invalidate incompatible conversions.
    patch_holdings(path, {row.position_id: {'shares': '449.63271'}}, expected_revision=snap.revision)
    assert resolve_cost(active_components(read_snapshot(path).holdings.iloc[0]), 'USD').amount is None


@pytest.mark.parametrize('shares', ['1', '449.63269999999994'])
def test_legacy_batch_reconstructs_only_matching_summary(tmp_path, shares):
    path = tmp_path / 'holdings.csv'
    save_purchase_batch(path, dict(name='Invented'), [dict(shares=shares, price='100', date='2026-09-04')], currency='USD', expected_revision=None)
    row = read_snapshot(path).holdings.iloc[0].to_dict()
    row.pop(FIELD)
    assert active_components(row)[0]['date'] == '2026-09-04'
    row['shares'] = 2
    assert active_components(row)[0]['date'] == ''


def test_settings_default_revision_and_roundtrip(tmp_path):
    assert load_settings(tmp_path).reporting_currency == 'EUR'
    assert not (tmp_path / 'portfolio.yaml').exists()
    save_settings(tmp_path, 'GBP', None)
    settings = load_settings(tmp_path)
    assert settings.reporting_currency == 'GBP'
    with pytest.raises(DataError):
        save_settings(tmp_path, 'USD', None)
    save_settings(tmp_path, 'EUR', settings.revision)
    assert load_settings(tmp_path).reporting_currency == 'EUR'


def test_missing_one_component_excludes_entire_position(prices):
    frame = holding(shares=2)
    parts = [component(1, 100, 'EUR'), component(1, 100, 'USD')]
    frame[FIELD] = encode_components(parts, frame.iloc[0])
    result = prepare_portfolio(frame, prices)
    assert result.current_value_reporting.iloc[0] == 192
    assert pd.isna(result.unrealized_gain_reporting.iloc[0])


@pytest.mark.parametrize('rate', ['0', '-1', 'NaN', 'Infinity'])
def test_invalid_supplied_rates(rate):
    with pytest.raises(DataError):
        supplied_conversion(component(1, 100, 'USD'), 'EUR', rate=rate)


def test_reporting_identity_does_not_depend_on_eur_fx():
    class NoFX(InventedPrices):
        def fx(self, currency):
            raise AssertionError('An identity conversion must not request FX')
    result = prepare_portfolio(holding(), PriceService(NoFX(), now=lambda: NOW), reporting_currency='USD')
    assert result.current_value_reporting.iloc[0] == 120
    assert result.unrealized_gain_reporting.iloc[0] == 20


def test_missing_current_fx_is_unknown_not_zero():
    class NoFX(InventedPrices):
        def fx(self, currency):
            raise ValueError('Synthetic unavailable FX')
    result = prepare_portfolio(holding(), PriceService(NoFX(), now=lambda: NOW))
    assert pd.isna(result.current_value_reporting.iloc[0])
    assert pd.isna(result.unrealized_gain_reporting.iloc[0])


def test_stale_rates_cannot_be_confirmed_as_new_estimates(prices):
    from datetime import timedelta
    current_rate(prices, 'USD', 'EUR')
    prices.now = lambda: NOW + timedelta(days=1)
    def unavailable(currency):
        raise ValueError('Synthetic FX outage')
    prices.provider.fx = unavailable
    with pytest.raises(DataError, match='current FX lookup'):
        estimate_missing([component(1, 100, 'USD')], 'EUR', prices)


def test_corrupt_historical_cache_is_not_a_negative_cost(historical):
    from hashlib import sha256
    key = 'purchase-fx-v1:USD:EUR:2026-09-04'
    historical.cache.directory.mkdir()
    path = historical.cache.directory / (sha256(key.encode()).hexdigest() + '.json')
    path.write_text(json.dumps(dict(data=dict(rate=-1, observed_on='2026-09-04'), attempted_at=NOW.isoformat(), fetched_at=NOW.isoformat())))
    result, due = historical.cached('USD', 'EUR', '2026-09-04')
    assert result.rate is None and due
    assert historical.get('USD', 'EUR', '2026-09-04', refresh=True).rate == .9


def test_future_dates_and_invalid_settings_are_rejected(historical, tmp_path):
    assert historical.get('USD', 'EUR', '2099-01-01').rate is None
    with pytest.raises(DataError):
        save_settings(tmp_path, 'CHF', None)
    (tmp_path / 'portfolio.yaml').write_text('version: 1\nreporting_currency: invalid\n')
    with pytest.raises(DataError):
        load_settings(tmp_path)


def test_cost_component_quantity_must_reconcile():
    from portfolio_app.cost_basis import read_details
    raw = dict(version=1, summary=['2','100','USD'], components=[component(1,100,'USD')])
    with pytest.raises(DataError):
        read_details(json.dumps(raw))


def test_reporting_risk_histories_and_foreign_cash(tmp_path):
    from portfolio_app.risk_data import RiskHistoryService, AdjustedHistory
    dates = pd.date_range('2026-01-01', periods=3)
    class Provider:
        def fetch(self, ticker, years):
            currency, values = {'SYNTH': ('USD',[100,110,120]), 'USDEUR=X': ('EUR',[.8,.9,1.]),
                                'GBPEUR=X': ('EUR',[1.2,1.2,1.2])}[ticker]
            return AdjustedHistory(pd.Series(values,index=dates), currency)
    service = RiskHistoryService(Provider(), tmp_path / 'risk')
    result = service.in_currency('SYNTH', 'GBP')
    assert result.prices.tolist() == pytest.approx([80/1.2,99/1.2,100])
    assert result.currency == 'GBP'
    assert service.cash('USD','GBP').prices.tolist() == pytest.approx([.8/1.2,.9/1.2,1/1.2])
    assert service.cash('EUR','USD').prices.tolist() == pytest.approx([1/.8,1/.9,1])


def test_price_chart_uses_historical_fx_without_changing_native_data():
    from portfolio_app.history import HistoryResult, convert_price_history
    original = HistoryResult(('2026-09-01','2026-09-02'), (100,120), 'USD')
    rates = HistoryResult(original.dates, (.9,.8), 'EUR')
    result = convert_price_history(original, 'EUR', {'USD': rates})
    assert result.prices == (90,96)
    assert original.prices == (100,120) and original.currency == 'USD'
    assert not convert_price_history(original, 'GBP', {'USD':rates}).prices


def test_estimate_status_flows_through_grouped_performance(prices):
    from portfolio_app.performance_allocation import performance_exposures, add_performance_column
    frame = holding()
    parts = estimate_missing(active_components(frame.iloc[0]), 'EUR', prices)
    frame[FIELD] = encode_components(parts, frame.iloc[0])
    result = prepare_portfolio(frame, prices)
    measures = performance_exposures(result, [])
    table = add_performance_column(pd.DataFrame({'Investment':['Invented instrument']}), ['invented'],
                                   list(measures.measures()), key='asset_id')
    assert table['Performance coverage'].iloc[0] == 'Complete · Estimated'
