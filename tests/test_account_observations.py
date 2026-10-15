"""Synthetic mark provenance and concurrency cases; no private workspace reads."""
from dataclasses import asdict
from datetime import date, timedelta
from hashlib import sha256
import json
from pathlib import Path

import pytest

from portfolio_app.account_journal import Account, Event, Journal, PositionSpec, append_event, append_events, replay
from portfolio_app.account_observations import (
    FILE, BoundPriceObservation, bind_mark, current_marks, load_observations, save_observations,
)
from portfolio_app.derivative_types import ContractSpec, PriceObservation
from portfolio_app.holdings import DataError


def invented():
    contract = ContractSpec('SYNTHETIC', '2026-03-20', 'EUR', 10, 1)
    journal = Journal(accounts=(Account('a', 'Invented account'),), contracts=(contract,),
        positions=(PositionSpec('p', 'a', contract.id),))
    journal = append_events(journal, (
        Event('cash', '2026-01-01', 'cash_open', account_id='a', currency='EUR', amount=1000),
        Event('opening', '2026-01-01', 'trade', account_id='a', position_id='p', quantity=1, price=100),
    ))
    return journal, contract


def mark(contract, day='2026-01-03', price=110):
    return PriceObservation(contract.id, day, price, source='Explicitly invented observation')


def settlement(day='2026-01-03', price=120, identity='settlement', **fields):
    return Event(identity, day, 'settlement', account_id='a', position_id='p', price=price, **fields)


def test_same_day_saved_mark_is_invalidated_by_later_settlement():
    journal, contract = invented()
    observation = bind_mark(journal, 'p', mark(contract))
    assert current_marks(journal, {'p': observation}) == {'p': observation}
    settled = append_event(journal, settlement())
    assert current_marks(settled, {'p': observation}) == {}
    state = replay(settled)
    assert state.cash['a', 'EUR'] == 1200
    assert state.positions['p'].unsettled_pnl(state.positions['p'].last_price) == 0
    # An explicitly confirmed mark after settlement can still be same-day.
    newer = bind_mark(settled, 'p', mark(contract, price=121))
    assert current_marks(settled, {'p': newer}) == {'p': newer}


def test_same_day_correction_invalidates_binding_and_void_restores_previous_basis():
    journal, contract = invented()
    settled = append_event(journal, settlement())
    observation = bind_mark(settled, 'p', mark(contract, price=121))
    corrected = append_event(settled, settlement(price=125, identity='correction', replaces='settlement'))
    assert current_marks(corrected, {'p': observation}) == {}
    late = append_event(settled, settlement('2026-01-04', 130, 'later'))
    assert current_marks(late, {'p': observation}) == {}
    voided = append_event(late, Event('void', '2026-01-05', 'void', replaces='later'))
    assert current_marks(voided, {'p': observation}) == {'p': observation}


def test_fees_do_not_change_clearing_basis_but_partial_close_does():
    journal, contract = invented()
    observation = bind_mark(journal, 'p', mark(contract))
    with_fee = append_event(journal, Event('fee', '2026-01-03', 'fee', account_id='a', currency='EUR', amount=1))
    assert current_marks(with_fee, {'p': observation}) == {'p': observation}
    increased = append_event(journal, Event('increase', '2026-01-02', 'trade', account_id='a', position_id='p', quantity=1, price=105))
    observation = bind_mark(increased, 'p', mark(contract))
    partial = append_event(increased, Event('partial', '2026-01-03', 'trade', account_id='a', position_id='p', quantity=-1, price=111))
    assert current_marks(partial, {'p': observation}) == {}


def test_final_settlement_also_invalidates_basis_binding():
    journal, contract = invented()
    observation = bind_mark(journal, 'p', mark(contract, '2026-03-20', 110))
    final = append_event(journal, Event('final', '2026-03-20', 'final_settlement', account_id='a', position_id='p', price=115))
    assert current_marks(final, {'p': observation}) == {}


def test_legacy_marks_need_strictly_later_dates_and_ignore_voided_events():
    journal, contract = invented()
    same_day = mark(contract, '2026-01-01')
    later = mark(contract, '2026-01-02')
    assert current_marks(journal, {'p': same_day}) == {}
    assert current_marks(journal, {'p': later}) == {'p': later}
    settled = append_event(journal, settlement())
    voided = append_event(settled, Event('void', '2026-01-04', 'void', replaces='settlement'))
    assert current_marks(voided, {'p': later}) == {'p': later}


