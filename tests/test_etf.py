from dataclasses import replace
from datetime import date
from pathlib import Path
import shutil
from zipfile import ZipFile
import xml.etree.ElementTree as ET

import pandas as pd
import pytest

from portfolio_app.aggregation import aggregate, aggregate_dimension
from portfolio_app.etf import (
    FundSnapshot, effective_exposure_table, expand_etfs, fund_breakdown,
    load_funds, matching_fund, snapshot_age_days, validate_constituents,
)
from portfolio_app.exposures import normalize_exposures
from portfolio_app.grouping import InstrumentGroup, group_classifications, group_exposures, group_members_table
from portfolio_app.holdings import DataError
from portfolio_app.vaneck import parse_holdings, refresh_snapshot


@pytest.fixture
def fund():
    return FundSnapshot(
        "smh_ucits", "VanEck Semiconductor UCITS ETF", "IE00BMC38736", ("VVSM.DE", "SMH.L"),
        date(2026, 9, 4), "https://www.vaneck.com/", pd.DataFrame([
            {"constituent_id": "nvda", "name": "Nvidia Corp", "ticker": "NVDA", "isin": "US67066G1040", "weight": 0.08},
            {"constituent_id": "tsmc", "name": "TSMC", "ticker": "TSM", "isin": "US8740391003", "weight": 0.07},
        ]),
    )


@pytest.fixture
def positions():
    return pd.DataFrame([
        {"position_id": "p1", "id": "my-nvidia", "name": "Nvidia", "ticker": "NVDA", "isin": "US67066G1040", "current_value_eur": 2000., "portfolio": "AI", "account": "A"},
        {"position_id": "p2", "id": "semis", "name": "SMH UCITS", "ticker": "VVSM.DE", "isin": "IE00BMC38736", "current_value_eur": 10000., "portfolio": "AI", "account": "B"},
        {"position_id": "p3", "id": "us-smh", "name": "US SMH", "ticker": "SMH", "isin": "US92189F6768", "current_value_eur": 500., "portfolio": "Core", "account": "B"},
    ])


def test_partial_weights_and_source_values_are_conserved(fund, positions):
    exposures = expand_etfs(normalize_exposures(positions), [fund], positions)
    assert exposures["value"].sum() == pytest.approx(12500)
    source = exposures.loc[exposures["source_instrument"] == "semis"]
    assert source["value"].sum() == pytest.approx(10000)
    assert source["value"].tolist() == pytest.approx([800, 700, 8500])
    assert source["source_position_id"].eq("p2").all()
    assert source["account"].eq("B").all()
    assert source["direct_or_indirect"].eq("indirect").all()
    assert source.loc[source["ticker"] == "NVDA", "asset_id"].iloc[0] == "my-nvidia"
    assert exposures.loc[exposures["asset_id"] == "us-smh", "value"].iloc[0] == 500
    table = effective_exposure_table(exposures)
    nvidia = table.loc[table["Ticker"] == "NVDA"].iloc[0]
    assert nvidia["Direct (EUR)"] == 2000
    assert nvidia["ETF-derived (EUR)"] == 800
    assert nvidia["Total (EUR)"] == 2800
    assert nvidia["Allocation %"] == pytest.approx(22.4)


def test_smh_group_candidates_respect_listing_identity_and_snapshot(fund, positions):
    from portfolio_app.etf import smh_group_candidates

    positions = positions.copy()
    positions.loc[positions.id == "my-nvidia", "ticker"] = "NVD.DE"
    assert smh_group_candidates(positions, [fund]) == ({"semis"}, {"my-nvidia"}, {"my-nvidia"})
    assert smh_group_candidates(positions, []) == (set(), set(), set())
    positions.loc[positions.id == "my-nvidia", ["ticker", "isin"]] = ["NVDA", "US0000000000"]
    assert smh_group_candidates(positions, [fund])[1] == set()
    positions.loc[positions.id == "my-nvidia", "isin"] = ""
    assert smh_group_candidates(positions, [fund])[1] == {"my-nvidia"}
    empty_weights = fund.constituents.copy()
    empty_weights["weight"] = 0.
    assert smh_group_candidates(positions, [replace(fund, constituents=empty_weights)])[1] == set()


