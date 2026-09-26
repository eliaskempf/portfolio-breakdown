import pandas as pd
import shutil
from types import SimpleNamespace

import pytest

from portfolio_app.holdings import load_holdings
from portfolio_app.purchases import HISTORY_COLUMN, read_purchase_history
from test_instrument_ui import launch_editor, search, stub_search
from test_ui import by_label, launch

CSV = "date,shares,price,fees\n2026-01-01,2,100,1\n2026-02-01,3,120,2\n"


def bulk(app):
    by_label(app.radio, "Position action").set_value("Bulk add purchases").run()
    assert not app.exception
    return app


def paste(app, content=CSV):
    by_label(app.radio, "Purchase input").set_value("Paste CSV / TSV").run()
    by_label(app.text_area, "Paste purchases with headers").set_value(content).run()
    assert not app.exception


def test_create_bulk_position_preview_save_reopen_and_history(tmp_path):
    path = tmp_path / "holdings.csv"
    app = bulk(launch_editor(path))
    by_label(app.text_input, "Instrument name").set_value("Synthetic company")
    by_label(app.text_input, "Ticker").set_value("demo")
    by_label(app.text_input, "Account / broker").set_value("Demo A")
    paste(app)
    preview = app.table[0].value.set_index("Measure")["Value"]
    assert preview["Shares after save"] == "5"
    assert preview["Average buy-in after save"] == "112.600000 EUR"
    assert not path.exists()
    by_label(app.button, "Save purchase batch").click().run()
    assert not app.exception
    assert any("Saved 2 purchases" in item.value for item in app.success)
    assert not app.text_area
    reopened = launch_editor(path)
    by_label(reopened.radio, "Position action").set_value("Edit position").run()
    assert not reopened.exception
    assert any(item.label == "Saved purchase batches" for item in reopened.expander)
    assert by_label(reopened.number_input, "Quantity held (total)").value == 5
    assert HISTORY_COLUMN not in [item.label for item in reopened.text_input]


def test_existing_position_historical_cost_entry_keeps_shares(tmp_path):
    path = tmp_path / "holdings.csv"
    path.write_text("id,name,ticker,shares\nexample,Synthetic,DEMO,5\n")
    app = bulk(launch_editor(path))
    by_label(app.selectbox, "Purchase destination").set_value("position-0").run()
    by_label(app.radio, "How to apply purchases").set_value("Calculate buy-in for shares already held").run()
    paste(app)
    by_label(app.button, "Save purchase batch").click().run()
    assert not app.exception
    stored = load_holdings(path).iloc[0]
    assert stored["shares"] == 5
    assert stored["acquisition_price"] == 112.6
    assert read_purchase_history(stored[HISTORY_COLUMN])[0]["mode"] == "reconcile"


def test_invalid_batch_or_currency_never_offers_save(tmp_path):
    path = tmp_path / "holdings.csv"
    path.write_text("id,name,ticker,shares,acquisition_price,acquisition_currency\nexample,Synthetic,DEMO,5,80,USD\n")
    app = bulk(launch_editor(path))
    by_label(app.selectbox, "Purchase destination").set_value("position-0").run()
    by_label(app.text_input, "Purchase currency").set_value("EUR")
    paste(app)
    assert any("must match" in item.value for item in app.error)
    assert not any(item.label == "Save purchase batch" for item in app.button)
    by_label(app.text_input, "Purchase currency").set_value("USD")
    paste(app, "shares,price\n-1,2")
    assert app.error
    assert not any(item.label == "Save purchase batch" for item in app.button)
    assert load_holdings(path).iloc[0]["shares"] == 5


def test_unknown_opening_cost_is_visible_in_preview_and_stays_unknown(tmp_path):
    path = tmp_path / "holdings.csv"
    path.write_text("id,name,ticker,shares\nexample,Synthetic,DEMO,5\n")
    app = bulk(launch_editor(path))
    by_label(app.selectbox, "Purchase destination").set_value("position-0").run()
    paste(app)
    assert any("remain unknown" in item.value for item in app.warning)
    by_label(app.button, "Save purchase batch").click().run()
    assert not app.exception
    row = load_holdings(path).iloc[0]
    assert row["shares"] == 10
    assert pd.isna(row["acquisition_price"])


