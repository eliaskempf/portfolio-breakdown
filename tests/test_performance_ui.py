import shutil

from test_ui import by_label, launch


def test_performance_summary_and_holdings_with_static_prices(tmp_path, sample_data_dir):
    shutil.copy(sample_data_dir / "demo_prices.json", tmp_path)
    path = tmp_path / "holdings.csv"
    path.write_text("id,name,ticker,shares,acquisition_price,acquisition_currency\n"
                    "a,Synthetic A,NVDA,1,60,EUR\nb,Synthetic B,ENR.DE,2,30,EUR\n"
                    "c,Synthetic C,TSM,1,40,USD\nd,Synthetic D,NVDA,0,20,EUR\n")
    original = path.read_bytes()
    app = launch(tmp_path)
    assert not app.exception
    metrics = {item.label: item.value for item in app.metric}
    assert metrics["Unrealized gain / loss (EUR)"] == "€+0.00"
    assert metrics["Return on cost"] == "+0.00%"
    assert metrics["Cost basis (EUR)"] == "€120.00"
    assert any("Coverage: 2 of 3" in item.value for item in app.caption)
    table = next(item.value for item in app.dataframe if "shares" in item.value)
    assert table.loc[table.id == "c", "Performance"].iloc[0] == 25
    assert "unrealized_gain" not in table and "return_pct" not in table
    by_label(app.radio, "Performance display").set_value("Amount").run()
    table = next(item.value for item in app.dataframe if "shares" in item.value)
    assert table.loc[table.id == "c", "Performance"].iloc[0] == 10
    by_label(app.multiselect, "Holdings").set_value(["a"]).run()
    assert not app.exception
    assert next(item.value for item in app.metric if item.label == "Cost basis (EUR)") == "€120.00"
    assert next(item.value for item in app.dataframe if "shares" in item.value).Performance.tolist() == [20]
    by_label(app.radio, "Portfolio representation").set_value("ETF look-through").run()
    assert not app.exception
    assert next(item.value for item in app.metric if item.label == "Cost basis (EUR)") == "€120.00"
    assert path.read_bytes() == original


def test_legacy_unlabeled_buy_in_explained_without_invented_performance(tmp_path, sample_data_dir):
    shutil.copy(sample_data_dir / "demo_prices.json", tmp_path)
    (tmp_path / "holdings.csv").write_text("id,name,ticker,shares,acquisition_price\na,Synthetic A,NVDA,1,40\n")
    app = launch(tmp_path)
    assert not app.exception
    assert any("Add average buy-in prices" in item.value for item in app.info)
    table = next(item.value for item in app.dataframe if "shares" in item.value)
    assert table.Performance.isna().all()
    assert table.performance_note.tolist() == ["Missing buy-in currency"]


def test_label_performance_toggle_and_detail_use_cost_weighted_return(tmp_path, sample_data_dir):
    shutil.copy(sample_data_dir / "demo_prices.json", tmp_path)
    (tmp_path / "holdings.csv").write_text("id,name,ticker,shares,acquisition_price,acquisition_currency\na,Synthetic A,NVDA,1,40,EUR\nb,Synthetic B,ENR.DE,2,60,EUR\n")
    (tmp_path / "classifications.yaml").write_text("a:\n  classifications:\n    labels: [[Group A, Child]]\nb:\n  classifications:\n    labels: [[Group A, Child]]\n")
    app = launch(tmp_path)
    assert not app.exception
    assert app.dataframe[0].value.Performance.tolist() == [-25]  # Gain -40 / cost 160.
    assert app.dataframe[0].value["Performance coverage"].tolist() == ["Complete"]
    by_label(app.radio, "Performance display").set_value("Amount").run()
    assert app.dataframe[0].value.Performance.tolist() == [-40]
    from portfolio_app.label_comparison import Label
    by_label(app.selectbox, "Detail view").set_value((Label("labels", ("Group A",)).key,)).run()
    assert not app.exception
    assert app.dataframe[0].value.Performance.tolist() == [40, -80]
    by_label(app.radio, "Performance display").set_value("%").run()
    assert app.dataframe[0].value.Performance.tolist() == [100, -100*2/3]
    by_label(app.selectbox, "Group by").set_value("taxonomy:labels").run()
    assert not app.exception
    assert set(app.dataframe[0].value.Performance) == {-25}