@pytest.mark.parametrize("lookthrough", [False, True])
def test_grouping_conserves_values_metadata_and_collapses_residual(fund, positions, lookthrough):
    group = InstrumentGroup("view-group:example", "Synthetic group", frozenset({"semis", "my-nvidia"}), frozenset({"semis"}))
    original = normalize_exposures(positions)
    if lookthrough:
        original = expand_etfs(original, [fund], positions)
    before = original.copy(deep=True)
    result = group_exposures(original, group)
    pd.testing.assert_frame_equal(original, before)
    assert result["value"].sum() == 12500
    assert result.loc[result.asset_id == group.asset_id, "value"].sum() == 12000
    assert result.groupby("account")["value"].sum().to_dict() == {"A": 2000, "B": 10500}
    assert result.source_position_id.tolist() == original.source_position_id.tolist()
    assert result.loc[result.asset_id == group.asset_id, "ticker"].eq("").all()
    nodes = aggregate_dimension(result, "holding", show_tickers=True)
    assert nodes.loc[nodes["label"] == group.name, "value"].tolist() == [12000]
    # Source instruments determine grouping, not the underlying company alone.
    other = original.loc[original.asset_id == "my-nvidia"].iloc[[0]].copy()
    other["source_instrument"] = "another-fund"
    other["direct_or_indirect"] = "indirect"
    extended = group_exposures(pd.concat([original, other], ignore_index=True), group)
    assert extended.loc[extended.source_instrument == "another-fund", "asset_id"].tolist() == ["my-nvidia"]


def test_group_classifications_and_filtered_members_preserve_original_data(fund, positions):
    group = InstrumentGroup("view-group:example", "Synthetic group", frozenset({"semis", "my-nvidia"}), frozenset({"semis"}))
    classes = {"semis": {"labels": (("Synthetic category", "Funds"),)}, "my-nvidia": {"labels": (("Synthetic category", "Chips"),)}}
    result = group_classifications(classes, group)
    assert result[group.asset_id] == classes["semis"]
    assert group.asset_id not in classes
    exposures = group_exposures(expand_etfs(normalize_exposures(positions), [fund], positions), group)
    nodes = aggregate(exposures, result, taxonomy="labels")
    assert nodes.loc[nodes.label == "Funds", "value"].tolist() == [12000]
    detail = group_members_table(positions.loc[positions.id == "my-nvidia"], group)
    assert detail["EUR value"].tolist() == [2000]
    assert detail["Within group (%)"].tolist() == [100]
    # Unvalued positions remain visible and do not turn into zero-valued holdings.
    positions.loc[positions.id == "my-nvidia", "current_value_eur"] = float("nan")
    detail = group_members_table(positions, group)
    assert detail.iloc[-1]["Investment"] == "Nvidia"
    assert pd.isna(detail.iloc[-1]["EUR value"])
    with pytest.raises(DataError, match="conflicts"):
        group_exposures(exposures, group)


def test_grouping_multiple_accounts_and_empty_selections(fund, positions):
    group = InstrumentGroup("view-group:example", "Synthetic group", frozenset({"semis", "my-nvidia"}), frozenset({"semis"}))
    extra = positions.iloc[[0]].copy()
    extra["position_id"], extra["account"], extra["current_value_eur"] = "p4", "C", 500.
    positions = pd.concat([positions, extra], ignore_index=True)
    exposures = expand_etfs(normalize_exposures(positions), [fund], positions)
    result = group_exposures(exposures, group)
    assert result.loc[result.asset_id == group.asset_id, "value"].sum() == 12500
    detail = group_members_table(positions, group)
    assert detail["EUR value"].tolist() == [10000, 2500]
    assert detail["Within group (%)"].sum() == 100
    assert group_exposures(exposures.iloc[:0], group).empty
    assert group_members_table(positions.iloc[:0], group).empty


def test_full_weights_need_no_residual(fund):
    frame = fund.constituents.copy()
    frame["weight"] = [0.6, 0.4]
    assert len(fund_breakdown(replace(fund, constituents=frame))) == 2


def test_generic_classification_and_missing_constituents(fund, positions):
    exposures = expand_etfs(normalize_exposures(positions), [fund], positions)
    classification = {"my-nvidia": {"ai": (("AI", "Compute", "GPUs"),)}}
    nodes = aggregate(exposures, classification, taxonomy="ai")
    assert nodes.iloc[0]["value"] == 12500
    assert nodes.loc[nodes["label"] == "GPUs", "value"].iloc[0] == 2800
    assert nodes.loc[nodes["label"] == "Unclassified", "value"].iloc[0] == 9700
    holdings = aggregate_dimension(exposures, "holding", show_tickers=True)
    assert len(holdings.loc[holdings["label"] == "Nvidia (NVDA)"]) == 1


