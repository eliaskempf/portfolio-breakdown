from datetime import date
from decimal import Decimal
import json
from pathlib import Path

import pandas as pd
import pytest

from portfolio_app.holdings import DataError, load_holdings, metadata_dimensions
from portfolio_app.positions import read_snapshot, save_position
from portfolio_app.purchases import (
    HISTORY_COLUMN, parse_purchase_text, read_opening, read_purchase_history,
    repeated_batch, save_purchase_batch, summarize_purchases, validate_purchases,
)


def rows():
    return [
        {"date": "2026-01-01", "shares": "2", "price": "100", "fees": "1"},
        {"date": "2026-02-01", "shares": "3", "price": "120", "fees": "2"},
    ]


def fields():
    return {"name": "Synthetic example", "ticker": "demo", "portfolio": "Demo", "account": "Demo A"}


@pytest.mark.parametrize("content", [
    "date,shares,price,fees\n2026-01-01,1.25,12.5,0.1\n",
    "\ufeffDate;Shares;Price;Fees\n2026-01-01;1,25;12,5;0,1\n",
    "date\tshares\tprice\tfees\n2026-01-01\t1,25\t12,5\t0,1\n",
    'date,shares,price,fees\n2026-01-01,"1,25","12,5","0,1"\n',
])
def test_table_formats_preserve_fractional_precision(content):
    purchases = validate_purchases(parse_purchase_text(content))
    result = summarize_purchases(purchases, "eur", {})
    assert result.total_shares == Decimal("1.25")
    assert result.batch_cost == Decimal("15.725")
    assert result.average == Decimal("12.58")


@pytest.mark.parametrize("content", [
    "shares,shares,price\n1,1,2", "ticker,shares,price\nDEMO,1,2",
    "shares,price\n1,2,3", "shares,price\n1", 'shares,price\n1,"unfinished',
    "shares\n1", "shares,price,currency\n1,2,EUR",
])
def test_invalid_input_headers_or_rows_are_rejected(content):
    with pytest.raises(DataError):
        parse_purchase_text(content)


@pytest.mark.parametrize("row", [
    {"shares": "0", "price": "1"}, {"shares": "-1", "price": "1"},
    {"shares": "NaN", "price": "1"}, {"shares": "1", "price": "Infinity"},
    {"shares": "1", "price": "-1"}, {"shares": "1", "price": "1", "fees": "-1"},
    {"shares": "1", "price": "1", "date": "2026-02-30"},
    {"shares": "1", "price": "1", "date": "2099-01-01"},
    {"shares": "1", "price": "1", "date": "01/02/2026"},
    {"shares": "1", "price": "1,234.56"}, {"shares": "1e309", "price": "1"},
    {"shares": "1", "price": "1", "currency": "USD"},
])
def test_invalid_purchases_fail_before_any_save(row):
    with pytest.raises(DataError):
        validate_purchases([row], today=date(2026, 9, 6))


def test_weighted_average_includes_fees_and_existing_opening_cost():
    purchases = validate_purchases(rows())
    result = summarize_purchases(purchases, "EUR", {})
    assert result.total_shares == 5
    assert result.average == Decimal("112.6")
    opening = {"shares": "5", "acquisition_price": "80", "acquisition_currency": "EUR"}
    combined = summarize_purchases(purchases, "EUR", opening)
    assert combined.total_shares == 10
    assert combined.average == Decimal("96.3")


def test_unknown_prices_are_never_treated_as_zero_cost():
    purchases = validate_purchases([{"shares": "2", "price": "", "fees": "1"}])
    assert summarize_purchases(purchases, "EUR", {}).average is None
    opening = {"shares": "2", "acquisition_price": ""}
    result = summarize_purchases(validate_purchases(rows()), "EUR", opening)
    assert result.total_shares == 7
    assert result.batch_cost == 563
    assert result.average is None
    zero_opening = {"shares": "0", "acquisition_price": ""}
    assert summarize_purchases(validate_purchases(rows()), "EUR", zero_opening).average == Decimal("112.6")


