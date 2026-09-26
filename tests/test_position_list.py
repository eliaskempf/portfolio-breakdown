"""Position navigation and rename tests use invented local data only."""

from portfolio_app.holdings import load_holdings
from portfolio_app.position_list import list_context
from portfolio_app.positions import read_snapshot
from test_instrument_ui import launch_editor
from test_ui import by_label


def workspace(path):
    path.write_text('position_key,id,name,shares,account\n'
                    'first,a,Invented token,1,First\n'
                    'second,a,Invented token,2,Second\n')


def test_open_exact_position_then_rename_and_return_to_list(tmp_path):
    path = tmp_path / 'holdings.csv'
    workspace(path)
    app = launch_editor(path, 'Positions')
    app.session_state['position_edit_open_request'] = {
        'id': 'second', 'revision': read_snapshot(path).revision, 'context': list_context(path),
    }
    app.run()
    assert not app.exception
    assert by_label(app.radio, 'Position action').value == 'Edit position'
    assert by_label(app.selectbox, 'Position to edit').value == 'second'
    assert by_label(app.text_input, 'Account / broker').value == 'Second'
    assert not by_label(app.text_input, 'Instrument name').disabled
    assert by_label(app.text_input, 'Ticker').disabled
    by_label(app.text_input, 'Instrument name').set_value('My custom token')
    by_label(app.button, 'Save position').click().run()
    assert not app.exception and not app.error
    stored = load_holdings(path)
    assert stored.name.tolist() == ['My custom token', 'My custom token']
    assert stored.shares.tolist() == [1., 2.]
    assert by_label(app.radio, 'Position action').value == 'Positions'
    by_label(app.radio, 'Position action').set_value('Edit position').run()
    by_label(app.button, 'Back to positions').click().run()
    assert not app.exception
    assert by_label(app.radio, 'Position action').value == 'Positions'


def test_stale_list_event_cannot_open_another_row(tmp_path):
    path = tmp_path / 'holdings.csv'
    workspace(path)
    app = launch_editor(path, 'Positions')
    old_revision = read_snapshot(path).revision
    path.write_text('position_key,id,name,shares,account\nsecond,b,Another invented token,3,Other\n')
    app.session_state['position_edit_open_request'] = {
        'id': 'second', 'revision': old_revision, 'context': list_context(path),
    }
    app.run()
    assert not app.exception
    assert by_label(app.radio, 'Position action').value == 'Positions'
    assert any('list changed' in warning.value for warning in app.warning)
