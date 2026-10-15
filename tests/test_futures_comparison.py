"""Entirely invented rates, margins and paths; no live network or user data."""
from dataclasses import replace
import json

import numpy as np
import pandas as pd
import pytest

from portfolio_app.futures import FuturePosition
from portfolio_app.futures_comparison import ComparisonSettings, cash_growth, evaluate_paths, simulate_comparison
from portfolio_app.futures_reference import parse_references, refresh_references, load_references
from portfolio_app.futures_workspace import (SANDBOX_FILE, load_sandbox, switch_to_fmwn,
    load_comparison, save_comparison)
from portfolio_app.holdings import DataError


def test_shared_financing_proxy_validates_combined_rate():
    from portfolio_app.futures_comparison import financing_from_estr
    assert financing_from_estr(.0125, .0075) == pytest.approx(.02)
    assert financing_from_estr(-.005, 0.) == -.005
    for rate, spread in [(.20, .01), (-.10, -.01), (float('nan'), 0.)]:
        with pytest.raises(DataError, match='Combined'):
            financing_from_estr(rate, spread)
    with pytest.raises(DataError, match='spread'):
        ComparisonSettings(financing_spread=float('nan'))


def test_retired_implied_settings_migrate_without_rewriting_file(tmp_path):
    import json
    from portfolio_app.futures_workspace import COMPARISON_FILE
    path = tmp_path / COMPARISON_FILE
    path.write_text(json.dumps(dict(financing_source='implied', financing_rate=.09)))
    before = path.read_bytes()
    settings, _ = load_comparison(tmp_path)
    assert settings.financing_source == 'ecb'
    assert settings.financing_spread == .005
    assert path.read_bytes() == before
    path.write_text(json.dumps(dict(financing_source='manual', financing_rate=.01)))
    manual, _ = load_comparison(tmp_path)
    assert manual.financing_source == 'manual' and manual.financing_rate == .01


def test_ecb_start_reference_uses_history_when_current_snapshot_is_in_future(tmp_path):
    import json
    from datetime import date
    from portfolio_app.futures_reference import estr_reference_for_start
    from portfolio_app.futures_history_data import HISTORY_FILE
    from test_futures_history import synthetic_history_snapshot
    snapshot = dict(estr_rate=.08, estr_reference_date='2003-10-01')
    start = date(2003, 9, 30)
    assert estr_reference_for_start(tmp_path, snapshot, start) is None
    (tmp_path/HISTORY_FILE).write_text(json.dumps(synthetic_history_snapshot()))
    found = estr_reference_for_start(tmp_path, snapshot, start)
    assert found == dict(estr_rate=0., estr_reference_date='2003-09-30')
    assert estr_reference_for_start(tmp_path, None, date(2003, 10, 15)) is None
    assert estr_reference_for_start(tmp_path, snapshot, date(2003, 10, 1)) == snapshot


def synthetic_reference_html():
    ecb = '<table><tr><td>Rate</td><td>1.25</td></tr><tr><td>Reference date</td><td>02-01-2025</td></tr></table>'
    headers = ['Exchange', 'Underlying', 'Product description', 'Trading Class', 'Intraday Initial 1',
               'Intraday Maintenance 1', 'Overnight Initial', 'Overnight Maintenance', 'Currency', 'Has Options',
               'Short Overnight Initial', 'Short Overnight Maintenance']
    values = ['EUREX', 'SYNTHETIC', '-', 'FMWN', '900', '800', '2000', '1500', 'EUR', 'No', '1800', '1400']
    ibkr = '<table><tr>' + ''.join(f'<th>{v}</th>' for v in headers) + '</tr><tr>' + ''.join(f'<td>{v}</td>' for v in values) + '</tr></table>'
    return ecb, ibkr


def position(**kwargs):
    return FuturePosition(mark_price=100, entry_price=100, margin_per_contract=2000, maintenance_per_contract=1500, **kwargs)


def test_margin_touch_stops_trade_even_if_market_recovers():
    paths = np.array([[1., 1.], [.7, 1.], [1.5, 1.]])
    result = evaluate_paths(position(), ComparisonSettings(savings_rate=0), paths, [4000, 8000])
    assert result['Margin breach (%)'].tolist() == [50., 0.]
    assert result['Futures median end value (EUR)'].tolist() == pytest.approx([2500., 10500.])
    assert result['Initial loss buffer (EUR)'].tolist() == [2500., 6500.]
    assert result['Adverse move to floor (%)'].tolist() == [25., 65.]
    assert result['Initial floor price (points)'].tolist() == [75., 35.]


