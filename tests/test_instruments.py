from types import SimpleNamespace

import pytest

from portfolio_app.instruments import Instrument, InstrumentSearch, catalog_search, merge_search_results, normalize_results, parse_isin_candidates, result_groups, valid_isin


@pytest.mark.parametrize("value,expected", [
    (" us67066g1040 ", "US67066G1040"), ("DE000ENER6Y0", "DE000ENER6Y0"),
    ("IE00BMC38736", "IE00BMC38736"), ("US67066G1041", ""),
    ("-", ""), (None, ""), ("not an isin", ""),
])
def test_isin_validation(value, expected):
    assert valid_isin(value) == expected


def test_search_retains_distinct_listings_and_excludes_other_asset_types():
    results = normalize_results([
        {"symbol": " nvda ", "longname": "NVIDIA", "quoteType": "EQUITY", "exchDisp": "NASDAQ"},
        {"symbol": "NVDA", "shortname": "Duplicate", "quoteType": "EQUITY"},
        {"symbol": "NVD.DE", "shortname": "NVIDIA", "quoteType": "EQUITY"},
        {"symbol": "VVSM.DE", "quoteType": "ETF"},
        {"symbol": "BTC-USD", "quoteType": "CRYPTOCURRENCY"},
        {"symbol": "NVDA-option", "quoteType": "OPTION"},
        {"quoteType": "EQUITY"}, None,
    ])
    assert [item.ticker for item in results] == ["NVDA", "NVD.DE", "VVSM.DE", "BTC-USD"]
    assert "NASDAQ" in results[0].label


def test_search_blank_does_not_call_network_and_isin_query_fills_identifier(monkeypatch, tmp_path):
    calls = []

    def search(query, **kwargs):
        calls.append(query)
        assert kwargs["news_count"] == 0
        assert kwargs["timeout"] == 10
        return SimpleNamespace(quotes=[{"symbol": "ENR.DE", "shortname": "Siemens Energy", "quoteType": "EQUITY"}])

    monkeypatch.setattr("portfolio_app.instruments.yf.Search", search)
    provider = InstrumentSearch(tmp_path)
    assert provider.search("  ") == []
    assert calls == []
    assert provider.search("DE000ENER6Y0")[0].isin == "DE000ENER6Y0"


@pytest.mark.parametrize("reverse_symbol,expected", [("NVDA", "US67066G1040"), ("NVD.DE", "")])
def test_details_require_exact_listing_reverse_isin_match(monkeypatch, tmp_path, reverse_symbol, expected):
    ticker = SimpleNamespace(get_info=lambda: {"currency": "USD"}, get_isin=lambda: "US67066G1040")
    monkeypatch.setattr("portfolio_app.instruments.yf.Ticker", lambda symbol: ticker)
    monkeypatch.setattr("portfolio_app.instruments.lookup_isin_candidates", lambda query: ["US67066G1040"])
    provider = InstrumentSearch(tmp_path)
    monkeypatch.setattr(provider, "search", lambda query: [Instrument(reverse_symbol, "NVIDIA")])
    result = provider.details(Instrument("NVDA", "NVIDIA"))
    assert result.isin == expected
    assert result.currency == "USD"


def test_optional_metadata_failure_keeps_search_result(monkeypatch, tmp_path):
    def fail(*args):
        raise ConnectionError("offline")

    monkeypatch.setattr("portfolio_app.instruments.yf.Ticker", lambda symbol: SimpleNamespace(get_info=fail, get_isin=fail))
    monkeypatch.setattr("portfolio_app.instruments.lookup_isin_candidates", fail)
    listing = Instrument("ENR.DE", "Siemens Energy")
    assert InstrumentSearch(tmp_path).details(listing) == listing


def test_search_failure_is_not_silently_an_empty_result(monkeypatch, tmp_path):
    def fail(*args, **kwargs):
        raise ConnectionError("offline")

    monkeypatch.setattr("portfolio_app.instruments.yf.Search", fail)
    with pytest.raises(ConnectionError):
        InstrumentSearch(tmp_path).search("Example")


def test_isin_suggestions_ignore_bonds_invalid_identifiers_and_duplicates():
    payload = ('mmSuggestDeliver(0, new Array("Name", "Category", "Keywords"), '
               'new Array(new Array("Example", "Stocks", "NVDA|US67066G1040|NVDA"),'
               'new Array("Duplicate", "Stocks", "|US67066G1040|"),'
               'new Array("Example bond", "Bonds", "US8740391003"),'
               'new Array("Malformed", "Stocks", "invalid"),'
               'new Array("Example ETF", "ETFs", "IE00BMC38736")))')
    assert parse_isin_candidates(payload) == ["US67066G1040", "IE00BMC38736"]
    assert parse_isin_candidates("unexpected response") == []


