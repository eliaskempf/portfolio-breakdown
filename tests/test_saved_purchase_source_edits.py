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


def test_currency_review_rounded_batch_summary_does_not_hide_edit(tmp_path):
    # v0.2.1 Add purchase wrote a 50-digit average. Review currency re-encoded
    # the same dated component with a float summary and a new estimated target.
    original, rounded = '33.' + '3' * 48, '33.33333333333333'
    record = dict(purchase('3', '33', fees='1'), fx_rate='0.8')
    batch = dict(v1_batch('add', opening('0', ''), [record], '3', original), version=2)
    parts = [prior_component('3', '100', target_cost='80')]
    parts[0]['conversions']['GBP'] = dict(amount='70', rate='0.7', method='estimate',
                                          saved_at='2024-04-01', observed_on='2024-04-01')
    path = tmp_path/'holdings.csv'

    def read(review_price, csv_price):
        path.write_text(csv_with_document(content([batch]), ['3', review_price, 'USD'], parts,
                                          acquisition_price=csv_price))
        return cost_basis.active_components(read_snapshot(path).holdings.iloc[0])

    assert read(rounded, original) == parts
    edited, = read(rounded, rounded)
    assert edited['date'] == '' and edited['conversions'] == {}
    assert D(edited['amount']) == 3 * D(rounded)
    assert first_sale(path).costs == (edited,)

    # A different summary cannot bind these conversions to the batch, even
    # when its dated amounts and supplied FX happen to coincide.
    assert read('34', '34') == parts
    parts[0]['date'] = ADD_DATE
    assert read(rounded, rounded) == parts


def test_matching_reviewed_purchase_result_skips_recalculation(tmp_path, monkeypatch):
    from portfolio_app.portfolio import purchases

    batches, parts = ordinary_history(False)
    parts[0]['conversions']['GBP'] = dict(amount='14', rate='0.7', method='estimate',
                                          saved_at='2024-04-01', observed_on='2024-04-01')
    path = tmp_path/'holdings.csv'
    path.write_text(csv_with_document(content(batches), ['2', '10', 'USD'], parts))

    def unexpected_replay(*args, **kwargs):
        pytest.fail('An unchanged final purchase result does not require replay')

    monkeypatch.setattr(purchases, 'summarize_purchases', unexpected_replay)
    assert cost_basis.active_components(read_snapshot(path).holdings.iloc[0]) == parts


@pytest.mark.parametrize('field', ['acquisition_price', 'shares'])
def test_edit_to_integer_spelling_retires_currency_review_of_original_fraction(tmp_path, field):
    # v0.2.1 used the original column's numeric type when saving the FX review.
    q = '2.00000000000000001' if field == 'shares' else '2'
    price = '10.00000000000000001' if field == 'acquisition_price' else '10'
    amount = D(q) * D(price)
    record = dict(purchase(q, price), fx_rate='0.8')
    batch = dict(v1_batch('add', opening('0',''), [record], q, price), version=2)
    part = prior_component(q, str(amount), target_cost=str(amount*D('0.8')))
    part['conversions']['GBP'] = dict(amount=str(amount*D('0.7')), rate='0.7', method='estimate',
                                      saved_at='2024-04-01', observed_on='2024-04-01')
    path = tmp_path/'holdings.csv'
    def read(**updates):
        path.write_text(csv_with_document(content([batch]), ['2.0','10.0','USD'], [part], **updates))
        return cost_basis.active_components(read_snapshot(path).holdings.iloc[0])
    assert read() == [part]
    integer = '10' if field == 'acquisition_price' else '2'
    for spelling in (integer, integer + '.0'):
        edited, = read(**{field: spelling})
        assert not edited['date'] and edited['conversions'] == {}
