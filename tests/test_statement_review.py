"""Statement review rules exercised with deliberately invented PDFs only."""
from decimal import Decimal

import pytest

from portfolio_app.consors_pdf import parse_consors_pdf, local_review_rows
from portfolio_app.transaction_imports import as_event, save_pdf_drafts
from portfolio_app.account_journal import load_journal
from portfolio_app.positions import read_snapshot
from test_consors_pdf import statement
from test_transaction_imports import setup


@pytest.mark.parametrize('sale', [False, True])
@pytest.mark.parametrize('fees,taxes', [(False,False), (True,False), (False,True), (True,True)])
def test_fully_explained_statement_knows_charges(sale, fees, taxes):
    draft = parse_consors_pdf(statement(sale=sale,fees=fees,taxes=taxes))
    assert draft.statement_charges_known
    assert not draft.charge_review_reasons
    event = as_event(draft)
    assert event.charges_known
    assert sum(c.amount for c in event.charges if c.kind == 'fee') == (7 if fees else 0)
    assert sum(c.amount for c in event.charges if c.kind == 'tax') == (3 if taxes else 0)
    assert event.amount == Decimal(100)


@pytest.mark.parametrize('options', [
    dict(net='107,01'),
    dict(extra=('Unexplained service 1,00 EUR',)),
    dict(extra=('Servicegebühr 4',)),
    dict(extra=('Kurswert 100,00 EUR',)),
    dict(missing=('price',)),
    dict(extra=('Informationen bei Käufen bzw. Verlust Wertpapiere ungleich Aktien',
                'Synthetische Rechengröße 12,34 EUR')),
])
def test_balancing_or_rounded_statement_cannot_bypass_unresolved_evidence(options):
    draft = parse_consors_pdf(statement(**options))
    assert not draft.statement_charges_known
    assert draft.charge_review_reasons


def test_review_text_is_only_transient_and_points_to_the_actual_rows():
    source = statement(extra=('Informationen bei Käufen bzw. Verlust Wertpapiere ungleich Aktien',
                              'Synthetische Rechengröße 12,34 EUR'))
    draft = parse_consors_pdf(source)
    rows = local_review_rows(source,draft)
    assert len(rows) == 1
    assert rows[0][0] == 1 and rows[0][2] == 'Supplementary tax detail'
    assert rows[0][3] == 'Synthetische Rechengröße 12,34 EUR'
    event = as_event(draft,charges_known=True)
    assert '12,34' not in event.note and 'Rechengröße' not in event.note
    assert 'Supplementary tax detail: page 1, row' in event.note


def test_per_statement_confirmation_does_not_confirm_the_rest_of_the_batch(tmp_path):
    snapshot,journal,revision=setup(tmp_path)
    first=parse_consors_pdf(statement(net='107,01'))
    second=parse_consors_pdf(statement(quantity='3',gross='150,00',net='157,01'))
    automatic=parse_consors_pdf(statement(quantity='4',gross='200,00',net='207,00'))
    result=save_pdf_drafts(tmp_path,snapshot.holdings.iloc[0],journal,revision,snapshot,
        [first,second,automatic],confirmed_charge_ids=(first.fingerprint,))
    assert [e.charges_known for e in result.records] == [True,False,True]
    assert not result.active
    assert read_snapshot(tmp_path/'holdings.csv').holdings.iloc[0].shares==2
    assert [e.amount for e in load_journal(tmp_path)[0].securities[0].records] == [100,150,200]


def test_confirmation_for_one_similar_trade_does_not_allow_another(tmp_path):
    from portfolio_app.holdings import DataError
    snapshot,journal,revision=setup(tmp_path)
    first=parse_consors_pdf(statement())
    second=parse_consors_pdf(statement())
    third=parse_consors_pdf(statement())
    # Distinct byte fingerprints, same invented execution details.
    assert len({d.fingerprint for d in (first,second,third)}) == 3
    with pytest.raises(DataError,match='Similar'):
        save_pdf_drafts(tmp_path,snapshot.holdings.iloc[0],journal,revision,snapshot,
            [first,second,third],confirmed_similar_ids=(second.fingerprint,))
    assert not load_journal(tmp_path)[0].securities


def test_report_does_not_call_valid_commission_a_failed_zero_fee_check():
    from portfolio_app.statement_diagnostics import layout_report
    report=layout_report([statement()])
    assert 'Statement charges: reconciled exactly.' in report
    assert 'Automatic zero fees: review required.' not in report
    assert 'Nonzero fees or taxes are listed.' not in report