@pytest.mark.parametrize("query", ["vaneck", "Van Eck", "semiconductors", "semiconductor", "van eck semiconductor ucits", "VVSM", "IE00BMC38736", "chips", "semi"])
def test_supported_ucits_fund_is_discoverable_by_keywords(query):
    results = catalog_search(query)
    assert any(item.ticker == "VVSM.DE" and item.isin == "IE00BMC38736" for item in results)
    groups = result_groups(results)
    fund = next(group for group in groups if group["isin"] == "IE00BMC38736")
    assert fund["ucits"]
    assert len([group for group in groups if group["isin"] == "IE00BMC38736"]) == 1


def test_exact_us_ticker_remains_distinct_from_ucits_listings():
    us = Instrument("SMH", "VanEck Semiconductor ETF", "NASDAQ", "ETF")
    results = merge_search_results("SMH", catalog_search("smh"), [us, Instrument("VVSM.DE", "Abbreviated name", "XETRA", "ETF")])
    assert results[0] == us
    fund = next(item for item in results if item.ticker == "VVSM.DE")
    assert fund.isin == "IE00BMC38736"
    assert fund.currency == "EUR"
    assert sum(item.ticker == "VVSM.DE" for item in results) == 1


def test_catalog_survives_provider_failure(monkeypatch, tmp_path):
    def fail(*args, **kwargs):
        raise ConnectionError("offline")

    monkeypatch.setattr("portfolio_app.instruments.yf.Search", fail)
    provider = InstrumentSearch(tmp_path)
    assert provider.search("vaneck")[0].ticker == "VVSM.DE"
    assert "unavailable" in provider.last_error


def test_punctuation_and_short_queries_do_not_match_every_listing():
    assert catalog_search("...") == []
    assert catalog_search("v") == []


@pytest.mark.parametrize("query", ["DE000EWG2LD7", "EWG2LD", "EWG2.SG", "Euwax Gold II", "Euwax Gold 2"])
def test_gold_etc_catalog_identifiers_and_alias_survive_outage(monkeypatch, tmp_path, query):
    def fail(*args, **kwargs):
        raise ConnectionError("offline")

    monkeypatch.setattr("portfolio_app.instruments.yf.Search", fail)
    results = InstrumentSearch(tmp_path).search(query)
    assert len(results) == 1
    listing = results[0]
    assert (listing.ticker, listing.isin, listing.currency, listing.kind) == ("EWG2.SG", "DE000EWG2LD7", "EUR", "ETC")
    assert result_groups(results)[0]["kind"] == "ETC"


def test_verified_gold_etc_overrides_provider_mutualfund_category(monkeypatch, tmp_path):
    quotes = [
        {"symbol": "EWG2.SG", "shortname": "EUWAX Gold II", "quoteType": "MUTUALFUND"},
        {"symbol": "INVENTED-FUND", "shortname": "EUWAX Gold II", "quoteType": "MUTUALFUND"},
        {"symbol": "INVENTED-OPTION", "quoteType": "OPTION"},
    ]
    monkeypatch.setattr("portfolio_app.instruments.yf.Search", lambda *args, **kwargs: SimpleNamespace(quotes=quotes))
    # No catalog keyword match: normalization must recognize the exact listing
    # without treating arbitrary funds or similarly named securities as gold.
    results = InstrumentSearch(tmp_path).search("precious metals")
    assert [item.ticker for item in results] == ["EWG2.SG"]
    assert results[0].kind == "ETC"


@pytest.mark.parametrize("symbol,name", [("ETH", "Ethereum"), ("BTC", "Bitcoin"), ("SOL", "Solana")])
@pytest.mark.parametrize("by_name", [False, True])
def test_crypto_search_includes_eur_when_provider_only_returns_usd(monkeypatch, tmp_path, symbol, name, by_name):
    quotes = [{"symbol": f"{symbol}-USD", "shortname": f"{name} USD", "quoteType": "CRYPTOCURRENCY"}]
    monkeypatch.setattr("portfolio_app.instruments.yf.Search", lambda *args, **kwargs: SimpleNamespace(quotes=quotes))
    results = InstrumentSearch(tmp_path).search(name if by_name else symbol)
    assert [item.ticker for item in results] == [f"{symbol}-EUR", f"{symbol}-USD"]
    assert all(item.kind == "CRYPTOCURRENCY" and not item.isin for item in results)
    assert [item.currency for item in results] == ["EUR", "USD"]


def test_explicit_crypto_currency_and_token_identity_are_preserved():
    assert [item.ticker for item in catalog_search("ETH-USD")] == ["ETH-USD"]
    assert catalog_search("Ethereum Classic") == []
    groups = result_groups(catalog_search("ETH"))
    assert len(groups) == 2
    assert [group["listings"][0]["currency"] for group in groups] == ["EUR", "USD"]
