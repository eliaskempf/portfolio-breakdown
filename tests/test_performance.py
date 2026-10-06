"""Performance calculations use invented positions, never working portfolio data."""

import pandas as pd
import pytest

from portfolio_app.performance import position_performance, summarize_performance


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
