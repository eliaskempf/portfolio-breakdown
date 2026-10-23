"""Independent, exact-decimal economics and persistence tests; invented data only."""
from dataclasses import replace
from decimal import Decimal as D

import pytest

from consors_audit_fixtures import BUY, FREE, SALE, LOSS_SALE, SUPPLEMENTARY_DETAILS, TAX_PAGE, LEGACY, COMPANY_FOOTER, text_pdf
from portfolio_app.account_journal import Journal, load_journal, save_journal
from portfolio_app.consors_pdf import parse_consors_pdf, parse_consors_batch
from portfolio_app.gain_components import security_gain_components
from portfolio_app.history_commands import reconstruction_candidate, save_history
from portfolio_app.history_observations import ObservationStore
from portfolio_app.holdings import DataError
from portfolio_app.positions import read_snapshot
from portfolio_app.security_history import effective_holdings, replay_securities, selected_events
from portfolio_app.transaction_imports import save_pdf_drafts, identity_review_required


@pytest.fixture
def workspace(tmp_path):
    (tmp_path/'holdings.csv').write_text(
        'position_key,id,name,isin,wkn,shares,acquisition_price,acquisition_currency\n'
        'orchard,orchard,Synthetic Orchard,XX0000000777,SYN777,2,31.5,EUR\n'
        'second,orchard,Synthetic Orchard,XX0000000777,SYN777,0,,EUR\n')
    return tmp_path


def save(root, drafts, **options):
    snapshot = read_snapshot(root/'holdings.csv')
    journal, revision = load_journal(root)
    return save_pdf_drafts(root, snapshot.holdings.iloc[0], journal, revision, snapshot, drafts, **options)


def activate(root, *, accept_balance_change=False):
    journal, revision = load_journal(root)
    book = journal.securities[0]
    candidate = reconstruction_candidate(book, [e.id for e in book.records], starts_at_zero=not book.active)
    return save_history(root, candidate, expected_journal_revision=revision,
        expected_holdings_revision=read_snapshot(root/'holdings.csv').revision,
        activate=True, accept_balance_change=accept_balance_change)


def test_buy_partial_sale_fifo_cash_and_repeated_application(workspace):
    source = workspace/'holdings.csv'
    original = source.read_bytes()
    save(workspace, [parse_consors_pdf(text_pdf(BUY)), parse_consors_pdf(text_pdf(SALE, TAX_PAGE))], charges_known=True)
    journal, _ = load_journal(workspace)
    assert source.read_bytes() == original
    assert not journal.securities[0].active
    assert effective_holdings(read_snapshot(source).holdings, journal.securities).iloc[0].shares == 2
    for _ in range(2):
        journal = activate(workspace)
        state = replay_securities(journal.securities)
        assert state.positions['orchard'].quantity == 2
        assert sum(D(c['amount']) for c in state.positions['orchard'].costs) == D(63)
        assert [(c.kind, c.amount) for c in state.cash] == [
            ('buy', D(-125)), ('fee', D(-1)), ('sell', D(90)),
            ('fee', D('-.50')), ('tax', D('-.50')), ('tax', D('-.50'))]
        assert sum(c.amount for c in state.cash) == D('-37.50')
        gains = security_gain_components(journal, 'orchard', ObservationStore(), '2025-03-25', current_value=D(80))
        assert (gains.remaining_cost, gains.realized, gains.unrealized,
                gains.purchase_payments, gains.sale_proceeds, gains.expenses) == (
                    D(63), D('25.50'), D(17), D(126), D('88.50'), D(0))
        assert len(journal.securities[0].records) == 3
    # Retained coordinates/quoted price, never original PDFs or raw rows.
    record = journal.securities[0].records[0]
    assert 'Quoted unit price: 31.250000 EUR' in record.note
    assert 'Gross amount: page 1, row 5' in record.note
    assert all(p.suffix != '.pdf' for p in workspace.rglob('*'))


def test_loss_calculation_details_never_change_fifo_or_cash(workspace):
    sale = parse_consors_pdf(text_pdf(
        (*LOSS_SALE,'Verbleibender synthetischer Bestand ST 7,25000'),SUPPLEMENTARY_DETAILS))
    save(workspace,[parse_consors_pdf(text_pdf(BUY)),sale],charges_known=True)
    journal = activate(workspace)
    state = replay_securities(journal.securities)
    assert state.positions['orchard'].quantity == D(2)
    assert sum(D(cost['amount']) for cost in state.positions['orchard'].costs) == D(63)
    assert [(flow.kind,flow.amount) for flow in state.cash] == [
        ('buy',D(-125)),('fee',D(-1)),('sell',D(40)),('fee',D('-.50'))]
    gains = security_gain_components(journal,'orchard',ObservationStore(),'2025-03-25',current_value=D(80))
    assert (gains.realized,gains.sale_proceeds,gains.remaining_cost) == (D('-23.50'),D('39.50'),D(63))


