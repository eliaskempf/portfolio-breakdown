"""Invented securities, accounts and amounts; never read working portfolio data."""
from dataclasses import replace
from decimal import Decimal as D
import pytest

from portfolio_app.account_journal import Account, Event, Journal, replay, journal_from_dict, journal_to_dict
from portfolio_app.holdings import DataError, parse_holdings
from portfolio_app.security_history import SecurityBook, SecurityEvent as E, Charge, replay_securities, effective_holdings


def book(*events, account=''):
    return SecurityBook('position-0', 'invented', ('10', '8', 'EUR'), account,
                        events, tuple(e.id for e in events))


def opening(quantity='10', cost='80'):
    return E('open', 'opening', '2024-01-01', quantity=D(quantity), amount=D(cost), currency='EUR')


def test_duplicate_candidates_distinguish_recorded_execution_times():
    from portfolio_app.security_history import duplicate_candidates
    first = E('first', 'asset_fee', '2024-02-01', quantity=D('0.01'),
              timestamp_utc='2024-02-01T08:00:00Z')
    later = replace(first, id='later', sequence=1, timestamp_utc='2024-02-01T09:00:00Z')
    repeated = replace(first, id='repeated', sequence=2)
    assert duplicate_candidates(book(first, later)) == ()
    assert duplicate_candidates(book(first, later, repeated)) == (('first', 'repeated'),)


@pytest.mark.parametrize('timestamp_first', [False, True])
def test_duplicate_candidates_still_match_date_only_and_timestamped_evidence(timestamp_first):
    from portfolio_app.security_history import duplicate_candidates
    manual = E('manual', 'buy', '2024-02-01', quantity=D(2), amount=D(20), currency='EUR')
    imported = replace(manual, id='imported', timestamp_utc='2024-02-01T08:00:00Z')
    events = (imported, manual) if timestamp_first else (manual, imported)
    assert duplicate_candidates(book(*events)) == ((events[0].id, events[1].id),)


def test_fifo_unknown_opening_partial_sale_does_not_invent_lots():
    b = book(opening(), E('buy', 'buy', '2024-02-01', quantity=D(5), price=D(20), currency='EUR', charges_known=True),
             E('sale', 'sell', '2024-03-01', quantity=D(4), price=D(30), currency='EUR', charges_known=True))
    state = replay_securities((b,)).positions[b.position_id]
    assert state.quantity == 11
    assert state.lots[0].costs[0]['amount'] == ''
    assert state.lots[1].costs[0]['amount'] == '100'


def test_fifo_purchases_include_fees_and_dispose_oldest_cost():
    b = book(opening('0', '0'), E('a', 'buy', '2024-02-01', quantity=D('2.5'), price=D(10), currency='EUR',
        charges=(Charge(D(1), 'EUR'),), charges_known=True),
        E('b', 'buy', '2024-03-01', quantity=D(2), price=D(30), currency='EUR', charges_known=True),
        E('s', 'sell', '2024-04-01', quantity=D(3), price=D(40), currency='EUR', charges_known=True))
    state = replay_securities((b,)).positions[b.position_id]
    assert state.quantity == D('1.5')
    assert sum(D(p['amount']) for p in state.costs) == 45
    assert len(state.lots) == 1


def test_cash_posted_once_after_existing_cash_snapshot_and_unknown_is_not_zero():
    b = book(opening(), E('a', 'buy', '2024-02-01', quantity=D(2), price=D(10), currency='EUR', charges_known=True), account='a')
    j = Journal(version=2, accounts=(Account('a', 'Invented'),), securities=(b,),
                events=(Event('cash', '2024-01-01', 'cash_open', 'a', 'EUR', D(100)),))
    assert replay(j).cash['a', 'EUR'] == 80
    assert len(j.events) == 1
    unknown = replace(b, records=(*b.records, E('missing', 'buy', '2024-03-01', quantity=D(1), currency='EUR')),
                      active_ids=(*b.active_ids, 'missing'))
    state = replay(replace(j, securities=(unknown,)))
    assert ('a', 'EUR') in state.unknown_cash
    with pytest.raises(DataError, match='incomplete'):
        state.available_cash('a', 'EUR')
    assert journal_from_dict(journal_to_dict(j)) == j


