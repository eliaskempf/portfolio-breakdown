"""Performance calculations use invented positions, never working portfolio data."""

import pandas as pd
import pytest

from portfolio_app.performance.performance import position_performance, summarize_performance


def positions():
    return pd.DataFrame({
        "shares": [2., 4., 3.], "acquisition_price": [30., 25., 10.],
        "acquisition_currency": ["EUR", "EUR", "USD"],
        "quote_currency": ["USD", "EUR", "USD"],
        "current_price": [50., 20., 15.], "current_value_reporting": [80., 80., 36.],
    })


def test_currency_matched_gains_and_cost_weighted_summary():
    original = positions()
    result = position_performance(original)
    assert result.cost_basis.tolist() == [60, 100, 30]
    assert result.unrealized_gain.tolist() == [20, -20, 15]
    assert result.native_return_pct.tolist() == pytest.approx([100/3, -20, 50])
    assert pd.isna(result.return_pct.iloc[2])
    assert pd.isna(result.unrealized_gain_reporting.iloc[2])
    summary = summarize_performance(result)
    assert (summary.held_count, summary.covered_count) == (3, 2)
    assert summary.cost_reporting == 160
    assert summary.gain_reporting == 0
    assert summary.return_pct == 0  # Total gain / total cost, not mean of returns.
    pd.testing.assert_frame_equal(original, positions())


def test_cross_currency_performance_uses_current_value_in_cost_currency():
    frame = positions().iloc[[0]].assign(acquisition_currency="GBP")
    result = position_performance(frame, {"GBP": 1.25})
    assert result.cost_basis.iloc[0] == 60  # Historical GBP cost stays GBP.
    assert result.unrealized_gain.iloc[0] == 4  # Current EUR 80 / 1.25 - GBP 60.
    assert result.native_return_pct.iloc[0] == pytest.approx(100 * 4/60)
    assert pd.isna(result.return_pct.iloc[0])
    assert summarize_performance(result).covered_count == 0
    missing_fx = position_performance(frame)
    assert pd.isna(missing_fx.unrealized_gain.iloc[0])
    assert "excluded from gains" in missing_fx.performance_note.iloc[0]


def test_unknown_cost_or_currency_never_implies_zero_or_quote_currency():
    for frame in (positions().assign(acquisition_price=float("nan")),
                  positions().drop(columns="acquisition_currency"), positions().assign(acquisition_currency="")):
        result = position_performance(frame)
        assert result.unrealized_gain.isna().all()
        assert result.performance_note.str.startswith("Missing").all()
        assert summarize_performance(result).gain_reporting is None


def test_missing_valuation_excluded_from_matching_summary_costs():
    frame = positions()
    frame.loc[0, "current_value_reporting"] = float("nan")
    result = position_performance(frame)
    summary = summarize_performance(result)
    assert summary.covered_count == 1
    assert summary.cost_reporting == 100
    assert summary.gain_reporting == -20
    assert summary.return_pct == -20
    assert result.cost_basis.iloc[0] == 60
    # A USD quote and USD basis still support native performance without EUR FX.
    result = position_performance(frame.assign(current_value_reporting=float("nan")))
    assert result.unrealized_gain.iloc[2] == 15


def test_zero_shares_and_zero_cost():
    result = position_performance(positions().assign(shares=0))
    assert result.unrealized_gain.isna().all()
    assert summarize_performance(result).held_count == 0
    result = position_performance(positions().assign(acquisition_price=0))
    assert result.return_pct.isna().all()
    assert result.unrealized_gain_reporting.iloc[0] == 80
    assert summarize_performance(result).return_pct is None


def test_duplicate_instruments_remain_separate_cost_rows():
    frame = positions().iloc[:2].assign(id="synthetic", account=["Synthetic A", "Synthetic B"])
    result = position_performance(frame)
    assert len(result) == 2
    assert result.unrealized_gain.tolist() == [20, -20]


def test_empty_input_and_overflow_stay_unavailable():
    assert summarize_performance(position_performance(positions().iloc[:0])).gain_reporting is None
    result = position_performance(positions().assign(shares=1e308, acquisition_price=1e308))
    assert result.unrealized_gain.isna().all()
    assert result.cost_basis.isna().all()


def test_performance_writes_complete_columns_and_keeps_position_order(monkeypatch):
    frame = pd.concat([positions()] * 32, ignore_index=True)
    frame.index = pd.Index(range(len(frame) * 3, 0, -3), name='source_row')
    frame.attrs['source'] = 'synthetic'
    writes = []
    set_value = pd.DataFrame._set_value

    def counted(self, index, column, value, *args, **kwargs):
        writes.append(column)
        return set_value(self, index, column, value, *args, **kwargs)

    monkeypatch.setattr(pd.DataFrame, '_set_value', counted)
    result = position_performance(frame)
    assert not writes
    pd.testing.assert_index_equal(result.index, frame.index)
    assert result.attrs == frame.attrs
    assert result.unrealized_gain.tolist()[:2] == [20, -20]
    assert result.cost_basis.notna().sum() == len(frame)


def test_cash_costs_are_read_once_and_duplicate_row_labels_keep_separate_balances(monkeypatch):
    from portfolio_app.performance import performance

    frame = positions().iloc[:2].assign(instrument_type='cash', acquisition_price=[10., float('nan')])
    frame.index = [7, 7]
    reads = []
    read = performance.active_components

    def counted(row):
        reads.append(row['shares'])
        return read(row)

    monkeypatch.setattr(performance, 'active_components', counted)
    result = position_performance(frame)
    assert reads == frame.shares.tolist()
    assert result.cost_basis_reporting.notna().tolist() == [True, False]
    assert performance.cash_balance_only(result).tolist() == [False, True]


def test_invalid_closed_cash_evidence_precedes_historical_fx_requests():
    from portfolio_app.performance.cost_basis import component, encode_components
    from portfolio_app.portfolio.holdings import DataError

    frame = positions().iloc[:2].assign(instrument_type=['equity', 'cash'], shares=[2., 0.],
                                         acquisition_currency='USD')
    frame['cost_basis_details'] = [
        encode_components([component(2, 60, 'USD', '2024-01-02')], frame.iloc[0]), '{invalid']

    class Historical:
        def get(self, *args):
            pytest.fail('Invalid cash evidence must be reported before requesting an exchange rate')

    with pytest.raises(DataError, match='Stored cost details'):
        position_performance(frame, historical=Historical())
