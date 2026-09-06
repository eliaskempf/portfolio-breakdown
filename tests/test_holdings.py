import pytest

from portfolio_app.holdings import DataError, load_holdings, metadata_dimensions
from portfolio_app.taxonomy import load_classifications, paths_for


def test_optional_fields_and_zero_shares(tmp_path):
    path = tmp_path / "holdings.csv"
    path.write_text("id,name,shares\na,Asset,0\n")
    result = load_holdings(path)
    assert result.iloc[0]["shares"] == 0
    assert result.iloc[0]["ticker"] == ""
    assert result["acquisition_price"].isna().all()


@pytest.mark.parametrize("csv", [
    "id,name\na,Asset\n",
    "id,name,shares\n,Asset,1\n",
    "id,name,shares\na,,1\n",
    "id,name,shares\na,Asset,\n",
    "id,name,shares\na,Asset,-1\n",
    "id,name,shares\na,Asset,inf\n",
    "id,name,shares\na,Asset,NaN\n",
    "id,name,shares,acquisition_price\na,Asset,1,unknown\n",
    "id,name,shares,ticker\na,Asset,1,A\na,Asset,2,B\n",
])
def test_invalid_holdings_are_actionable(tmp_path, csv):
    path = tmp_path / "holdings.csv"
    path.write_text(csv)
    with pytest.raises(DataError):
        load_holdings(path)


def test_repeated_asset_keeps_separate_positions(holdings):
    nvda = holdings.loc[holdings["id"] == "nvda"]
    assert len(nvda) == 2
    assert nvda["position_id"].nunique() == 2


@pytest.mark.parametrize("yaml", [
    "- bad", "a: []", "a: {sector: [[Tech]]}",
    "a: {classifications: {ai: AI}}", "a: {classifications: {ai: [[]]}}",
    "a: {classifications: {ai: [[AI, null]]}}",
    "a: {classifications: {ai: [{path: [AI], weight: 1}]}}",
])
def test_invalid_classifications(tmp_path, yaml):
    path = tmp_path / "classifications.yaml"
    path.write_text(yaml)
    with pytest.raises(DataError):
        load_classifications(path)


def test_duplicate_paths_do_not_change_allocation(tmp_path):
    path = tmp_path / "classifications.yaml"
    path.write_text("a: {classifications: {ai: [[AI, Compute], [AI, Compute], [AI, Energy]]}}")
    classifications = load_classifications(path)
    assert len(paths_for(classifications, "a", "ai")) == 2
    assert paths_for(classifications, "missing", "ai") == (("Unclassified",),)


def test_empty_files(tmp_path):
    csv = tmp_path / "holdings.csv"
    csv.write_text("id,name,shares\n")
    assert load_holdings(csv).empty
    yaml = tmp_path / "classifications.yaml"
    yaml.write_text("")
    assert load_classifications(yaml) == {}


def test_tickers_are_uppercase_before_duplicate_validation(tmp_path):
    path = tmp_path / "holdings.csv"
    path.write_text("id,name,shares,ticker,isin\na,Asset,1, nvda ,us67066g1040\na,Asset,2,NVDA,US67066G1040\n")
    frame = load_holdings(path)
    assert frame["ticker"].tolist() == ["NVDA", "NVDA"]
    assert frame["isin"].eq("US67066G1040").all()


@pytest.mark.parametrize("target,expected", [("0.15", 0.15), ("15%", 0.15), ("0", 0), ("100%", 1), ("", None)])
def test_optional_target_allocation(tmp_path, target, expected):
    path = tmp_path / "holdings.csv"
    path.write_text(f"id,name,shares,target_allocation\na,Asset,1,{target}\n")
    frame = load_holdings(path)
    if expected is None:
        assert frame["target_allocation"].isna().all()
    else:
        assert frame["target_allocation"].iloc[0] == expected
    assert "target_allocation" not in metadata_dimensions(frame)


@pytest.mark.parametrize("target", ["15", "-0.1", "101%", "NaN", "inf", "unknown"])
def test_invalid_targets(tmp_path, target):
    path = tmp_path / "holdings.csv"
    path.write_text(f"id,name,shares,target_allocation\na,Asset,1,{target}\n")
    with pytest.raises(DataError, match="target_allocation"):
        load_holdings(path)