def test_binding_rejects_wrong_position_contract_old_or_future_date():
    journal, contract = invented()
    with pytest.raises(DataError, match='position'):
        bind_mark(journal, 'missing', mark(contract))
    with pytest.raises(DataError, match='contract'):
        bind_mark(journal, 'p', PriceObservation('invented-other-contract', '2026-01-02', 100))
    with pytest.raises(DataError, match='predates'):
        bind_mark(journal, 'p', mark(contract, '2025-12-31'))
    with pytest.raises(DataError, match='future'):
        bind_mark(journal, 'p', mark(contract, (date.today()+timedelta(days=1)).isoformat()))


def test_bound_marks_roundtrip_with_legacy_read_only_compatibility(tmp_path):
    journal, contract = invented()
    original = mark(contract)
    legacy = dict(version=1, marks={'p': asdict(original)}, fx=[])
    path = tmp_path/FILE
    path.write_text(json.dumps(legacy, default=str))
    before = path.read_bytes()
    marks, rates, revision = load_observations(tmp_path)
    assert marks == {'p': original} and not rates
    assert path.read_bytes() == before
    bound = bind_mark(journal, 'p', original)
    save_observations(tmp_path, {'p': bound}, {}, revision)
    reloaded, _, revision = load_observations(tmp_path)
    assert isinstance(reloaded['p'], BoundPriceObservation)
    assert current_marks(journal, reloaded) == reloaded
    assert json.loads(path.read_text())['version'] == 2
    assert list((tmp_path/'.backups').iterdir())


def test_revision_hash_matches_read_snapshot_even_if_file_changes_after_read(tmp_path, monkeypatch):
    journal, contract = invented()
    save_observations(tmp_path, {'p': bind_mark(journal, 'p', mark(contract))}, {}, None)
    path = tmp_path/FILE
    original = path.read_bytes()
    real_read = Path.read_bytes
    def changed_after_read(selected):
        data = real_read(selected)
        if selected == path:
            path.write_bytes(original+b'\n')
        return data
    with monkeypatch.context() as patch:
        patch.setattr(Path, 'read_bytes', changed_after_read)
        marks, rates, revision = load_observations(tmp_path)
    assert revision == sha256(original).hexdigest()
    with pytest.raises(DataError, match='changed'):
        save_observations(tmp_path, marks, rates, revision)


@pytest.mark.parametrize('changes', [dict(rate='0'), dict(rate='NaN'), dict(source=''),
    dict(target_currency='EUR'), dict(date='2999-01-01')])
def test_invalid_fx_is_rejected_before_any_write(tmp_path, changes):
    value = dict(source_currency='EUR', target_currency='USD', rate='1.1', date='2026-01-01', source='Synthetic FX') | changes
    with pytest.raises(DataError):
        save_observations(tmp_path, {}, {('EUR','USD'): value}, None)
    assert not (tmp_path/FILE).exists()


def test_duplicate_pairs_future_marks_and_invalid_fingerprint_fail_load(tmp_path):
    _, contract = invented()
    rate = dict(source_currency='EUR', target_currency='USD', rate='1.1', date='2026-01-01', source='Synthetic FX')
    invalids = [dict(version=2,marks={},fx=[rate,rate]),
        dict(version=2,marks={'p':asdict(mark(contract,(date.today()+timedelta(days=1)).isoformat()))},fx=[]),
        dict(version=2,marks={'p':dict(asdict(mark(contract)),basis_fingerprint='invalid')},fx=[])]
    for raw in invalids:
        path = tmp_path/FILE
        path.write_text(json.dumps(raw,default=str))
        before = path.read_bytes()
        with pytest.raises(DataError):
            load_observations(tmp_path)
        assert path.read_bytes() == before


def test_manual_cross_rate_can_invert_either_leg():
    from portfolio_app.account_observations import manual_rate
    rates = {('EUR','USD'): {'rate':'1.2'}, ('EUR','GBP'): {'rate':'.8'}}
    assert manual_rate(rates, 'GBP', 'USD') == pytest.approx(1.5)
    assert manual_rate(rates, 'USD', 'GBP') == pytest.approx(2/3)