@pytest.mark.parametrize("currency", [""])
def test_known_opening_requires_matching_explicit_currency(currency):
    with pytest.raises(DataError, match="currency"):
        summarize_purchases(validate_purchases(rows()), "EUR", {"shares": "5", "acquisition_price": "80", "acquisition_currency": currency})


def test_historical_calculation_changes_cost_without_adding_shares():
    purchases = validate_purchases(rows())
    result = summarize_purchases(purchases, "EUR", {"shares": "5"}, mode="reconcile")
    assert result.total_shares == 5
    assert result.average == Decimal("112.6")
    for opening in ({}, {"shares": "4"}, {"shares": "0"}):
        with pytest.raises(DataError, match="total the shares"):
            summarize_purchases(purchases, "EUR", opening, mode="reconcile")


def test_blank_rows_are_ignored_and_empty_batch_rejected():
    assert len(validate_purchases([{}, *rows(), {"shares": "", "price": None}])) == 2
    with pytest.raises(DataError, match="at least one"):
        validate_purchases([{}])


def test_historical_tolerance_does_not_distort_tiny_positions():
    purchases = validate_purchases([{"shares": "0.000000001", "price": "100"}])
    with pytest.raises(DataError, match="total the shares"):
        summarize_purchases(purchases, "EUR", {"shares": "0.000000000001"}, mode="reconcile")


def test_batch_is_saved_as_one_position_with_atomic_history_and_backup(tmp_path):
    path = tmp_path / "holdings.csv"
    save_purchase_batch(path, fields(), rows(), currency="eur", expected_revision=None)
    snapshot = read_snapshot(path)
    result = snapshot.holdings.iloc[0]
    assert result["ticker"] == "DEMO"
    assert result["shares"] == 5
    assert result["acquisition_price"] == pytest.approx(112.6)
    assert HISTORY_COLUMN not in metadata_dimensions(snapshot.holdings)
    history = read_purchase_history(result[HISTORY_COLUMN])
    assert history[0]["purchases"] == rows()
    original = path.read_bytes()
    more = [{"date": "2026-03-01", "shares": "0.25", "price": "10", "fees": "0"}]
    save_purchase_batch(path, {}, more, currency="EUR", expected_revision=snapshot.revision, position_id="position-0")
    reopened = read_snapshot(path).holdings
    assert len(reopened) == 1
    assert reopened.iloc[0]["shares"] == 5.25
    assert len(read_purchase_history(reopened.iloc[0][HISTORY_COLUMN])) == 2
    assert original in [backup.read_bytes() for backup in (tmp_path / ".backups").glob("*.csv")]


def test_exact_csv_decimals_are_used_for_later_purchases(tmp_path):
    path = tmp_path / "holdings.csv"
    path.write_text("id,name,shares,acquisition_price,acquisition_currency\nexample,Synthetic,0.123456789123456789,10.123456789123456789,EUR\n")
    opening = read_opening(path, read_snapshot(path).revision, "position-0")
    assert opening["shares"] == "0.123456789123456789"
    save_purchase_batch(path, {}, [{"shares": "0.000000001", "price": "10"}], currency="EUR",
                        expected_revision=read_snapshot(path).revision, position_id="position-0")
    raw = pd.read_csv(path, dtype=str)
    assert raw.iloc[0]["shares"] == "0.123456790123456789"


def test_reconcile_preserves_targets_and_custom_metadata(tmp_path):
    path = tmp_path / "holdings.csv"
    path.write_text("id,name,ticker,shares,target_allocation,notes\nexample,Synthetic,DEMO,5,20%,Keep this\n")
    save_purchase_batch(path, {}, rows(), currency="EUR", expected_revision=read_snapshot(path).revision, position_id="position-0", mode="reconcile")
    row = load_holdings(path).iloc[0]
    assert row["shares"] == 5
    assert row["acquisition_price"] == pytest.approx(112.6)
    assert row["target_allocation"] == .2
    assert row["notes"] == "Keep this"