def test_full_sale_closes_lots_and_counts_charges_once(workspace):
    sale = tuple(r.replace('2,00000','4,00000').replace('90,00','180,00').replace('88,50','178,50') for r in SALE)
    save(workspace, [parse_consors_pdf(text_pdf(BUY)), parse_consors_pdf(text_pdf(sale, TAX_PAGE))], charges_known=True)
    journal = activate(workspace, accept_balance_change=True)
    balance = replay_securities(journal.securities).positions['orchard']
    assert balance.quantity == 0 and not balance.lots
    gains = security_gain_components(journal, 'orchard', ObservationStore(), '2025-03-25', current_value=D(0))
    assert gains.remaining_cost == 0 and gains.realized == D('52.50') and gains.sale_proceeds == D('178.50')


def test_fractional_lots_use_fifo_and_buy_tax_is_separate(workspace):
    first = ('Kauf AM 10.02.2025', 'ORCHARD SYN777 XX0000000777', 'ST 1,25000',
        'Kurs 16,0000 EUR', 'Kurswert 20,00 EUR', 'Provision 0,50 EUR',
        'KapSt 0,25 EUR', 'Zulasten 20,75 EUR')
    second = ('Kauf AM 11.02.2025', 'ORCHARD SYN777 XX0000000777', 'ST 0,75000',
        'Kurs 20,0000 EUR', 'Kurswert 15,00 EUR', 'Provision 0,75 EUR', 'Zulasten 15,75 EUR')
    sale = ('Verkauf AM 12.02.2025', 'ORCHARD SYN777 XX0000000777', 'ST 1,50000',
        'Kurs 30,0000 EUR', 'Kurswert 45,00 EUR', 'Provision 1,00 EUR', 'Zugunsten 44,00 EUR')
    save(workspace, [parse_consors_pdf(text_pdf(r)) for r in (first, second, sale)], charges_known=True)
    journal = activate(workspace, accept_balance_change=True)
    # FIFO consumes 20.50 + one third of 15.75 = 25.75; leaves 10.50.
    gains = security_gain_components(journal, 'orchard', ObservationStore(), '2025-03-25', current_value=D(12))
    assert replay_securities(journal.securities).positions['orchard'].quantity == D('.5')
    assert (gains.remaining_cost, gains.realized, gains.expenses, gains.purchase_payments) == (
        D('10.50'), D('18.25'), D('.25'), D('36.50'))


@pytest.mark.parametrize('confirmed', [False, True])
@pytest.mark.parametrize('invalid', [(*FREE, 'Unknown refund - 7,25 EUR'), (*FREE, 'Kurswert EUR 125,00'),
    tuple(r.replace('Zulasten', 'Zugunsten') for r in FREE)])
def test_errors_block_whole_batch_even_with_completeness_confirmation(workspace, confirmed, invalid):
    before = load_journal(workspace)
    with pytest.raises(DataError):
        save(workspace, [parse_consors_pdf(text_pdf(BUY)), parse_consors_pdf(text_pdf(invalid))], charges_known=confirmed)
    assert load_journal(workspace) == before
    assert not (workspace/'transaction-imports').exists()


def test_mixed_selected_batch_only_autoconfirms_eligible_rows(workspace):
    invalid = text_pdf((*FREE, 'Devisenkurs'))
    drafts = parse_consors_batch([text_pdf(FREE), text_pdf(SALE, TAX_PAGE), invalid])
    book = save(workspace, drafts[:2])
    assert [e.charges_known for e in book.records] == [True, False]
    assert book.records[0].charges == ()
    assert book.records[1].amount == 90
    assert len(load_journal(workspace)[0].securities[0].records) == 2


def test_duplicate_anywhere_in_portfolio_is_atomic(workspace):
    draft = parse_consors_pdf(text_pdf(BUY))
    save(workspace, [draft])
    before = load_journal(workspace)
    snapshot = read_snapshot(workspace/'holdings.csv')
    with pytest.raises(DataError, match='Already imported'):
        save_pdf_drafts(workspace, snapshot.holdings.iloc[1], *before, snapshot,
            [parse_consors_pdf(text_pdf(FREE)), draft])
    assert load_journal(workspace) == before


