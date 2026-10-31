"""Public identifiers and invented positions; no working portfolio data."""
import pandas as pd

from portfolio_app.instruments import Instrument
from portfolio_app.transaction_matching import holding_matches, suggested_listing, instrument_fields


def test_crypto_pair_matches_stable_position_and_ambiguity_is_preserved():
    holdings = pd.DataFrame([
        dict(position_id='one', id='invented-id', name='Bitcoin', ticker='BTC-EUR', isin='', instrument_type='crypto'),
        dict(position_id='two', id='other-id', name='Bitcoin', ticker='BTC-USD', isin='', instrument_type='crypto'),
        dict(position_id='stock', id='stock-id', name='Invented company', ticker='BTC-X', isin='', instrument_type='equity'),
    ])
    assert holding_matches(' btc ', holdings) == ['one', 'two']
    assert holding_matches('Bitcoin', holdings) == ['one', 'two']
    assert holding_matches('BT', holdings) == []


def test_verified_crypto_pairs_prefer_reporting_currency_even_without_metadata():
    listings = [Instrument('BTC-USD', 'Bitcoin USD', kind='CRYPTOCURRENCY'),
                Instrument('BTC-GBP', 'Bitcoin GBP', kind='CRYPTOCURRENCY')]
    chosen = suggested_listing('BTC', listings, 'GBP')
    assert chosen.ticker == 'BTC-GBP'
    assert instrument_fields(chosen, 'BTC')['name'] == 'Bitcoin'
    assert instrument_fields(chosen, 'BTC')['instrument_type'] == 'crypto'
    assert suggested_listing('BTC', listings, 'EUR').ticker == 'BTC-USD'
    assert suggested_listing('BT', listings, 'GBP') is None


def test_ambiguous_equity_and_token_symbol_requires_selection():
    listings = [Instrument('ABC', 'Invented company'),
                Instrument('ABC-EUR', 'Invented token EUR', kind='CRYPTOCURRENCY')]
    assert suggested_listing('ABC', listings, 'EUR') is None
    assert suggested_listing('ABC', listings, 'EUR', kind='CRYPTOCURRENCY').ticker == 'ABC-EUR'


def test_holding_and_listing_labels_do_not_add_empty_or_currency_suffixes():
    from portfolio_app.transaction_matching import holding_label, listing_label
    assert holding_label({'name':'Bitcoin', 'account':''}) == 'Bitcoin'
    assert holding_label({'name':'Bitcoin', 'account':'Invented wallet'}) == 'Bitcoin · Invented wallet'
    listing = Instrument('LTC-EUR', 'Litecoin EUR', kind='CRYPTOCURRENCY')
    assert listing_label(listing) == 'LTC-EUR · Litecoin'
    assert instrument_fields(listing, 'LTC')['name'] == 'Litecoin'