def test_invalid_batch_and_provider_validation_never_partially_save(tmp_path):
    path = tmp_path / "holdings.csv"
    with pytest.raises(DataError):
        save_purchase_batch(path, fields(), [rows()[0], {"shares": "-1", "price": "1"}], currency="EUR", expected_revision=None)
    assert not path.exists()

    def reject(frame):
        raise DataError("Unsupported listing")

    with pytest.raises(DataError):
        save_purchase_batch(path, fields(), rows(), currency="EUR", expected_revision=None, validate=reject)
    assert not path.exists()


def test_stale_and_double_submitted_batch_do_not_add_twice(tmp_path):
    path = tmp_path / "holdings.csv"
    save_purchase_batch(path, fields(), rows(), currency="EUR", expected_revision=None)
    original = path.read_bytes()
    with pytest.raises(DataError, match="Holdings changed"):
        save_purchase_batch(path, fields(), rows(), currency="EUR", expected_revision=None)
    assert path.read_bytes() == original


def test_repeated_import_requires_explicit_override(tmp_path):
    path = tmp_path / "holdings.csv"
    save_purchase_batch(path, fields(), rows(), currency="EUR", expected_revision=None)
    revision = read_snapshot(path).revision
    opening = read_opening(path, revision, "position-0")
    assert repeated_batch(validate_purchases(list(reversed(rows()))), "EUR", opening)
    with pytest.raises(DataError, match="matches purchases saved earlier"):
        save_purchase_batch(path, {}, rows(), currency="EUR", expected_revision=revision, position_id="position-0")
    assert load_holdings(path).iloc[0]["shares"] == 5
    save_purchase_batch(path, {}, rows(), currency="EUR", expected_revision=revision, position_id="position-0", allow_repeat=True)
    assert load_holdings(path).iloc[0]["shares"] == 10


def test_failed_atomic_replace_keeps_both_summary_and_history(tmp_path, monkeypatch):
    path = tmp_path / "holdings.csv"
    save_purchase_batch(path, fields(), rows(), currency="EUR", expected_revision=None)
    original = path.read_bytes()

    def fail(*args):
        raise OSError("disk unavailable")

    monkeypatch.setattr(Path, "replace", fail)
    with pytest.raises(OSError):
        save_purchase_batch(path, {}, rows(), currency="EUR", expected_revision=read_snapshot(path).revision, position_id="position-0", mode="reconcile")
    assert path.read_bytes() == original


def test_manual_summary_edit_keeps_reference_records(tmp_path):
    path = tmp_path / "holdings.csv"
    save_purchase_batch(path, fields(), rows(), currency="EUR", expected_revision=None)
    history = load_holdings(path).iloc[0][HISTORY_COLUMN]
    save_position(path, {"shares": "4"}, expected_revision=read_snapshot(path).revision, position_id="position-0")
    assert load_holdings(path).iloc[0][HISTORY_COLUMN] == history


@pytest.mark.parametrize("value", ["bad JSON", "{}", "[{}]", json.dumps([{"version": 2}])])
def test_corrupt_history_is_actionable(value):
    with pytest.raises(DataError, match="history is invalid"):
        read_purchase_history(value)


def test_large_batch_history_can_be_reopened(tmp_path):
    path = tmp_path / "holdings.csv"
    batch = [{"date": "2026-01-01", "shares": "0.123456789123456789123456789123456789", "price": "1.123456789123456789", "fees": "0.0000000012345678912345"}] * 1000
    save_purchase_batch(path, fields(), batch, currency="EUR", expected_revision=None)
    assert path.stat().st_size > 131072
    opening = read_opening(path, read_snapshot(path).revision, "position-0")
    assert len(read_purchase_history(opening[HISTORY_COLUMN])[0]["purchases"]) == 1000
