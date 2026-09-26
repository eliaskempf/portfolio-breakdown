from streamlit.testing.v1 import AppTest
import pytest

from portfolio_app.holdings import load_holdings
from portfolio_app.instruments import Instrument, catalog_search
from portfolio_app.search_widget import SEARCH_KEY
from test_ui import by_label


def launch_editor(path):
    return AppTest.from_string(
        "from pathlib import Path\n"
        "from portfolio_app.positions import read_snapshot\n"
        "from portfolio_app.position_ui import render_position_editor\n"
        f"path = Path({str(path)!r})\n"
        "render_position_editor(path, read_snapshot(path), [])\n"
    ).run()


def stub_search(monkeypatch):
    class Search:
        calls = []

        def __init__(self, cache_dir):
            pass

        def search(self, query):
            self.calls.append(query)
            if query == "offline":
                raise ConnectionError("unavailable")
            if query == "empty":
                return []
            return [Instrument("NVDA", "NVIDIA", "NASDAQ"), Instrument("NVD.DE", "NVIDIA", "XETRA")]

        def details(self, listing):
            if listing.ticker == "NVDA":
                return Instrument("NVDA", "NVIDIA", "NASDAQ", isin="US67066G1040", currency="USD")
            return listing

    monkeypatch.setattr("portfolio_app.instrument_ui.InstrumentSearch", Search)
    # AppTest does not drive component JavaScript. Exercise the Python boundary
    # with native buttons; browser smoke tests cover the real live component.
    def search_box(query, groups, message, selected=""):
        import streamlit as st

        st.caption(message)
        choice = None
        for group in groups:
            for listing in group["listings"]:
                st.caption(listing["exchange"])
                if st.button("Select " + listing["ticker"]):
                    choice = {"query": query, "ticker": listing["ticker"]}
        return choice

    monkeypatch.setattr("portfolio_app.instrument_ui.render_search_box", search_box)
    return Search


def search(app, query):
    app.session_state[SEARCH_KEY] = {"query": query}
    app.run()
    assert not app.exception


def test_search_fill_and_save_preserves_buy_in_currency_and_quantities(monkeypatch, tmp_path):
    provider = stub_search(monkeypatch)
    path = tmp_path / "holdings.csv"
    app = launch_editor(path)
    assert provider.calls == []
    by_label(app.number_input, "Quantity held (total)").set_value(2.5)
    by_label(app.text_input, "Buy-in currency").set_value("GBP")
    search(app, "Nvidia")
    assert any(item.value == "NASDAQ" for item in app.caption)
    by_label(app.button, "Select NVDA").click().run()
    assert not app.exception
    assert by_label(app.text_input, "Ticker").value == "NVDA"
    assert by_label(app.text_input, "ISIN (optional)").value == "US67066G1040"
    assert by_label(app.text_input, "Buy-in currency").value == "GBP"
    assert by_label(app.number_input, "Quantity held (total)").value == 2.5
    by_label(app.button, "Save position").click().run()
    assert not app.exception
    stored = load_holdings(path).iloc[0]
    assert stored["ticker"] == "NVDA"
    assert stored["isin"] == "US67066G1040"
    assert stored["shares"] == 2.5


def test_selection_without_isin_clears_previous_identifier(monkeypatch, tmp_path):
    stub_search(monkeypatch)
    app = launch_editor(tmp_path / "holdings.csv")
    search(app, "Nvidia")
    by_label(app.button, "Select NVDA").click().run()
    by_label(app.button, "Select NVD.DE").click().run()
    assert not app.exception
    assert by_label(app.text_input, "Ticker").value == "NVD.DE"
    assert by_label(app.text_input, "ISIN (optional)").value == ""
    assert any("ISIN unavailable" in item.value for item in app.success)
    by_label(app.text_input, "ISIN (optional)").set_value("US67066G1040").run()
    assert not app.exception


def test_existing_exact_listing_reuses_identity(monkeypatch, tmp_path):
    stub_search(monkeypatch)
    path = tmp_path / "holdings.csv"
    path.write_text("id,name,ticker,isin,shares,account\nexisting,NVIDIA,NVDA,US67066G1040,1,Demo A\n")
    app = launch_editor(path)
    search(app, "Nvidia")
    by_label(app.button, "Select NVDA").click().run()
    assert not app.exception
    assert by_label(app.selectbox, "Existing instrument").value == "existing"
    assert by_label(app.text_input, "Ticker").disabled
    by_label(app.text_input, "Account / broker").set_value("Demo B")
    by_label(app.number_input, "Quantity held (total)").set_value(2.)
    by_label(app.button, "Save position").click().run()
    assert not app.exception
    assert load_holdings(path)["id"].tolist() == ["existing", "existing"]


