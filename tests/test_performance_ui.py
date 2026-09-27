import shutil

from test_ui import by_label, launch, theme_view


def test_performance_summary_and_holdings_with_static_prices(tmp_path, sample_data_dir):
    shutil.copy(sample_data_dir / "demo_prices.json", tmp_path)
    path = tmp_path / "holdings.csv"
    path.write_text("id,name,ticker,shares,acquisition_price,acquisition_currency\n"
                    "a,Synthetic A,NVDA,1,60,EUR\nb,Synthetic B,ENR.DE,2,30,EUR\n"
                    "c,Synthetic C,TSM,1,40,USD\nd,Synthetic D,NVDA,0,20,EUR\n")
    original = path.read_bytes()
    app = launch(tmp_path)
    assert not app.exception
    assert by_label(app.metric, 'Current value').delta == '+€0.00'
    assert any('coverage: 2 of 3' in item.value for item in app.caption)
    table = next(item.value for item in app.tabs[1].dataframe if 'shares' in item.value)
    assert table.loc[table.id == 'c', 'Performance'].isna().all()  # Foreign costs aren't EUR returns.
    by_label(app.get('button_group'), 'Performance display').set_value('%').run()
    assert by_label(app.metric, 'Current value').delta == '+0.00%'
    by_label(app.multiselect, 'Holdings').set_value(['a']).run()
    assert not app.exception
    assert by_label(app.metric, 'Current value').delta == '+0.00%'
    assert next(item.value for item in app.tabs[1].dataframe if 'shares' in item.value).Performance.tolist() == [100 / 3]
    by_label(app.get('button_group'), 'Performance display').set_value('€').run()
    assert next(item.value for item in app.tabs[1].dataframe if 'shares' in item.value).Performance.tolist() == [20]
    by_label(app.toggle, 'Break down ETFs').set_value(True).run()
    assert not app.exception
    assert by_label(app.metric, 'Current value').delta == '+€0.00'
    assert path.read_bytes() == original


def test_legacy_unlabeled_buy_in_explained_without_invented_performance(tmp_path, sample_data_dir):
    shutil.copy(sample_data_dir / "demo_prices.json", tmp_path)
    (tmp_path / "holdings.csv").write_text("id,name,ticker,shares,acquisition_price\na,Synthetic A,NVDA,1,40\n")
    app = launch(tmp_path)
    assert not app.exception
    assert any("coverage: 0 of 1" in item.value for item in app.caption)
    assert by_label(app.metric, 'Current value').delta == 'Unavailable'
    table = next(item.value for item in app.dataframe if "shares" in item.value)
    assert table.Performance.isna().all()
    assert table.performance_note.tolist() == ["Missing buy-in currency"]


def test_label_performance_toggle_and_detail_use_cost_weighted_return(tmp_path, sample_data_dir):
    shutil.copy(sample_data_dir / "demo_prices.json", tmp_path)
    (tmp_path / "holdings.csv").write_text("id,name,ticker,shares,acquisition_price,acquisition_currency\na,Synthetic A,NVDA,1,40,EUR\nb,Synthetic B,ENR.DE,2,60,EUR\n")
    (tmp_path / "classifications.yaml").write_text("a:\n  classifications:\n    labels: [[Group A, Child]]\nb:\n  classifications:\n    labels: [[Group A, Child]]\n")
    app = launch(tmp_path)
    theme_view(app, 'selected_labels')
    assert not app.exception
    by_label(app.get('button_group'), 'Performance display').set_value('%').run()
    assert app.tabs[1].dataframe[-1].value.Performance.tolist() == [-25]  # Gain -40 / cost 160.
    assert app.tabs[1].dataframe[-1].value["Performance coverage"].tolist() == ["Complete"]
    by_label(app.get("button_group"), "Performance display").set_value("€").run()
    assert app.tabs[1].dataframe[-1].value.Performance.tolist() == [-40]
    from portfolio_app.label_comparison import Label
    by_label(app.selectbox, "Detail view").set_value((Label("labels", ("Group A",)).key,)).run()
    assert not app.exception
    assert app.tabs[1].dataframe[-1].value.Performance.tolist() == [40, -80]
    by_label(app.get("button_group"), "Performance display").set_value("%").run()
    assert app.tabs[1].dataframe[-1].value.Performance.tolist() == [100, -100*2/3]
    by_label(app.selectbox, "Group by").set_value("taxonomy:labels").run()
    assert not app.exception
    assert set(app.tabs[1].dataframe[-1].value.Performance) == {-25}