def test_fund_table_uses_same_resolved_classifications_as_exposure_pipeline(fund, positions):
    from portfolio_app.etf_ui import classified_fund_table

    classifications = {"my-nvidia": {"sector": (("Synthetic sector", "Synthetic branch"),)}}
    positions = positions.copy()
    # Exchange-qualified direct listing is linked through ISIN, even when the
    # provider's ticker and constituent ID differ from the direct position.
    positions.loc[positions.id == "my-nvidia", "ticker"] = "NVD.DE"
    table = classified_fund_table(fund, positions, classifications)
    assert table.weight.is_monotonic_decreasing
    assert table.loc[table.ticker == "NVDA", "classification:sector"].iloc[0] == "Synthetic sector > Synthetic branch"
    assert table.loc[table.constituent_id.str.startswith("etf-other:"), "classification:sector"].iloc[0] == "Unclassified"
    assert table.weight.sum() == pytest.approx(1)


def test_selected_label_comparison_combines_direct_and_fund_exposures(fund, positions):
    from portfolio_app.label_comparison import Label, compare_labels

    classifications = {
        "my-nvidia": {"labels": (("Synthetic group", "Branch A"),)},
        "tsmc": {"labels": (("Synthetic group", "Branch B"),)},
        "semis": {"labels": (("Synthetic group", "Fund"),)},
    }
    label = Label("labels", ("Synthetic group",))
    direct = compare_labels(normalize_exposures(positions), classifications, [label], overlap="split")
    expanded = expand_etfs(normalize_exposures(positions), [fund], positions)
    indirect = compare_labels(expanded, classifications, [label], overlap="split")
    assert direct.matched_value == 12000
    assert indirect.matched_value == 3500  # Direct 2000 plus named constituents 800+700.
    assert indirect.table.iloc[0]["Assets"] == 2
    assert indirect.unmatched_value == 9000  # Residual + unrelated instrument, no guessed labels.
    assert indirect.table.iloc[0]["Selected labels %"] == 100
    assert indirect.table.iloc[0]["Portfolio %"] == 28


@pytest.mark.parametrize("weights", [[0.7, 0.4], [-0.1, 0.2], [float("nan"), 0.2], [float("inf"), 0.2]])
def test_invalid_weights_are_rejected(fund, weights):
    frame = fund.constituents.copy()
    frame["weight"] = weights
    with pytest.raises(DataError):
        validate_constituents(frame)


def test_duplicate_constituents_are_rejected(fund):
    frame = fund.constituents.copy()
    frame["constituent_id"] = "duplicate"
    with pytest.raises(DataError):
        validate_constituents(frame)


def test_only_ucits_listings_match(fund):
    assert matching_fund({"ticker": "SMH"}, [fund]) is None
    assert matching_fund({"ticker": "smh.l"}, [fund]) is fund
    assert matching_fund({"isin": "IE00BMC38736", "ticker": "SMH.MI"}, [fund]) is fund
    assert matching_fund({"isin": "US92189F6768", "ticker": "SMH.L"}, [fund]) is None


def test_multiple_etf_positions_merge_exposure(fund, positions):
    extra = positions.iloc[[1]].copy()
    extra["position_id"] = "p4"
    extra["current_value_eur"] = 5000.
    extra["account"] = "C"
    positions = pd.concat([positions, extra], ignore_index=True)
    exposures = expand_etfs(normalize_exposures(positions), [fund], positions)
    table = effective_exposure_table(exposures)
    assert table.loc[table["Ticker"] == "NVDA", "Total (EUR)"].iloc[0] == 3200
    assert table["Total (EUR)"].sum() == 17500


def test_zero_unvalued_and_unsupported_positions(fund, positions):
    positions["current_value_eur"] = 0.
    result = expand_etfs(normalize_exposures(positions), [fund], positions)
    assert effective_exposure_table(result)["Allocation %"].isna().all()
    positions["current_value_eur"] = float("nan")
    result = expand_etfs(normalize_exposures(positions), [fund], positions)
    assert result.empty
    assert effective_exposure_table(result).empty


def test_synthetic_provider_snapshot(sample_data_dir):
    funds = load_funds(sample_data_dir / "etfs")
    assert len(funds) == 1
    fund = funds[0]
    assert fund.isin == "IE00BMC38736"
    assert fund.as_of <= date.today()
    assert len(fund.constituents) == 2
    assert fund.constituents["weight"].sum() <= 1 + 1e-12
    assert fund_breakdown(fund)["weight"].sum() == pytest.approx(1)


