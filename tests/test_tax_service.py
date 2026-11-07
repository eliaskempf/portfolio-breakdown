"""Portfolio selection never changes tax ownership or duplicates allowances."""
from decimal import Decimal as D
from tax_sample import create_tax_sample
from portfolio_app.tax_service import candidates, result_for, combined
from portfolio_app.tax_evidence import load_inputs, evidence_key


def test_broker_groups_and_combined_sum(tmp_path):
    rows, journal = create_tax_sample(tmp_path)
    items = candidates(rows, journal, '2025-12-31')
    raw, _ = load_inputs(tmp_path)
    assert len(items) == 3
    results = [result_for(c, 2025, raw['funds'][evidence_key(2025, c.instrument)], {})[1] for c in items]
    gross, tax, count, total = combined(results, D(100))
    assert gross == D('442.75') and count == total == 3
    assert tax.taxable == D('309.925')
    assert tax.allowance_used == 100


def test_partial_result_is_counted_and_unknown_not_zero(tmp_path):
    rows, journal = create_tax_sample(tmp_path)
    item = candidates(rows, journal, '2025-12-31')[0]
    unknown = result_for(item, 2025, {}, {})[1]
    known = result_for(item, 2025, {'start_price': '100', 'end_price': '110', 'distribution_mode': 'Accumulating / no distributions'}, {})[1]
    _, _, count, total = combined([unknown, known])
    assert count == 1 and total == 2


def test_reviewed_lots_require_unchanged_history(tmp_path):
    rows, journal = create_tax_sample(tmp_path)
    item = candidates(rows, journal, '2025-12-31')[0]
    raw, _ = load_inputs(tmp_path)
    manual = raw['funds'][evidence_key(2025, item.instrument)]
    override = {'fingerprint': 'a'*64, 'lots': [{'quantity': '10', 'acquired': '2025-07-01'}]}
    assert result_for(item, 2025, manual, {}, override=override, fingerprint='b'*64)[1].gross is None
    assert result_for(item, 2025, manual, {}, override=override, fingerprint='a'*64)[1].gross == D('8.86')


def test_only_splits_within_the_calculation_year_need_price_reconciliation(tmp_path):
    from dataclasses import replace
    from portfolio_app.vorabpauschale import TaxLot
    rows, journal = create_tax_sample(tmp_path)
    item = candidates(rows, journal, '2025-12-31')[0]
    raw, _ = load_inputs(tmp_path)
    manual = raw['funds'][evidence_key(2025, item.instrument)]
    # Both inputs already use the units established by the earlier split.
    old = replace(item.holding, lots=(TaxLot(D(100), '2023-01-01'),),
                  splits=(('2024-06-01', D(2)),))
    assert result_for(replace(item, holding=old), 2025, manual, {})[1].gross == D('177.10')
    current = replace(old, splits=(('2025-06-01', D(2)),))
    assert result_for(replace(item, holding=current), 2025, manual, {})[1].gross is None