def test_search_does_not_replace_a_different_exchange_listing(monkeypatch, tmp_path):
    stub_search(monkeypatch)
    path = tmp_path / "holdings.csv"
    path.write_text("id,name,ticker,isin,shares\nxetra,NVIDIA,NVD.DE,US67066G1040,1\n")
    app = launch_editor(path)
    search(app, "Nvidia")
    by_label(app.button, "Select NVDA").click().run()
    assert by_label(app.selectbox, "Existing instrument").value == ""
    assert by_label(app.text_input, "Ticker").value == "NVDA"
    by_label(app.number_input, "Quantity held (total)").set_value(2.)
    by_label(app.button, "Save position").click().run()
    assert not app.exception
    assert load_holdings(path)["ticker"].tolist() == ["NVD.DE", "NVDA"]


def test_empty_or_failed_search_clears_old_results_and_keeps_manual_form(monkeypatch, tmp_path):
    stub_search(monkeypatch)
    app = launch_editor(tmp_path / "holdings.csv")
    for query in ("empty", "offline", "  "):
        search(app, "Nvidia")
        search(app, query)
        assert not any(item.label.startswith("Select ") for item in app.button)
        assert by_label(app.text_input, "Instrument name") is not None
    by_label(app.text_input, "Instrument name").set_value("Manual example")
    by_label(app.button, "Save position").click().run()
    assert not app.exception
    assert (tmp_path / "holdings.csv").exists()


def test_search_cache_filter_and_stale_selection(monkeypatch, tmp_path):
    provider = stub_search(monkeypatch)
    app = launch_editor(tmp_path / "holdings.csv")
    search(app, "Nvidia")
    search(app, "  NVIDIA  ")
    assert provider.calls == ["nvidia"]
    by_label(app.radio, "Search for").set_value("ETFs").run()
    assert not any(item.label.startswith("Select ") for item in app.button)
    assert provider.calls == ["nvidia"]
    by_label(app.radio, "Search for").set_value("All").run()
    monkeypatch.setattr("portfolio_app.instrument_ui.render_search_box", lambda *args: {"query": "old query", "ticker": "NVDA"})
    app.run()
    assert by_label(app.text_input, "Ticker").value == ""


def test_gold_search_filter_fills_and_saves_non_equity_security(monkeypatch, tmp_path):
    # Public listing metadata with an invented empty position in temporary storage.
    provider = stub_search(monkeypatch)
    monkeypatch.setattr(provider, "search", lambda self, query: catalog_search(query))
    path = tmp_path / "holdings.csv"
    app = launch_editor(path)
    search(app, "Euwax Gold 2")
    by_label(app.radio, "Search for").set_value("Equities").run()
    assert not any(item.label == "Select EWG2.SG" for item in app.button)
    by_label(app.radio, "Search for").set_value("ETCs").run()
    by_label(app.button, "Select EWG2.SG").click().run()
    assert not app.exception
    assert by_label(app.text_input, "Ticker").value == "EWG2.SG"
    assert by_label(app.text_input, "ISIN (optional)").value == "DE000EWG2LD7"
    assert by_label(app.selectbox, "Instrument type").value == "etc"
    assert by_label(app.selectbox, "Underlying exposure").value == "non_equity"
    by_label(app.button, "Save position").click().run()
    assert not app.exception
    stored = load_holdings(path).iloc[0]
    assert stored["instrument_type"] == "etc"
    assert stored["exposure_kind"] == "non_equity"
    assert stored["shares"] == 0


def test_selecting_fund_after_gold_resets_underlying_exposure(monkeypatch, tmp_path):
    provider = stub_search(monkeypatch)
    monkeypatch.setattr(provider, "search", lambda self, query: catalog_search(query))
    app = launch_editor(tmp_path / "holdings.csv")
    search(app, "EWG2.SG")
    by_label(app.button, "Select EWG2.SG").click().run()
    search(app, "VVSM.DE")
    by_label(app.button, "Select VVSM.DE").click().run()
    assert not app.exception
    assert by_label(app.selectbox, "Underlying exposure").value == "unknown"


@pytest.mark.parametrize('symbol', ['ETH', 'SOL'])
def test_crypto_search_offers_eur_and_fills_crypto_form(monkeypatch, tmp_path, symbol):
    provider = stub_search(monkeypatch)
    monkeypatch.setattr(provider, "search", lambda self, query: catalog_search(query))
    app = launch_editor(tmp_path / "holdings.csv")
    by_label(app.radio, "Search for").set_value("Crypto").run()
    search(app, symbol)
    choices = [item.label for item in app.button if item.label.startswith("Select ")]
    assert choices == [f"Select {symbol}-EUR", f"Select {symbol}-USD"]
    by_label(app.button, f"Select {symbol}-EUR").click().run()
    assert not app.exception
    assert by_label(app.text_input, "Ticker").value == f"{symbol}-EUR"
    assert by_label(app.text_input, "ISIN (optional)").value == ""
    assert by_label(app.selectbox, "Instrument type").value == "crypto"
    assert by_label(app.selectbox, "Underlying exposure").value == "non_equity"
