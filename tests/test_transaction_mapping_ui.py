"""Currency suggestions use public listings and explicitly invented holdings."""
import pytest
from streamlit.testing.v1 import AppTest

from portfolio_app.instruments import Instrument


def listing_key(app, asset):
    popup = next(x for x in app.get('popover') if x.proto.popover.help == 'Price listing · '+asset)
    return popup.proto.popover.id.split('-', 2)[2].removesuffix('_picker')


def listing_value(app, asset):
    return app.session_state[listing_key(app, asset)]


def set_listing(app, asset, ticker):
    # AppTest doesn't expose popover/component clicks; browser tests cover those.
    app.session_state[listing_key(app, asset)] = ticker
    return app.run()


def mapping_app(directory, currency):
    from pathlib import Path
    from portfolio_app.account_journal import Journal
    from portfolio_app.positions import read_snapshot
    from portfolio_app.transaction_csv import read_csv
    from portfolio_app.transaction_mapping_ui import render_mapping
    root = Path(directory)
    rows = read_csv('reference,date,type,asset,quantity,amount,currency\n'
                    'invented-a,2024-01-01,buy,ADA,1,10,EUR\n'
                    'invented-b,2024-01-01,buy,LTC,1,20,EUR\n')
    render_mapping(root, read_snapshot(root/'holdings.csv'), Journal(), rows,
                   'invented-import', currency=currency)


@pytest.mark.parametrize('currency', ['EUR', 'GBP'])
@pytest.mark.parametrize('available', [True, False])
def test_mixed_portfolio_fetches_preferred_currency_and_keeps_manual_choice(tmp_path, monkeypatch, currency, available):
    import portfolio_app.transaction_mapping_ui as ui
    (tmp_path/'holdings.csv').write_text(
        'id,name,ticker,instrument_type,shares\n'
        'invented-stock,Invented Company,INVENTED,equity,1\n'
        'invented-crypto,Invented Bitcoin holding,BTC-USD,crypto,1\n')
    queries = []
    def fetch(query, cache_dir, demo):
        queries.append(query)
        asset = query.split('-')[0]
        if '-' not in query:
            return [Instrument(asset+'-USD', asset+' USD', kind='CRYPTOCURRENCY')], ''
        return ([Instrument(query, asset+' '+currency, kind='CRYPTOCURRENCY')] if available else []), ''
    monkeypatch.setattr(ui, '_fetch', fetch)
    app = AppTest.from_function(mapping_app, args=(str(tmp_path), currency)).run()
    assert not app.exception
    assert sorted(queries) == sorted(['ADA', 'LTC', 'ADA-'+currency, 'LTC-'+currency])
    assert listing_value(app, 'ADA') == 'ADA-'+(currency if available else 'USD')
    set_listing(app, 'ADA', 'ADA-USD')
    assert not app.exception
    assert listing_value(app, 'ADA') == 'ADA-USD'
    assert len(queries) == 4  # Rerenders reuse both stages of the lookup.


def test_preferred_currency_does_not_resolve_ambiguous_assets_or_replace_existing_listing(tmp_path, monkeypatch):
    import portfolio_app.transaction_mapping_ui as ui
    (tmp_path/'holdings.csv').write_text(
        'id,name,ticker,instrument_type,shares\n'
        'invented-stock,Invented Company,INVENTED,equity,1\n'
        'invented-crypto,Invented Litecoin holding,LTC-USD,crypto,1\n')
    queries = []
    def fetch(query, cache_dir, demo):
        queries.append(query)
        if query == 'ADA':
            return [Instrument('ADA', 'Invented company'),
                    Instrument('ADA-USD', 'Cardano USD', kind='CRYPTOCURRENCY')], ''
        return [Instrument('ADA-EUR', 'Cardano EUR', kind='CRYPTOCURRENCY')], ''
    monkeypatch.setattr(ui, '_fetch', fetch)
    app = AppTest.from_function(mapping_app, args=(str(tmp_path), 'EUR')).run()
    assert not app.exception
    assert sorted(queries) == ['ADA', 'ADA-EUR']
    assert listing_value(app, 'ADA') == ''
    assert listing_value(app, 'LTC') == 'LTC-USD'


def test_existing_preferred_pair_and_unrelated_crypto_results_need_no_extra_lookup(tmp_path, monkeypatch):
    import portfolio_app.transaction_mapping_ui as ui
    (tmp_path/'holdings.csv').write_text(
        'id,name,ticker,instrument_type,shares\n'
        'invented-stock,Invented Company,INVENTED,equity,1\n')
    queries = []
    def fetch(query, cache_dir, demo):
        queries.append(query)
        if query == 'ADA':
            return [Instrument('ADA-EUR', 'Cardano EUR', kind='CRYPTOCURRENCY')], ''
        return [Instrument('UNRELATED-USD', 'Invented unrelated token', kind='CRYPTOCURRENCY')], ''
    monkeypatch.setattr(ui, '_fetch', fetch)
    app = AppTest.from_function(mapping_app, args=(str(tmp_path), 'EUR')).run()
    assert not app.exception
    assert sorted(queries) == ['ADA', 'LTC']
    assert listing_value(app, 'ADA') == 'ADA-EUR'
    assert listing_value(app, 'LTC') == ''
