"""Invented settlements and injected providers; never live market requests."""
from dataclasses import replace
import json
import pytest
from portfolio_app.futures import FuturePosition
from portfolio_app.futures_mtm import MTM_FILE, daily_mtm, load_settlements, mtm_stats, refresh_settlements
from portfolio_app.holdings import DataError


def position(**changes):
    p=FuturePosition(entry_price=100., mark_price=99., quote_date='2026-09-24', contract_expiry='2026-12-18',
        reference_price=99., entry_date='2026-09-21', entry_reference_price=100., entry_price_source='https://www.eurex.com/invented',
        reference_capital_eur=1000.)
    return replace(p, **changes)


def quote(day, price, expiry='2026-12-18'):
    return dict(instrument='FMWN', quote_date=day, price=price, contract_expiry=expiry,
        kind='Daily settlement', source='https://www.eurex.com/invented', fetched_at='2026-09-24T12:00:00+00:00')


def quotes():
    return [quote(f'2026-09-{day}', price) for day, price in [(21,100.),(22,102.),(23,98.),(24,99.)]]


@pytest.mark.parametrize('contracts', [1,-2])
def test_mtm_telescopes_and_stats_preserve_direction_funding_and_fees(contracts):
    p=position(contracts=contracts, fees_eur=25., net_funding_eur=400., closed_pnl_eur=80.)
    table=daily_mtm(p,quotes())
    assert table['Daily MTM (EUR)'].tolist() == [0.,200.*contracts,-400.*contracts,100.*contracts]
    assert table['Daily MTM (EUR)'].sum() == p.pnl
    stats=mtm_stats(table)
    assert stats['total'] == p.pnl
    assert stats['drawdown'] == (400. if contracts==1 else 400.)
    assert stats['complete'] and stats['flat']==1
    assert stats['up']+stats['down']==3
    assert mtm_stats(daily_mtm(replace(p,net_funding_eur=-300.,fees_eur=40.),quotes()))==stats


def test_missing_dates_never_become_zero_or_daily_change_across_gap():
    table=daily_mtm(position(),quotes()[:2]+quotes()[3:])
    assert table['Daily MTM (EUR)'].iloc[2:].isna().all()
    assert table['Contract P&L (EUR)'].iloc[-1] == -100.
    stats=mtm_stats(table)
    assert not stats['complete'] and stats['best'] is stats['worst'] is stats['drawdown'] is None


def test_failed_refresh_retains_existing_quotes_and_never_changes_position(tmp_path):
    (tmp_path/'futures-sandbox.json').write_text('synthetic unchanged marker')
    records={q['quote_date']:q for q in quotes()}
    calls=[]
    def provider(day, expiry):
        calls.append(day)
        return records[day.isoformat()]
    assert not refresh_settlements(tmp_path,position(),fetch=provider)
    assert len(calls)==4
    before=(tmp_path/MTM_FILE).read_bytes()
    refresh_settlements(tmp_path,position(),fetch=provider)
    assert len(calls)==4
    def failed(*args): raise OSError('invented outage')
    assert len(refresh_settlements(tmp_path,position(),fetch=failed,refresh=True))==4
    assert (tmp_path/MTM_FILE).read_bytes()==before
    assert (tmp_path/'futures-sandbox.json').read_text()=='synthetic unchanged marker'
    assert len(load_settlements(tmp_path)['2026-12-18'])==4


def test_wrong_dates_maturities_closed_and_invalid_cache_fail_explicitly(tmp_path):
    assert len(refresh_settlements(tmp_path,position(),fetch=lambda *args: quote('2026-09-20',100.)))==4
    assert not (tmp_path/MTM_FILE).exists()
    with pytest.raises(DataError): daily_mtm(position(),[quote('2026-09-21',100.,'2027-03-19')])
    with pytest.raises(DataError): daily_mtm(position(contracts=0),quotes())
    (tmp_path/MTM_FILE).write_text(json.dumps(dict(version=1,contracts={'2026-12-18':[quotes()[0],quotes()[0]]})))
    with pytest.raises(DataError): load_settlements(tmp_path)
