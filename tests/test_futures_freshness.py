"""Synthetic quote selection, accounting invariants, persistence and concurrency."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from threading import Event

import pytest
from portfolio_app import account_journal as ledger
from portfolio_app.account_observations import bind_mark, current_marks
from portfolio_app.actual_portfolio import journal_positions
from portfolio_app.automatic_settlements import apply_archived_settlements
from portfolio_app.derivative_market import make_contract
from portfolio_app.derivative_types import PriceObservation
from portfolio_app.futures_clock import business_date
from portfolio_app.futures_quotes import QuoteCollector, latest_quotes, read_quotes, record_result
from portfolio_app.futures_valuation import select_valuations, freshness
from portfolio_app.holdings import DataError
from portfolio_app.settlement_archive import SettlementArchive
from portfolio_app.valuation import value_holdings
from portfolio_app.positions import read_snapshot
from test_actual_portfolio import invented

NOW = datetime(2026, 1, 7, 12, tzinfo=timezone.utc)


def quote(contract, *, price=990, stamp='2026-01-07T11:45:00+00:00', retrieved=None):
    return PriceObservation(contract.id, business_date(datetime.fromisoformat(stamp)).isoformat(), price,
        kind='mark', source='Invented exact-contract feed', quote_at=stamp, delay_minutes=15,
        observed_at=(retrieved or NOW).isoformat())


def setup(tmp_path, product='FMWN', sign=1):
    service, journal, _ = invented(tmp_path)
    contract = make_contract(product, '2026-03-20')
    journal = replace(journal, contracts=(contract,), cash_links=(),
        positions=(replace(journal.positions[0], contract_id=contract.id),),
        events=tuple(replace(e, currency=contract.currency, quantity=2*sign if e.kind == 'trade' else e.quantity)
                     for e in journal.events))
    ledger.save_journal(tmp_path, journal, None)
    return service, journal, contract


@pytest.mark.parametrize('product', ['FMWN', 'FMWO', 'FMWB'])
@pytest.mark.parametrize('sign', [1, -1])
def test_market_fall_changes_value_exposure_not_cash_then_settlement_once(tmp_path, product, sign):
    service, journal, contract = setup(tmp_path, product, sign)
    before = ledger.replay(journal)
    old = PriceObservation(contract.id, '2026-01-06', 1010, source='Invented settlement')
    marks = select_valuations(journal, automatic={contract.id:quote(contract)}, settlements={contract.id:[old]}, now=NOW)
    assert marks['p'].observation.price == 990 and marks['p'].status == 'delayed'
    assert marks['p'].label == '' and 'Quote time' in marks['p'].detail
    valued = value_holdings(read_snapshot(tmp_path/'holdings.csv').holdings, service)
    rates = {(contract.currency,'EUR'):dict(rate='1')} if contract.currency != 'EUR' else {}
    result = journal_positions(valued, journal, service, valuations=marks, fx_rates=rates)
    position = result.set_index('position_id').loc['p']
    assert position.current_value_native == -20*sign*float(contract.multiplier)
    assert position.signed_notional_reporting == 2*sign*990*float(contract.multiplier)
    assert ledger.replay(journal) == before
    total = before.cash['a',contract.currency] + before.positions['p'].unsettled_pnl(990)
    archive = SettlementArchive(tmp_path)
    archive.record(contract, old)
    with ThreadPoolExecutor(max_workers=2) as pool:
        counts = list(pool.map(lambda _: apply_archived_settlements(tmp_path, today=NOW.date()), range(2)))
    assert sorted(counts) == [0,1]
    posted, _ = ledger.load_journal(tmp_path)
    after = ledger.replay(posted)
    assert after.cash['a',contract.currency] + after.positions['p'].unsettled_pnl(990) == total
    assert select_valuations(posted, automatic={contract.id:quote(contract)}, now=NOW)['p'].observation.price == 990


def test_settlement_reference_order_and_manual_same_day_precedence(tmp_path):
    _, journal, contract = setup(tmp_path)
    settlement = PriceObservation(contract.id,'2026-01-06',1010,source='Invented settlement')
    late = quote(contract,stamp='2026-01-06T18:00:00+00:00')
    early = quote(contract,stamp='2026-01-06T15:00:00+00:00')
    for observed, expected in [(early,1010),(late,990)]:
        selected = select_valuations(journal,automatic={contract.id:observed},settlements={contract.id:[settlement]},now=NOW)
        assert selected['p'].observation.price == expected
    manual = bind_mark(journal,'p',PriceObservation(contract.id,'2026-01-07',995,kind='manual',source='Invented manual'))
    assert select_valuations(journal,{'p':manual},{contract.id:quote(contract)},now=NOW)['p'].observation == manual
    next_day = quote(contract,stamp='2026-01-08T11:45:00+00:00')
    assert select_valuations(journal,{'p':manual},{contract.id:next_day},now=NOW+timedelta(days=1))['p'].observation == next_day
    backfilled = ledger.append_event(journal,ledger.Event('late','2026-01-06','settlement',position_id='p',price=1010))
    assert current_marks(backfilled,{'p':manual}) == {'p':manual}


def test_retrieval_cannot_freshen_old_quote_and_failures_retain_age(tmp_path):
    _, journal, contract = setup(tmp_path)
    old = quote(contract,stamp='2026-01-05T11:45:00+00:00')
    record_result(tmp_path,contract,old,'',attempted_at=NOW,now=NOW)
    later = NOW+timedelta(hours=2)
    record_result(tmp_path,contract,replace(old,observed_at=later.isoformat()),'',attempted_at=later,now=later)
    record_result(tmp_path,contract,None,'Invented outage',attempted_at=later,now=later)
    raw = read_quotes(tmp_path)
    assert len(raw['contracts'][contract.id]['observations']) == 1
    saved = latest_quotes(raw)[contract.id]
    result = freshness(saved,now=later,error=raw['contracts'][contract.id]['error'])
    assert result.status == 'stale' and result.qualified and '2d' in result.label
    assert 'Invented outage' in result.detail and saved.observed_at == NOW.isoformat()
    fallback = select_valuations(journal,now=NOW)['p']
    assert fallback.qualified and fallback.observation.date == '2026-01-02'
    assert 'Recorded' in fallback.label


def test_concurrent_old_response_and_equal_time_revision_do_not_replace_new_price(tmp_path):
    from portfolio_app.futures_valuation import load_valuations
    _, journal, contract = setup(tmp_path)
    fresh = quote(contract)
    record_result(tmp_path,contract,fresh,'',attempted_at=NOW,now=NOW)
    old = quote(contract,stamp='2026-01-06T11:45:00+00:00',price=1050)
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda q:record_result(tmp_path,contract,q,'Old request',attempted_at=NOW-timedelta(hours=1),now=NOW),
                      [old,replace(fresh,price=980)]))
    raw = read_quotes(tmp_path)
    assert latest_quotes(raw)[contract.id] == fresh
    assert len(raw['contracts'][contract.id]['observations']) == 3
    assert raw['contracts'][contract.id]['error'] == ''
    selected, _ = load_valuations(tmp_path,journal,now=NOW)
    assert selected['p'].status == 'revised' and selected['p'].qualified
    newer = quote(contract,stamp='2026-01-07T11:46:00+00:00')
    record_result(tmp_path,contract,newer,'',attempted_at=NOW,now=NOW)
    assert load_valuations(tmp_path,journal,now=NOW)[0]['p'].status == 'delayed'


def test_quote_worker_single_flight_backoff_and_manual_records_untouched(tmp_path):
    _, journal, contract = setup(tmp_path)
    manual_file = tmp_path/'account-observations.json'
    manual_file.write_text('{"version":2,"marks":{},"fx":[]}')
    original = manual_file.read_bytes()
    gate, calls = Event(), []
    def fetch(c):
        calls.append(c.id)
        assert gate.wait(5)
        return quote(c)
    now = NOW
    worker = QuoteCollector(fetch=fetch,now=lambda:now)
    assert worker.schedule(tmp_path,journal)
    assert not worker.schedule(tmp_path,journal)
    gate.set();worker._workers[str(tmp_path.resolve())].join(10)
    assert not worker.error(tmp_path)
    assert not worker.schedule(tmp_path,journal)
    now += timedelta(minutes=5)
    worker.fetch = lambda _: (_ for _ in ()).throw(DataError('Invented outage'))
    assert worker.schedule(tmp_path,journal)
    worker._workers[str(tmp_path.resolve())].join(10)
    assert latest_quotes(read_quotes(tmp_path))[contract.id].price == 990
    assert manual_file.read_bytes() == original and ledger.load_journal(tmp_path)[0] == journal
    assert len(calls) == 1


def test_partial_close_roll_and_closed_leg_do_not_reuse_wrong_contract_quote(tmp_path):
    _,journal,contract = setup(tmp_path)
    next_contract = make_contract('FMWN','2026-06-19')
    journal = ledger.add_position(ledger.add_contract(journal,next_contract),ledger.PositionSpec('next','a',next_contract.id))
    journal = ledger.roll(journal,
        close=ledger.Event('close','2026-01-07','trade',position_id='p',quantity=-1,price=1000,group_id='roll'),
        opening=ledger.Event('open-next','2026-01-07','trade',position_id='next',quantity=1,price=1100,group_id='roll'))
    values = select_valuations(journal,automatic={contract.id:quote(contract)},now=NOW)
    assert values['p'].observation.price == 990
    assert values['next'].observation.price == 1100 and values['next'].qualified
    journal = ledger.append_event(journal,ledger.Event('finish','2026-01-07','trade',position_id='p',quantity=-1,price=995))
    assert select_valuations(journal,now=NOW)['p'].status == 'closed'


def test_missing_corrupt_quotes_and_voided_settlements_keep_honest_fallback(tmp_path):
    from portfolio_app.futures_valuation import load_valuations
    _, journal, contract = setup(tmp_path)
    journal = ledger.append_events(journal, [
        ledger.Event('settled','2026-01-06','settlement',position_id='p',price=1050),
        ledger.Event('void','2026-01-06','void',replaces='settled')])
    SettlementArchive(tmp_path).record(contract, PriceObservation(contract.id,'2026-01-06',1050))
    values, _ = load_valuations(tmp_path,journal,now=NOW)
    assert values['p'].status == 'basis' and values['p'].observation.date == '2026-01-02'
    (tmp_path/'futures-quotes.json').write_text('{invented invalid data')
    values, _ = load_valuations(tmp_path,journal,now=NOW)
    assert values['p'].qualified and 'Cannot read saved futures quotes' in values['p'].detail
    assert freshness(None,now=NOW).label == 'No price'


def test_closed_roll_history_does_not_qualify_fresh_open_investment(tmp_path):
    from portfolio_app.futures_investments import investment_positions
    from portfolio_app.futures_valuation import summarize_freshness
    from test_futures_investments import grouped
    service, journal, contract = grouped(tmp_path)
    next_contract = replace(contract,expiry='2027-06-18')
    journal = ledger.add_position(ledger.add_contract(journal,next_contract),
        ledger.PositionSpec('next','a',next_contract.id,investment_id='investment:synthetic'))
    journal = ledger.roll(journal,
        close=ledger.Event('close','2026-01-07','trade',position_id='p',quantity=-2,price=1020,group_id='roll'),
        opening=ledger.Event('roll-open','2026-01-07','trade',position_id='next',quantity=2,price=1030,group_id='roll'))
    selected = select_valuations(journal,automatic={next_contract.id:quote(next_contract)},now=NOW)
    valued = value_holdings(read_snapshot(tmp_path/'holdings.csv').holdings,service)
    raw = journal_positions(valued,journal,service,valuations=selected)
    display = investment_positions(raw,journal)
    assert display.iloc[0].valuation_status == 'delayed'
    assert not summarize_freshness(display)[2]
