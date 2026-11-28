"""Exact results from old Add purchase batches can distinguish colliding CSV edits."""
from decimal import Decimal as D

import pytest

from portfolio_app.performance import cost_basis
from portfolio_app.portfolio.positions import read_snapshot
from test_legacy_purchase_recovery import ADD_DATE, content, opening, purchase, v1_batch
from test_saved_purchase_opening import csv_with_document, first_sale, prior_component


def ordinary_history(multiple):
    record = dict(purchase(), fx_rate='0.8')
    batches = [dict(v1_batch('add', opening('0', ''), [record], '2', '10'), version=2)]
    parts = [prior_component()]
    if multiple:
        addition = dict(purchase('1', day=ADD_DATE), fx_rate='0.8')
        batches.append(dict(v1_batch('add', opening('2','10'), [addition], '3','10'), version=2))
        parts.append(prior_component('1','10',ADD_DATE,'8'))
    return batches, parts


@pytest.mark.parametrize('multiple', [False, True])
@pytest.mark.parametrize('field', ['shares','acquisition_price'])
def test_ordinary_batch_proves_exact_source_edit_and_retires_supplied_fx(tmp_path, multiple, field):
    batches, parts = ordinary_history(multiple)
    q = batches[-1]['result_shares']
    edit = {field: (q if field == 'shares' else '10') + '.00000000000000001'}
    path = tmp_path/'holdings.csv'
    path.write_text(csv_with_document(content(batches), [q,'10','USD'], parts, **edit))
    saved = cost_basis.active_components(read_snapshot(path).holdings.iloc[0])
    assert len(saved) == 1 and saved[0]['date'] == '' and saved[0]['conversions'] == {}
    assert cost_basis.resolve_cost(saved,'EUR').exact_amount is None
    event = first_sale(path)
    assert event.costs == tuple(saved)
    assert event.quantity == D(edit.get('shares', q))


def test_unchanged_source_skips_purchase_replay_and_unrelated_history_preserves_currency_evidence(tmp_path, monkeypatch):
    batches, parts = ordinary_history(False)
    path = tmp_path/'holdings.csv'
    path.write_text(csv_with_document(content(batches), ['2','10','USD'], parts))
    with monkeypatch.context() as isolated:
        def unexpected_replay(*args, **kwargs):
            pytest.fail('Matching exact source does not require historical purchase replay')
        isolated.setattr(cost_basis, '_legacy_components', unexpected_replay)
        assert cost_basis.active_components(read_snapshot(path).holdings.iloc[0]) == parts

    # A saved component with a different date cannot establish that the earlier
    # batch wrote this document. The unmarked currency evidence retains its FX.
    parts[0]['date'] = ADD_DATE
    path.write_text(csv_with_document(content(batches), ['2','10','USD'], parts,
                                      acquisition_price='10.00000000000000001'))
    assert cost_basis.active_components(read_snapshot(path).holdings.iloc[0]) == parts