def test_short_breach_is_price_rise_and_losses_can_exceed_collateral():
    result = evaluate_paths(position(contracts=-1), ComparisonSettings(), np.array([[1.], [2.], [.5]]), [4000])
    assert result.iloc[0]['Initial floor price (points)'] == 125
    assert result.iloc[0]['Margin breach (%)'] == 100
    assert result.iloc[0]['Futures median end value (EUR)'] == -6000


def test_same_cash_savings_compounds_and_fees_do_not_consume_notional():
    settings = ComparisonSettings(months=24, savings_rate=.02, cash_rate=.01, fees_per_contract=10)
    result = evaluate_paths(position(), settings, np.ones((3, 2)), [4000]).iloc[0]
    assert result['Futures median end value (EUR)'] == pytest.approx(3990*cash_growth(.01, 2))
    assert result['Cash benchmark end value (EUR)'] == pytest.approx(4000*cash_growth(.02, 2))
    assert result['Margin breach (%)'] == 0
    assert result['Futures beat cash (%)'] == 0


def test_opening_requires_initial_margin_plus_costs():
    result = evaluate_paths(position(), ComparisonSettings(fees_per_contract=10), np.ones((2, 2)), [2000, 2010])
    assert pd.isna(result.iloc[0]['Margin breach (%)'])
    assert pd.isna(result.iloc[0]['Futures median end value (EUR)'])
    assert result.iloc[0]['Cash benchmark end value (EUR)'] > 2000
    assert result.iloc[1]['Margin breach (%)'] == 0


def test_no_trade_and_old_pnl_excluded_from_new_cash_comparison():
    settings = ComparisonSettings(savings_rate=0)
    paths = np.array([[1.], [.8]])
    a = evaluate_paths(position(), settings, paths, [10000])
    b = evaluate_paths(replace(position(), entry_price=10), settings, paths, [10000])
    pd.testing.assert_frame_equal(a, b)
    result = evaluate_paths(position(contracts=0), settings, paths, [1000]).iloc[0]
    assert result['Futures median end value (EUR)'] == 1000
    assert result['Margin breach (%)'] == 0
    assert pd.isna(result['Adverse move to floor (%)'])


def test_higher_margin_moves_floor_and_daily_interest_continues_after_exit():
    paths = np.array([[1.], [.8], [.8]])
    normal = evaluate_paths(position(), ComparisonSettings(savings_rate=0), paths, [4000]).iloc[0]
    stressed = evaluate_paths(position(), ComparisonSettings(savings_rate=0, margin_stress=2), paths, [4000]).iloc[0]
    assert normal['Margin breach (%)'] == 0
    assert stressed['Margin breach (%)'] == 100
    interest = evaluate_paths(position(), ComparisonSettings(cash_rate=.01), np.array([[1.], [.7], [1.]]), [4000]).iloc[0]
    half_year = cash_growth(.01, .5)
    assert interest['Futures median end value (EUR)'] == pytest.approx((4000*half_year-3000)*half_year)


def test_simulation_reproducibility_zero_volatility_and_monotonic_breach():
    settings = ComparisonSettings(capital_min=2000, capital_max=10000, levels=5)
    first = simulate_comparison(position(), settings, paths=1000)
    pd.testing.assert_frame_equal(first, simulate_comparison(position(), settings, paths=1000))
    assert first['Margin breach (%)'].is_monotonic_decreasing
    assert (first['Sampling low (%)'] <= first['Margin breach (%)']).all()
    assert (first['Sampling high (%)'] >= first['Margin breach (%)']).all()
    flat = simulate_comparison(position(), replace(settings, volatility=0), paths=5)
    assert flat['Margin breach (%)'].eq(0).all()
    assert flat['Futures median end value (EUR)'].equals(flat['Collateral (EUR)'])


@pytest.mark.parametrize('kwargs', [dict(months=0), dict(capital_min=50000), dict(volatility=-.1),
    dict(savings_rate=float('nan')), dict(margin_stress=.5), dict(fees_per_contract=-1)])
def test_invalid_assumptions(kwargs):
    with pytest.raises(DataError):
        ComparisonSettings(**kwargs)


def test_legacy_identity_is_preserved_until_backed_up_switch(tmp_path):
    old = dict(version=1, instrument='FMAE', simulation=True,
               position=dict(contracts=2, entry_price=321., mark_price=456., margin_per_contract=9999., collateral_budget=7000.))
    original = json.dumps(old)
    (tmp_path / SANDBOX_FILE).write_text(original)
    previous, revision = load_sandbox(tmp_path)
    assert previous.instrument == 'FMAE'
    assert (tmp_path / SANDBOX_FILE).read_text() == original
    switch_to_fmwn(tmp_path, revision)
    current, _ = load_sandbox(tmp_path)
    assert current.instrument == 'FMWN' and current.contracts == 2
    assert current.pnl == 0 and current.collateral_budget == 7000
    assert next((tmp_path / '.backups').glob('*.json')).read_text() == original
    with pytest.raises(DataError, match='changed'):
        switch_to_fmwn(tmp_path, revision)


