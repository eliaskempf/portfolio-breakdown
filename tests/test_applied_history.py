"""Applied ledgers are authoritative; invented inputs still expose real evidence gaps."""
from dataclasses import replace
from datetime import date

import pytest

from portfolio_app.account_journal import Journal, load_journal, save_journal
from portfolio_app.history_commands import book_for, save_history
from portfolio_app.history_observations import (
    CoverageReview, HistoryScope, Observation, ObservationStore, load_observations, save_observations,
)
from portfolio_app.history_view import history_view
from portfolio_app.personal_performance import calculate_history
from portfolio_app.positions import read_snapshot
from portfolio_app.security_history import HistoryReview, SecurityEvent as E, effective_holdings


def applied_position(directory):
    path = directory/'holdings.csv'
    path.write_text('position_key,id,name,shares,acquisition_price,acquisition_currency\n'
                    'holding,SYNTH,Invented stock,2,10,EUR\n')
    snapshot = read_snapshot(path)
    records = (E('opening', 'opening', '2024-01-01'),
               E('purchase', 'buy', '2024-01-02', quantity=1, price=10, currency='EUR', charges_known=True))
    book = replace(book_for(Journal(), snapshot.holdings.iloc[0]), records=records,
                   active_ids=tuple(e.id for e in records))
    journal = save_history(directory, book, activate=True, accept_balance_change=True,
                          expected_journal_revision=None, expected_holdings_revision=snapshot.revision)
    scope = HistoryScope((book.position_id,))
    store = ObservationStore(tuple(Observation(f'price-{n}', 'price', book.position_id, stamp, value, 'EUR')
        for n,(stamp,value) in enumerate((('2024-01-02',10),('2025-01-02',13),('2025-01-03',14)))))
    return journal, scope, store


def test_applying_one_share_history_replaces_two_share_position_and_enables_gain(tmp_path):
    journal, scope, store = applied_position(tmp_path)
    rows = effective_holdings(read_snapshot(tmp_path/'holdings.csv').holdings, journal.securities)
    assert rows.shares.tolist() == [1]
    assert not journal.securities[0].reviews[-1].complete_from
    result = calculate_history(journal, scope, store, '2024-01-01', '2025-01-02')
    assert result.ending_value == 13 and result.net_contributions == 10 and result.gain == 3
    assert result.time_weighted == pytest.approx(.3) and result.money_weighted is not None


def test_later_prices_and_new_periods_need_no_confirmation(tmp_path):
    journal, scope, store = applied_position(tmp_path)
    for period in ('1M','1Y','All'):
        view = history_view(journal, scope, store, period, today=date(2025,1,3))
        assert view.result.gain is not None and view.all_result.money_weighted is not None
        assert not view.result.flow_reasons
    assert history_view(journal,scope,store,'All',today=date(2025,1,3)).all_result.total_gain == 4


def test_legacy_confirmation_metadata_is_preserved_but_never_expires_metrics(tmp_path):
    journal, scope, store = applied_position(tmp_path)
    book = journal.securities[0]
    legacy = HistoryReview('old',book.active_ids,'2024-01-02','accept','2024-01-01','2024-01-02','0'*64)
    historical = replace(journal,securities=(replace(book,reviews=(*book.reviews,legacy)),))
    store = replace(store,reviews=(CoverageReview('old',scope,'2024-01-01','2024-01-02','0'*64),))
    save_journal(tmp_path,historical,load_journal(tmp_path)[1])
    save_observations(tmp_path,store,None)
    saved = {p.name:p.read_bytes() for p in (tmp_path/'account-journal.json',tmp_path/'history-observations.json')}
    loaded,observations = load_journal(tmp_path)[0],load_observations(tmp_path)[0]
    result = calculate_history(loaded,scope,observations,'2024-01-01','2025-01-03')
    assert result.gain == 4 and result.money_weighted is not None
    assert loaded == historical and observations == store
    assert all((tmp_path/name).read_bytes() == content for name,content in saved.items())


@pytest.mark.parametrize('gap',['price','fees','inactive'])
def test_removing_confirmation_does_not_hide_real_input_gaps(tmp_path,gap):
    journal,scope,store = applied_position(tmp_path)
    book=journal.securities[0]
    if gap=='price':
        store=replace(store,observations=store.observations[:1])
    elif gap=='fees':
        journal=replace(journal,securities=(replace(book,records=(book.records[0],replace(book.records[1],charges_known=False))),))
    else:
        journal=replace(journal,securities=(replace(book,active_ids=()),))
    result=calculate_history(journal,scope,store,'2024-01-01','2025-01-03')
    assert result.gain is None and result.money_weighted is None and result.time_weighted is None
    assert result.value_reasons or result.flow_reasons