@pytest.fixture
def provider_xlsx(tmp_path):
    # Synthetic provider-shaped XLSX: no dependency on live downloads or Excel libraries.
    rows = [
        ["All Holdings  09/04/2026"], [],
        ["Number", "Holding Name", "Ticker", "ISIN", "Shares", "Market Value", "% of Net Assets"],
        ["1", "Nvidia", "NVDA US", "US67066G1040", "10", "$ 100", "8.20%"],
        ["2", "Taiwan Semiconductor", "TSM US", "US8740391003", "10", "$ 100", "7.10%"],
        ["3", "Other/Cash", " -- ", " -- ", " -- ", "$ 100", "84.70%"],
    ]
    namespace = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    strings = ET.Element("sst", xmlns=namespace)
    sheet = ET.Element("worksheet", xmlns=namespace)
    data = ET.SubElement(sheet, "sheetData")
    index = 0
    for row_index, values in enumerate(rows, start=1):
        row = ET.SubElement(data, "row", r=str(row_index))
        for column, value in enumerate(values):
            ET.SubElement(ET.SubElement(strings, "si"), "t").text = value
            cell = ET.SubElement(row, "c", r=f"{chr(65 + column)}{row_index}", t="s")
            ET.SubElement(cell, "v").text = str(index)
            index += 1
    path = tmp_path / "provider.xlsx"
    with ZipFile(path, "w") as archive:
        archive.writestr("xl/sharedStrings.xml", ET.tostring(strings))
        archive.writestr("xl/worksheets/sheet1.xml", ET.tostring(sheet))
    return path


def test_vaneck_export_parser(provider_xlsx):
    as_of, constituents = parse_holdings(provider_xlsx)
    assert as_of == "2026-09-04"
    assert constituents["ticker"].tolist() == ["NVDA", "TSM"]
    assert constituents["constituent_id"].tolist() == ["nvda", "tsmc"]
    assert constituents["weight"].tolist() == [0.082, 0.071]


@pytest.fixture
def configured_fund(tmp_path, sample_data_dir):
    import yaml

    directory = tmp_path / "etfs"
    shutil.copytree(sample_data_dir / "etfs", directory)
    manifest = directory / "smh_ucits.yaml"
    raw = yaml.safe_load(manifest.read_text())
    raw["as_of"] = "2026-09-01"
    manifest.write_text(yaml.safe_dump(raw))
    return load_funds(directory)[0]


def test_snapshot_recency(fund):
    assert snapshot_age_days(fund, date(2026, 9, 5)) == 1
    assert snapshot_age_days(fund, date(2026, 9, 12)) == 8
    assert snapshot_age_days(fund, date(2026, 9, 3)) == -1


def test_snapshot_update_preserves_ids_and_reloads(configured_fund, provider_xlsx):
    frame = configured_fund.constituents.copy()
    frame.loc[frame["ticker"] == "NVDA", "constituent_id"] = "custom-nvidia"
    fund = replace(configured_fund, constituents=frame)
    refreshed = refresh_snapshot(fund, fetch=provider_xlsx.read_bytes, today=date(2026, 9, 5))
    assert refreshed.as_of == date(2026, 9, 4)
    assert refreshed.constituents["constituent_id"].tolist() == ["custom-nvidia", "tsmc"]
    reloaded = load_funds(fund.manifest_path.parent)[0]
    assert reloaded.as_of == refreshed.as_of
    pd.testing.assert_frame_equal(refreshed.constituents, reloaded.constituents)


@pytest.mark.parametrize("failure", ["network", "invalid_file", "older", "future", "disk"])
def test_failed_update_preserves_previous_snapshot(configured_fund, provider_xlsx, monkeypatch, failure):
    from zipfile import BadZipFile

    fund = configured_fund
    old_manifest = fund.manifest_path.read_bytes()
    old_files = {path: path.read_bytes() for path in fund.manifest_path.parent.glob("*.csv")}

    def fetch():
        if failure == "network":
            raise ConnectionError("offline")
        return b"not an XLSX" if failure == "invalid_file" else provider_xlsx.read_bytes()

    if failure == "older":
        fund = replace(fund, as_of=date(2026, 9, 6))
    if failure == "disk":
        original_replace = Path.replace

        def fail_manifest_replace(source, destination):
            if destination == fund.manifest_path:
                raise OSError("read-only manifest")
            return original_replace(source, destination)

        monkeypatch.setattr(Path, "replace", fail_manifest_replace)
    today = date(2026, 9, 3) if failure == "future" else date(2026, 9, 7)
    with pytest.raises((DataError, OSError, BadZipFile)):
        refresh_snapshot(fund, fetch=fetch, today=today)
    assert fund.manifest_path.read_bytes() == old_manifest
    assert all(path.read_bytes() == content for path, content in old_files.items())
    assert load_funds(fund.manifest_path.parent)[0].as_of == date(2026, 9, 1)


def test_ambiguous_company_identity_is_rejected(fund, positions):
    extra = positions.iloc[[0]].copy()
    extra["id"] = "another-nvidia-id"
    positions = pd.concat([positions, extra], ignore_index=True)
    with pytest.raises(DataError, match="Several asset IDs"):
        expand_etfs(normalize_exposures(positions), [fund], positions)