def test_reference_parser_uses_overnight_long_short_and_dated_estr(tmp_path):
    ecb, ibkr = synthetic_reference_html()
    parsed = parse_references(ecb, ibkr)
    assert parsed['estr_rate'] == .0125 and parsed['estr_reference_date'] == '2025-01-02'
    assert parsed['long_initial'] == 2000 and parsed['long_maintenance'] == 1500
    assert parsed['short_initial'] == 1800 and parsed['short_maintenance'] == 1400
    refresh_references(tmp_path, fetch=lambda url: ecb if 'ecb.europa' in url else ibkr)
    before = load_references(tmp_path)
    with pytest.raises(DataError):
        refresh_references(tmp_path, fetch=lambda url: '<html>unavailable</html>')
    assert load_references(tmp_path) == before
    for broken in (ibkr.replace('FMWN', 'FMWO'), ibkr.replace('Overnight Initial', 'Changed layout'), ibkr.replace('EUR', 'USD')):
        with pytest.raises(DataError):
            parse_references(ecb, broken)


def test_comparison_persistence_revision_and_no_overwrite(tmp_path):
    (tmp_path / SANDBOX_FILE).write_text(json.dumps(dict(version=2, instrument='FMWN', simulation=True, position={})))
    defaults, revision = load_comparison(tmp_path, savings_rate=.0125)
    assert defaults.savings_rate == .0125
    changed = replace(defaults, months=6)
    save_comparison(tmp_path, changed, revision)
    assert load_comparison(tmp_path)[0] == changed
    with pytest.raises(DataError, match='changed'):
        save_comparison(tmp_path, defaults, revision)


def test_cash_grid_resolves_opening_margin_and_intermediate_cash_amounts():
    from portfolio_app.futures_comparison import collateral_grid
    p = replace(position(), margin_per_contract=5200., maintenance_per_contract=4500.)
    settings = ComparisonSettings(fees_per_contract=12., comparison_capital=12345.)
    grid = collateral_grid(p, settings)
    assert grid[0] == settings.capital_min and grid[-1] == settings.capital_max
    assert 5212. in grid and 12345. in grid
    assert ((grid > 5212) & (grid < 10000)).sum() >= 15
    assert ((grid > 10000) & (grid < 15000)).sum() >= 15
    assert ((grid >= 5212) & (grid < 15000)).sum() > (grid > 20000).sum()
    assert np.all(np.diff(grid) > 0)
    result = evaluate_paths(p, settings, np.ones((2, 1)))
    assert result.loc[result['Collateral (EUR)'] < 5212, 'Margin breach (%)'].isna().all()
    assert result.loc[result['Collateral (EUR)'] == 5212, 'Opening status'].item() == 'Funded'


def test_cash_grid_uses_stressed_signed_quantity_and_rolling_execution_costs():
    from portfolio_app.futures_comparison import collateral_grid
    p = position(contracts=-2)
    s = ComparisonSettings(rolling=True, margin_stress=1.5, roll_fee_per_side=7., roll_slippage_points=.03,
                           fees_per_contract=900., capital_min=1000., capital_max=18000.)
    assert 6020. in collateral_grid(p, s)  # 2*2000*1.5 + 2*(7+100*.03)
    # An explicit narrow range stays intact, even if its minimum exceeds margin.
    grid = collateral_grid(p, replace(s, capital_min=10000., capital_max=12000.))
    assert grid[0] == 10000 and grid[-1] == 12000 and len(grid) >= 41
    unfunded = replace(s, capital_max=5000.)
    assert np.array_equal(collateral_grid(p, unfunded), np.linspace(1000., 5000., s.levels))


def test_denser_cash_grid_does_not_change_existing_simulation_results():
    p = position()
    s = ComparisonSettings(capital_min=2000., capital_max=10000., levels=3)
    ratios = np.array([[1., 1.], [.7, 1.], [1.5, 1.]])
    sparse = evaluate_paths(p, s, ratios, [2000., 6000., 10000.])
    dense = evaluate_paths(p, s, ratios)
    retained = dense.loc[dense['Collateral (EUR)'].isin(sparse['Collateral (EUR)'])].reset_index(drop=True)
    pd.testing.assert_frame_equal(sparse, retained)
