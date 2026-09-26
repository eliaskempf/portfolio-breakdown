from pathlib import Path

import pandas as pd
import pytest

from portfolio_app.holdings import DataError, load_holdings, metadata_dimensions
from portfolio_app.positions import read_snapshot, save_position


@pytest.fixture
def path(tmp_path):
    path = tmp_path / "holdings.csv"
    path.write_text(
        "id,name,ticker,isin,shares,acquisition_price,portfolio,account,notes\n"
        "nvda,Nvidia,NVDA,US67066G1040,10.123456789,12.34,AI,Broker,keep this\n"
    )
    return path


def new_position(**overrides):
    return {
        "name": "Arista", "ticker": " anet ", "shares": "1.23456789", "isin": "US0404132054",
        "portfolio": "AI", "account": "Broker", "acquisition_price": "100.50", "acquisition_currency": "eur",
        "target_allocation": "10%", **overrides,
    }


def test_create_from_empty_directory(tmp_path):
    path = tmp_path / "new-data" / "holdings.csv"
    snapshot = read_snapshot(path)
    assert snapshot.holdings.empty
    assert snapshot.revision is None
    asset_id = save_position(path, new_position(), expected_revision=None)
    assert asset_id == "anet"
    result = load_holdings(path)
    assert result.iloc[0]["ticker"] == "ANET"
    assert result.iloc[0]["shares"] == pytest.approx(1.23456789)
    assert result.iloc[0]["acquisition_currency"] == "EUR"
    assert result.iloc[0]["target_allocation"] == 0.1
    assert "acquisition_currency" not in metadata_dimensions(result)


def test_append_preserves_original_cells_and_makes_backup(path):
    original = path.read_bytes()
    snapshot = read_snapshot(path)
    save_position(path, new_position(), expected_revision=snapshot.revision)
    raw = pd.read_csv(path, dtype=str, keep_default_na=False)
    assert raw.iloc[0]["shares"] == "10.123456789"
    assert raw.iloc[0]["acquisition_price"] == "12.34"
    assert raw.iloc[0]["notes"] == "keep this"
    assert raw.iloc[0]["acquisition_currency"] == ""
    assert len(raw) == 2
    assert [item.read_bytes() for item in (path.parent / ".backups").glob("*.csv")] == [original]


def test_edit_replaces_totals_instead_of_adding_a_purchase(path):
    snapshot = read_snapshot(path)
    save_position(path, {"shares": "15.5", "acquisition_price": "106.67", "acquisition_currency": "EUR"},
                  expected_revision=snapshot.revision, position_id="position-0")
    result = load_holdings(path)
    assert len(result) == 1
    assert result.iloc[0]["id"] == "nvda"
    assert result.iloc[0]["shares"] == 15.5
    assert result.iloc[0]["acquisition_price"] == 106.67
    assert result.iloc[0]["notes"] == "keep this"


def test_optional_buy_in_can_be_cleared(path):
    snapshot = read_snapshot(path)
    save_position(path, {"acquisition_price": "", "acquisition_currency": ""},
                  expected_revision=snapshot.revision, position_id="position-0")
    assert load_holdings(path)["acquisition_price"].isna().all()


@pytest.mark.parametrize("fields", [
    {"shares": "-1"}, {"shares": "nan"}, {"name": ""},
    {"target_allocation": "101%"}, {"acquisition_currency": "EUROS"},
])
def test_invalid_position_does_not_change_file(path, fields):
    original = path.read_bytes()
    with pytest.raises(DataError):
        save_position(path, new_position(**fields), expected_revision=read_snapshot(path).revision)
    assert path.read_bytes() == original


def test_stale_edit_cannot_overwrite_new_position(path):
    snapshot = read_snapshot(path)
    save_position(path, new_position(), expected_revision=snapshot.revision)
    newer = path.read_bytes()
    with pytest.raises(DataError, match="Holdings changed"):
        save_position(path, {"shares": "999"}, expected_revision=snapshot.revision, position_id="position-0")
    assert path.read_bytes() == newer


def test_double_submit_is_rejected(path):
    revision = read_snapshot(path).revision
    save_position(path, new_position(), expected_revision=revision)
    with pytest.raises(DataError, match="Holdings changed"):
        save_position(path, new_position(), expected_revision=revision)
    assert len(load_holdings(path)) == 2