@pytest.mark.parametrize('change', ['holdings', 'journal'])
def test_stale_revision_preserves_all_files(workspace, change):
    journal, revision = load_journal(workspace)
    snapshot = read_snapshot(workspace/'holdings.csv')
    if change == 'holdings':
        path = workspace/'holdings.csv'
        path.write_text(path.read_text().replace('2,31.5', '3,31.5'))
    else:
        save_journal(workspace, Journal(version=2), revision)
    before = {p.name:p.read_bytes() for p in workspace.iterdir() if p.is_file()}
    with pytest.raises(DataError, match='changed'):
        save_pdf_drafts(workspace, snapshot.holdings.iloc[0], journal, revision, snapshot,
                        [parse_consors_pdf(text_pdf(BUY))])
    assert {p.name:p.read_bytes() for p in workspace.iterdir() if p.is_file() and p.name in before} == before
    assert load_journal(workspace)[0].securities == ()


@pytest.mark.parametrize('field,value', [('isin','XX0000000888'), ('wkn','SYN888')])
def test_stored_identifier_conflict_cannot_be_overridden(workspace, field, value):
    draft = replace(parse_consors_pdf(text_pdf(BUY)), **{field:value})
    with pytest.raises(DataError, match='does not match'):
        save(workspace, [draft], identity_confirmed=True)


def test_missing_comparable_identifier_requires_explicit_review(workspace):
    path = workspace/'holdings.csv'
    path.write_text(path.read_text().replace('XX0000000777,SYN777', ','))
    draft = parse_consors_pdf(text_pdf(BUY))
    assert identity_review_required(draft, read_snapshot(path).holdings.iloc[0])
    with pytest.raises(DataError, match='identifiers'):
        save(workspace, [draft])
    assert not load_journal(workspace)[0].securities
    save(workspace, [draft], identity_confirmed=True)
    assert len(load_journal(workspace)[0].securities[0].records) == 1


def test_wkn_only_legacy_matches_stored_wkn(workspace):
    save(workspace, [parse_consors_pdf(text_pdf(LEGACY))])
    assert load_journal(workspace)[0].securities[0].records[0].charges_known


def test_report_numbers_map_to_local_filenames_even_for_blocked_and_duplicate_files(workspace, monkeypatch):
    from types import SimpleNamespace
    from streamlit.testing.v1 import AppTest
    import portfolio_app.transaction_import_ui as ui

    source = text_pdf((*FREE, COMPANY_FOOTER))
    files = [SimpleNamespace(name=name, size=len(raw), getvalue=lambda raw=raw: raw)
             for name, raw in [('synthetic-z.pdf',source), ('synthetic-broken.pdf',b'INVALID'),
                              ('synthetic-a.pdf',source)]]
    monkeypatch.setattr(ui.st, 'file_uploader', lambda *a, **kw: files)
    download = {}
    monkeypatch.setattr(ui.st, 'download_button', lambda *a, **kw: download.update(kw))
    app = AppTest.from_string(f'''
from pathlib import Path
from portfolio_app.account_journal import load_journal
from portfolio_app.positions import read_snapshot
from portfolio_app.history_commands import book_for
from portfolio_app.transaction_import_ui import render_pdf_import
root=Path({str(workspace)!r})
snapshot=read_snapshot(root/'holdings.csv')
journal,revision=load_journal(root)
row=snapshot.holdings.iloc[0]
render_pdf_import(root,row,book_for(journal,row),journal,revision,snapshot,'synthetic')
''').run()
    assert not app.exception
    details = app.dataframe[1].value
    assert list(zip(details.Document, details.File)) == [
        (1,'synthetic-z.pdf'),(2,'synthetic-broken.pdf'),(3,'synthetic-a.pdf')]
    assert app.dataframe[0].value['Listed fees'][0] == '€0.00'
    report = download['data']()
    assert all(f'Document {n}' in report for n in (1,2,3))
    assert 'Document 2\nPDF could not be read.' in report
    assert 'Automatic zero fees: eligible.' in report
    assert all(file.name not in report for file in files)
    assert 'DE000000000' not in report and '12345' not in report


@pytest.mark.parametrize('times', [('14:00:00','09:00:00'), (None,None), ('09:00:00','09:00:00'),
    (None,'09:00:00'), ('09:00:00',None)])
def test_same_day_ordering_uses_time_or_retains_upload_order(workspace, times):
    drafts = [replace(parse_consors_pdf(text_pdf(rows)), trade_date='2025-03-13', trade_time=time)
              for rows,time in zip((BUY,FREE),times)]
    book = save(workspace, drafts, allow_similar=True)
    selected = selected_events(replace(book, active_ids=tuple(e.id for e in book.records)))
    expected = list(reversed(book.records)) if times[0] == '14:00:00' else list(book.records)
    assert selected == tuple(expected)
    assert len({e.sequence for e in book.records}) == 2