def test_evidence_does_not_change_balances_and_opening_does_not_post_cash():
    b = replace(book(opening()), active_ids=())
    assert not replay_securities((b,)).positions
    assert not replay_securities((replace(b, active_ids=('open',)),)).cash


def test_materialization_preserves_source_and_rejects_external_change():
    rows = parse_holdings('id,name,shares,acquisition_price,acquisition_currency\ninvented,Invented,10,8,EUR\n')
    b = book(opening(), E('a', 'buy', '2024-02-01', quantity=D(2), price=D(10), currency='EUR', charges_known=True))
    effective = effective_holdings(rows, (b,))
    assert effective.iloc[0].shares == 12
    assert rows.iloc[0].shares == 10
    rows.loc[0, 'shares'] = 11
    with pytest.raises(DataError, match='outside History'):
        effective_holdings(rows, (b,))


def test_split_preserves_total_cost_and_oversale_rejected():
    b = book(opening(), E('split', 'split', '2024-02-01', quantity=D(2)))
    state = replay_securities((b,)).positions[b.position_id]
    assert state.quantity == 20
    assert sum(D(p['amount']) for p in state.costs) == 80
    with pytest.raises(DataError, match='exceeds'):
        replay_securities((book(opening(), E('s', 'sell', '2024-02-01', quantity=D(11), currency='EUR')),))


def test_internal_security_transfer_preserves_fifo_lots_without_cash():
    source=book(opening('0','0'),E('buy','buy','2024-02-01',quantity=D(4),price=D(20),currency='EUR',charges_known=True),
        E('move','transfer','2024-03-01',quantity=D(1),other_position_id='destination'))
    dest=SecurityBook('destination','invented',('0','',''),records=(E('dest-open','opening','2024-01-01'),),active_ids=('dest-open',))
    result=replay_securities((source,dest))
    assert result.positions['position-0'].quantity==3
    assert result.positions['destination'].quantity==1
    assert D(result.positions['destination'].costs[0]['amount'])==20
    assert len(result.cash)==1


def test_closed_position_and_reopening_keep_evidence_and_new_lot_cost():
    b=book(opening(),E('sell','sell','2024-02-01',quantity=D(10),price=D(12),currency='EUR',charges_known=True),
        E('new','buy','2024-03-01',quantity=D(2),price=D(15),currency='EUR',charges_known=True))
    state=replay_securities((b,),as_of='2024-02-02').positions[b.position_id]
    assert state.quantity==0 and not state.lots
    reopened=replay_securities((b,)).positions[b.position_id]
    assert reopened.quantity==2 and reopened.costs[0]['amount']=='30'


def test_corrected_trade_preserves_pending_receipt_reference():
    from portfolio_app.security_history import append_evidence
    b=book(opening(),E('buy','buy','2024-02-01',quantity=D(1),price=D(10),currency='EUR',charges_known=True,value_date='2024-02-03'),account='a')
    j=Journal(version=2,accounts=(Account('a','Invented'),),securities=(b,),events=(
        Event('cash','2024-01-01','cash_open','a','EUR',D(100)),
        Event('paid','2024-02-03','receive','a',reference='security:buy')))
    assert replay(j).cash['a','EUR']==90
    corrected=replace(b.records[-1],id='corrected',price=D(12),replaces='buy')
    b=replace(append_evidence(b,(corrected,)),active_ids=('open','corrected'))
    state=replay(replace(j,securities=(b,)))
    assert state.cash['a','EUR']==88 and not state.pending


def test_future_settlement_date_is_permitted_for_executed_trade():
    e=E('buy','buy','2024-02-01',quantity=D(1),price=D(10),currency='EUR',value_date='2099-01-01')
    assert e.value_date=='2099-01-01'