def test_existing_instrument_reuses_id_in_another_account(path):
    save_position(path, {
        "id": "nvda", "name": "Nvidia", "ticker": "NVDA", "isin": "US67066G1040",
        "shares": "3", "portfolio": "AI", "account": "Other broker",
    }, expected_revision=read_snapshot(path).revision)
    assert load_holdings(path)["id"].tolist() == ["nvda", "nvda"]


def test_accidental_duplicate_instrument_or_position_is_rejected(path):
    snapshot = read_snapshot(path)
    with pytest.raises(DataError, match="instrument already exists"):
        save_position(path, new_position(ticker="nvda"), expected_revision=snapshot.revision)
    with pytest.raises(DataError, match="position for this instrument"):
        save_position(path, {
            "id": "nvda", "name": "Nvidia", "ticker": "NVDA", "isin": "US67066G1040",
            "shares": "2", "portfolio": "AI", "account": "Broker",
        }, expected_revision=snapshot.revision)


def test_distinct_quote_listing_can_share_isin_but_requires_its_own_id(path):
    save_position(path, new_position(name="NVIDIA", ticker="NVD.DE", isin="US67066G1040"),
                  expected_revision=read_snapshot(path).revision)
    result = load_holdings(path)
    assert result["ticker"].tolist() == ["NVDA", "NVD.DE"]
    assert result["id"].nunique() == 2
    assert result["isin"].nunique() == 1
    with pytest.raises(DataError, match="instrument already exists"):
        save_position(path, new_position(ticker="", isin="US67066G1040"),
                      expected_revision=read_snapshot(path).revision)


def test_failed_replace_preserves_original_and_backup(path, monkeypatch):
    original = path.read_bytes()
    revision = read_snapshot(path).revision

    def fail_replace(source, destination):
        raise PermissionError("read-only destination")

    monkeypatch.setattr(Path, "replace", fail_replace)
    with pytest.raises(PermissionError):
        save_position(path, new_position(), expected_revision=revision)
    assert path.read_bytes() == original
    assert len(list((path.parent / ".backups").glob("*.csv"))) == 1
    assert not list(path.parent.glob("*.tmp"))


def test_custom_validation_runs_before_write(path):
    original = path.read_bytes()

    def reject(frame):
        raise DataError("wrong ETF listing")

    with pytest.raises(DataError, match="wrong ETF listing"):
        save_position(path, new_position(), expected_revision=read_snapshot(path).revision, validate=reject)
    assert path.read_bytes() == original


def test_rename_updates_same_instrument_across_accounts_only(tmp_path):
    path = tmp_path / 'holdings.csv'
    path.write_text('position_key,id,name,ticker,shares,account,bucket_id,within_bucket_target,notes\n'
                    'p1,token,Invented Token,TOKEN-EUR,0.25,First,alpha,0.4,keep one\n'
                    'p2,token,Invented Token,TOKEN-EUR,0.5,Second,beta,0.6,keep two\n'
                    'p3,other,Invented Token,OTHER-EUR,1,Third,alpha,0.6,keep other\n')
    before = load_holdings(path)
    original = path.read_bytes()
    save_position(path, {'name': 'Custom token name', 'shares': '.3'},
                  expected_revision=read_snapshot(path).revision, position_id='p1')
    after = load_holdings(path)
    assert after.name.tolist() == ['Custom token name', 'Custom token name', 'Invented Token']
    assert after.shares.tolist() == [.3, .5, 1.]
    pd.testing.assert_frame_equal(before.drop(columns=['name', 'shares']), after.drop(columns=['name', 'shares']))
    assert next((tmp_path / '.backups').glob('*.csv')).read_bytes() == original
    saved = path.read_bytes()
    with pytest.raises(DataError, match='nonempty name'):
        save_position(path, {'name': '   '}, expected_revision=read_snapshot(path).revision, position_id='p1')
    assert path.read_bytes() == saved


def test_stale_instrument_rename_is_rejected(path):
    stale = read_snapshot(path)
    save_position(path, {'name': 'New custom name'}, expected_revision=stale.revision, position_id='position-0')
    saved = path.read_bytes()
    with pytest.raises(DataError, match='Holdings changed'):
        save_position(path, {'name': 'Older edit'}, expected_revision=stale.revision, position_id='position-0')
    assert path.read_bytes() == saved