def test_changed_holdings_require_reload_before_saving_bulk(tmp_path):
    path = tmp_path / "holdings.csv"
    path.write_text("id,name,ticker,shares\nexample,Synthetic,DEMO,5\n")
    app = bulk(launch_editor(path))
    by_label(app.selectbox, "Purchase destination").set_value("position-0").run()
    paste(app)
    newer = "id,name,ticker,shares\nexample,Synthetic,DEMO,6\n"
    path.write_text(newer)
    by_label(app.button, "Save purchase batch").click().run()
    assert not app.exception
    assert any("Holdings changed" in item.value for item in app.warning)
    assert path.read_text() == newer
    by_label(app.button, "Reload position form").click().run()
    assert not app.exception


def test_bulk_search_can_fill_new_instrument(monkeypatch, tmp_path):
    stub_search(monkeypatch)
    app = bulk(launch_editor(tmp_path / "holdings.csv"))
    search(app, "Nvidia")
    by_label(app.button, "Select NVDA").click().run()
    assert not app.exception
    assert by_label(app.text_input, "ISIN (optional)").value == "US67066G1040"
    assert by_label(app.text_input, "Purchase currency").value == "EUR"
    paste(app)
    by_label(app.button, "Save purchase batch").click().run()
    assert not app.exception
    assert load_holdings(tmp_path / "holdings.csv").iloc[0]["ticker"] == "NVDA"


def test_table_editor_can_save_fractional_purchases(tmp_path):
    app = bulk(launch_editor(tmp_path / "holdings.csv"))
    by_label(app.text_input, "Instrument name").set_value("Synthetic table input").run()
    key = next(key for key in app.session_state.filtered_state if key.endswith("_rows"))
    delta = {
        "edited_rows": {0: {"shares": "0.25", "price": "12", "fees": "0.5"}},
        "added_rows": [{"shares": "0.75", "price": "8", "fees": "0.5"}], "deleted_rows": [],
    }
    app.session_state[key] = delta
    app.run()
    assert not app.exception
    # AppTest has no data-editor widget driver; resend the browser's edit state
    # with the button event, as the real client does.
    app.session_state[key] = delta
    by_label(app.button, "Save purchase batch").click().run()
    assert not app.exception
    stored = load_holdings(tmp_path / "holdings.csv").iloc[0]
    assert stored["shares"] == 1
    assert stored["acquisition_price"] == 10


def test_repeat_import_requires_checkbox(tmp_path):
    path = tmp_path / "holdings.csv"
    app = bulk(launch_editor(path))
    by_label(app.text_input, "Instrument name").set_value("Synthetic")
    paste(app)
    by_label(app.button, "Save purchase batch").click().run()
    bulk(app)
    by_label(app.selectbox, "Purchase destination").set_value("position-0").run()
    paste(app)
    assert by_label(app.button, "Save purchase batch").disabled
    by_label(app.checkbox, "These are additional purchases despite matching a saved batch").check().run()
    assert not by_label(app.button, "Save purchase batch").disabled
    by_label(app.button, "Save purchase batch").click().run()
    assert not app.exception
    assert load_holdings(path).iloc[0]["shares"] == 10


def test_bulk_in_full_app_keeps_history_out_of_allocation_dimensions(tmp_path, sample_data_dir):
    shutil.copy(sample_data_dir / "demo_prices.json", tmp_path)
    app = bulk(launch(tmp_path))
    by_label(app.text_input, "Instrument name").set_value("Synthetic unpriced position")
    paste(app)
    by_label(app.button, "Save purchase batch").click().run()
    assert not app.exception
    assert len(app.dataframe[-1].value) == 1
    assert HISTORY_COLUMN not in app.dataframe[-1].value.columns
    assert all("Purchase History" != item.label for item in app.multiselect)


@pytest.mark.parametrize("content,error", [(CSV.encode(), None), (b"\xff", "UTF-8"), (b"x" * 1_000_001, "too large")])
def test_upload_validates_and_saves_only_valid_input(monkeypatch, tmp_path, content, error):
    upload = SimpleNamespace(size=len(content), getvalue=lambda: content)
    monkeypatch.setattr("portfolio_app.purchase_ui.st.file_uploader", lambda *args, **kwargs: upload)
    path = tmp_path / "holdings.csv"
    app = bulk(launch_editor(path))
    by_label(app.text_input, "Instrument name").set_value("Synthetic upload")
    by_label(app.radio, "Purchase input").set_value("Upload CSV").run()
    assert not app.exception
    if error:
        assert any(error in item.value for item in app.error)
        assert not path.exists()
    else:
        by_label(app.button, "Save purchase batch").click().run()
        assert not app.exception
        assert load_holdings(path).iloc[0]["shares"] == 5
