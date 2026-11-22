"""Review, cancel and conflicting writes using invented account facts."""
from streamlit.testing.v1 import AppTest

from portfolio_app.ledger import account_journal as ledger
from test_actual_portfolio import invented
from test_ui import by_label
from list_helpers import list_data


def launch_activity(directory):
    app = AppTest.from_string(f'''
from pathlib import Path
from portfolio_app.ledger.account_journal import load_journal
from portfolio_app.interface.views.history.account_activity_ui import render_activity
directory = Path({str(directory)!r})
journal, revision = load_journal(directory)
render_activity(directory, journal, revision, key='accounts_activity_')
''')
    app.session_state['accounts_activity_panel'] = True
    return app.run()


def test_review_cancel_save_and_original_retained(tmp_path):
    _, journal, _ = invented(tmp_path)
    ledger.save_journal(tmp_path, journal, None)
    before = (tmp_path / ledger.FILE).read_bytes()
    app = launch_activity(tmp_path)
    app.session_state['accounts_activity_selected'] = 'fee'
    app.run()
    by_label(app.button, 'Correct entry').click().run()
    assert by_label(app.text_input, 'Corrected amount').value == '8.00'
    by_label(app.text_input, 'Corrected amount').set_value('3').run()
    by_label(app.button, 'Review correction').click().run()
    assert not app.exception and not app.error
    assert (tmp_path / ledger.FILE).read_bytes() == before
    by_label(app.button, 'Cancel correction').click().run()
    assert by_label(app.button, 'Correct entry')
    assert not any(b.label == 'Save correction' for b in app.button)
    by_label(app.button, 'Correct entry').click().run()
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
    app.session_state['accounts_activity_selected'] = 'fee'
    app.run()
    by_label(app.button, 'Correct entry').click().run()
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
    table = list_data(app, 'Recorded transactions')
    assert [(row['Action'], row['id']) for row in table['rows']] == [('Fee', 'fee')]
    assert next(c for c in table['columns'] if c['key'] == 'Action')['secondary'] == 'Note'
    assert not {'id', 'entry_id', 'reference', 'account_id', 'position_id'} & {c['key'] for c in table['columns']}
    assert not any(b.label == 'Reverse only' for b in app.button)
    app.session_state['accounts_activity_selected'] = 'fee'
    app.run()
    by_label(app.button, 'Reverse only').click().run()
    by_label(app.button, 'Review reversal').click().run()
    assert by_label(app.button, 'Save reversal')
    by_label(app.button, 'Cancel reversal').click().run()
    by_label(app.button, 'Back to transactions').click().run()
    assert by_label(app.multiselect, 'Transaction actions').value == ['Fee']
    app.session_state['accounts_activity_selected'] = 'fee'
    app.run()
    by_label(app.button, 'Reverse only').click().run()
    assert not any(b.label == 'Save reversal' for b in app.button)
    by_label(app.button, 'Review reversal').click().run()
    by_label(app.button, 'Save reversal').click().run()
    assert not app.exception
    saved = ledger.load_journal(tmp_path)[0]
    assert ledger.replay(saved).cash['a', 'EUR'] == 20000
    assert saved.events[-1].kind == 'void'


def test_closed_activity_skips_history_preparation(tmp_path, monkeypatch):
    _, journal, _ = invented(tmp_path)
    ledger.save_journal(tmp_path, journal, None)
    app = launch_activity(tmp_path)
    assert list_data(app, 'Recorded transactions')['rows']
    def unexpected(_journal):
        raise AssertionError('Closed journal prepared its history')
    monkeypatch.setattr('portfolio_app.interface.views.history.account_activity_ui.activity_rows', unexpected)
    app.session_state['accounts_activity_panel'] = False
    app.run()
    assert not app.exception and not app.get('bidi_component')
