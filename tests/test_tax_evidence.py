"""Invented prices, FX and distributions only; network providers are injected."""
from datetime import date
from decimal import Decimal as D
import pandas as pd
import pytest

from portfolio_app.portfolio.holdings import DataError
from portfolio_app.taxes.tax_evidence import (market_from_frame, to_eur, parse_ecb, prepare_inputs,
    load_inputs, save_inputs, read_market, request_market)


def frame(rows):
    return pd.DataFrame(rows, columns=['date', 'Close', 'Dividends', 'Stock Splits']).set_index('date').set_axis(pd.to_datetime([r[0] for r in rows]))


def test_per_date_fx_and_gross_distributions():
    f = frame([('2025-01-02', 100, 0, 0), ('2025-06-02', 110, 2, 0), ('2025-12-30', 120, 0, 0)])
    rates = {'2025-01-02': {'USD': D(2)}, '2025-06-02': {'USD': D(4)}, '2025-12-30': {'USD': D(3)}}
    data = market_from_frame(f, 'USD', 2025, '2026-01-01', rates)
    assert D(data['start_price']) == 50
    assert D(data['end_price']) == 40
    assert D(data['distributions']) == D('.5')


def test_splits_inside_and_after_year_are_not_double_adjusted():
    # Provider history is normalized for a 2:1 split in Y and a 3:1 split in Y+1.
    f = frame([('2025-01-02', 10, 0, 0), ('2025-03-03', 11, 1, 0),
               ('2025-07-01', 12, 0, 2), ('2025-12-30', 13, 0, 0), ('2026-03-02', 14, 0, 3)])
    data = market_from_frame(f, 'EUR', 2025, '2026-11-01', {})
    assert D(data['start_price']) == 30
    assert D(data['end_price']) == 39
    assert D(data['distributions']) == 3
    assert data['splits'] == [('2025-07-01', '2')]


def test_missing_start_end_and_empty_distribution_history():
    f = frame([('2025-06-02', 100, 0, 0)])
    data = market_from_frame(f, 'EUR', 2025, '2026-01-01', {})
    assert data['start_price'] is None and data['end_price'] is None
    assert prepare_inputs(2025, {}, data).distributions is None
    assert prepare_inputs(2025, {'distribution_mode': 'Accumulating / no distributions'}, data).distributions == 0


def test_manual_overrides_and_forecast_additions():
    data = {'start_price': '100', 'end_price': '110', 'distributions': '1', 'distribution_count': 1}
    manual = {'end_price': '120', 'expected_distributions': '.5'}
    inputs = prepare_inputs(2026, manual, data, forecast=True, current_price=D(130))
    assert inputs.end_price == 120 and inputs.distributions == D('1.5')
    assert prepare_inputs(2026, {}, data, forecast=True, current_price=D(130)).end_price == 130
    assert prepare_inputs(2025, manual, data).distributions == 1


def test_fx_weekends_bounded_and_no_future_rate():
    rates = {'2025-06-06': {'USD': D(2)}, '2025-06-09': {'USD': D(3)}}
    assert to_eur(D(10), 'USD', '2025-06-08', rates) == 5
    with pytest.raises(DataError):
        to_eur(D(10), 'USD', '2025-06-05', rates)
    with pytest.raises(DataError):
        to_eur(D(10), 'USD', '2025-07-05', rates)


def test_ecb_xml():
    assert parse_ecb(b'<root><Cube time="2025-01-02"><Cube currency="USD" rate="2"/></Cube></root>') == {'2025-01-02': {'USD': D(2)}}


def test_private_input_round_trip_and_stale_write(tmp_path):
    raw, rev = load_inputs(tmp_path)
    raw['funds']['2025:invented'] = {'exemption': '.3', 'start_price': '100', 'source': 'Invented assumption'}
    save_inputs(tmp_path, raw, rev)
    saved, newer = load_inputs(tmp_path)
    assert saved == raw and newer
    with pytest.raises(DataError):
        save_inputs(tmp_path, raw, rev)
    assert not (tmp_path/'account-journal.json').exists()


def test_requests_are_shared_and_manual_inputs_survive_refresh(tmp_path):
    class Jobs:
        work = None
        calls = 0
        def pending(self, *args):
            return self.work is not None
        def request(self, workspace, key, work):
            self.work = work
            self.calls += 1
    jobs = Jobs()
    download = lambda ticker, year: dict(start_price='100', end_price='110', fetched_on=date.today().isoformat())
    for _ in range(3):
        request_market(tmp_path, 2025, 'invented', 'SYN', jobs=jobs, download=download)
    assert jobs.calls == 1
    work, jobs.work = jobs.work, None
    work()
    assert read_market(tmp_path, 2025, 'invented')['end_price'] == '110'
    request_market(tmp_path, 2025, 'invented', 'SYN', jobs=jobs, download=download)
    assert jobs.calls == 1


def test_quote_subunits_do_not_silently_set_distribution_units():
    data = market_from_frame(frame([('2025-01-02', 10000, 0, 0), ('2025-12-30', 11000, 2, 0)]),
                             'EUR', 2025, '2026-01-01', {}, multiplier=D('.01'))
    assert D(data['start_price']) == 100 and D(data['end_price']) == 110
    assert data['distributions'] is None
    assert prepare_inputs(2025, {}, data).distributions is None


def test_distributing_fund_download_is_used_automatically():
    from portfolio_app.taxes.tax_evidence import download_market
    class Provider:
        history_metadata = {'currency': 'USD'}
        def history(self, **kwargs):
            assert kwargs['actions'] and not kwargs['auto_adjust']
            return frame([('2025-01-02', 200, 0, 0), ('2025-06-02', 220, 4, 0), ('2025-12-30', 240, 0, 0)])
    rates = {day: {'USD': D(2)} for day in ('2025-01-02', '2025-06-02', '2025-12-30')}
    market = download_market('SYNTHETIC', 2025, today=date(2026, 1, 2), factory=lambda ticker: Provider(), fx_loader=lambda day: rates)
    inputs = prepare_inputs(2025, {}, market)
    assert inputs.distributions == D(2)
    assert inputs.start_price == 100 and inputs.end_price == 120


def test_accumulation_conflict_requires_review_and_manual_overrides_win():
    market = dict(start_price='100', end_price='110', distribution_policy='Accumulating', distribution_count=1, distributions='1')
    assert prepare_inputs(2025, {}, market).issues
    manual = {'distribution_mode': 'Manual total', 'distributions': '2'}
    inputs = prepare_inputs(2025, manual, market)
    assert inputs.distributions == 2 and not inputs.issues
