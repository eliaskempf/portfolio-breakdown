"""Review, cancel and conflicting writes using invented account facts."""
from streamlit.testing.v1 import AppTest

from portfolio_app import account_journal as ledger
from test_actual_portfolio import invented
from test_ui import by_label


def launch_activity(directory):
    return AppTest.from_string(f'''
from pathlib import Path
from portfolio_app.account_journal import load_journal
from portfolio_app.account_activity_ui import render_activity
directory = Path({str(directory)!r})
journal, revision = load_journal(directory)
render_activity(directory, journal, revision, key='accounts_activity_')
''').run()


def test_review_cancel_save_and_original_retained(tmp_path):
    _, journal, _ = invented(tmp_path)
    ledger.save_journal(tmp_path, journal, None)
    before = (tmp_path / ledger.FILE).read_bytes()
    app = launch_activity(tmp_path)
    by_label(app.selectbox, 'Transaction details').set_value('fee').run()
    by_label(app.selectbox, 'Entry action').set_value('Correct entry').run()
    assert by_label(app.text_input, 'Corrected amount').value == '8.00'
    by_label(app.text_input, 'Corrected amount').set_value('3').run()
    by_label(app.button, 'Review correction').click().run()
    assert not app.exception and not app.error
    assert (tmp_path / ledger.FILE).read_bytes() == before
    by_label(app.button, 'Cancel correction').click().run()
    assert by_label(app.selectbox, 'Entry action').value == 'View entry'
    assert not any(b.label == 'Save correction' for b in app.button)
    by_label(app.selectbox, 'Entry action').set_value('Correct entry').run()
    by_label(app.text_input, 'Corrected amount').set_value('4').run()
    by_label(app.button, 'Review correction').click().run()
    by_label(app.button, 'Save correction').click().run()
    assert not app.exception and not app.error
    saved, _ = ledger.load_journal(tmp_path)
    assert saved.events[:-1] == journal.events
    assert saved.events[-1].amount == 4
    assert ledger.replay(saved).cash['a', 'EUR'] == 19996
    assert any('Replacement:' in c.value for c in app.caption)


def test_changed_inputs_and_revision_require_review_again(tmp_path):
    _, journal, _ = invented(tmp_path)
    ledger.save_journal(tmp_path, journal, None)
    app = launch_activity(tmp_path)
    by_label(app.selectbox, 'Transaction details').set_value('fee').run()
    by_label(app.selectbox, 'Entry action').set_value('Correct entry').run()
    by_label(app.text_input, 'Corrected amount').set_value('3').run()
    by_label(app.button, 'Review correction').click().run()
    by_label(app.text_input, 'Corrected amount').set_value('5').run()
    assert not any(b.label == 'Save correction' for b in app.button)
    by_label(app.button, 'Review correction').click().run()
    current, revision = ledger.load_journal(tmp_path)
    external = ledger.append_event(current, ledger.Event('external', '2026-01-06', 'fee', account_id='a', currency='EUR', amount=1))
    ledger.save_journal(tmp_path, external, revision)
    by_label(app.button, 'Save correction').click().run()
    assert not app.exception
    assert ledger.load_journal(tmp_path)[0] == external
    assert not any(b.label == 'Save correction' for b in app.button)


def test_journal_filters_and_explicit_reverse(tmp_path):
    _, journal, _ = invented(tmp_path)
    ledger.save_journal(tmp_path, journal, None)
    app = launch_activity(tmp_path)
    by_label(app.multiselect, 'Transaction actions').set_value(['Fee']).run()
    frame = app.dataframe[0].value
    assert frame.Action.tolist() == ['Fee']
    assert not {'id', 'reference', 'account_id', 'position_id'} & set(frame.columns)
    by_label(app.selectbox, 'Entry action').set_value('Reverse only').run()
    by_label(app.button, 'Review reversal').click().run()
    by_label(app.button, 'Save reversal').click().run()
    assert not app.exception
    saved = ledger.load_journal(tmp_path)[0]
    assert ledger.replay(saved).cash['a', 'EUR'] == 20000
    assert saved.events[-1].kind == 'void'
