"""Protocol state-machine contracts; no browser process is required."""
import importlib.util
from pathlib import Path
import sys
import time
from types import SimpleNamespace
import pytest

playwright = pytest.importorskip('playwright.sync_api')
spec = importlib.util.spec_from_file_location('protocol_browser_sync', Path(__file__).resolve().parents[1]/'tools/browser_sync.py')
_sync = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = _sync
spec.loader.exec_module(_sync)
StreamlitReruns = _sync.StreamlitReruns

def test_text_commit_readiness_keeps_one_deadline_during_polling(monkeypatch):
    from types import SimpleNamespace
    browser_support = SimpleNamespace(_sync=_sync)
    clock = [0.0]
    monkeypatch.setattr(browser_support._sync, 'time', SimpleNamespace(monotonic=lambda: clock[0]))
    budgets = []
    def settle_during_poll(page, *, timeout):
        budgets.append(timeout)
        clock[0] += .4
        runs._active = (frozenset({'poll'}), {})
    runs = StreamlitReruns(SimpleNamespace(on=lambda *args: None),
                          settle=settle_during_poll, timeout_ms=1000)
    def finish_poll(*, timeout):
        runs._active = None
        runs._completed_once = True
    monkeypatch.setattr(runs, 'wait', finish_poll)
    # No field method can run: repeated polling exhausts the same deadline
    # instead of renewing the budget at every lifecycle/DOM readiness check.
    with pytest.raises(playwright.TimeoutError, match='action timeout'):
        runs.commit_text(None, object(), 'after')
    assert budgets == pytest.approx([1000, 600, 200])


def test_readiness_timeout_reports_scope_state_without_widget_values(monkeypatch):
    from types import SimpleNamespace
    runs = StreamlitReruns(SimpleNamespace(on=lambda *a: None), settle=lambda *a, **kw: None)
    runs.expected = ('synthetic-field', 'do-not-log-widget-value')
    runs._early_scopes.add('invented-dialog')
    runs._awaiting_restart = True
    def timeout(**kwargs):
        raise playwright.TimeoutError('invented transport timeout')
    monkeypatch.setattr(runs, 'wait', timeout)
    with pytest.raises(playwright.TimeoutError) as caught:
        runs._ready(None, time.monotonic() + 1)
    message = str(caught.value)
    assert 'invented-dialog' in message and 'awaiting_restart' in message
    assert 'do-not-log-widget-value' not in message


@pytest.mark.parametrize('newer_scope', [None, 'poll-a', 'poll-c'])
def test_automatic_early_finish_retires_only_its_covered_request(newer_scope):
    from types import SimpleNamespace
    from streamlit.proto.BackMsg_pb2 import BackMsg
    from streamlit.proto.ForwardMsg_pb2 import ForwardMsg
    from streamlit.runtime.forward_msg_queue import ForwardMsgQueue
    runs = StreamlitReruns(SimpleNamespace(on=lambda *a: None), settle=lambda *a, **kw: None)
    def sent(scope):
        message = BackMsg()
        message.rerun_script.fragment_id = scope
        message.rerun_script.is_auto_rerun = True
        runs._sent(message.SerializeToString())
    def started(scope):
        message = ForwardMsg()
        message.new_session.script_run_id = 'invented-' + scope
        message.new_session.fragment_ids_this_run.append(scope)
        return message
    success = ForwardMsg(script_finished=ForwardMsg.FINISHED_FRAGMENT_RUN_SUCCESSFULLY)
    sent('poll-a')
    runs._received(started('poll-a').SerializeToString())
    sent('poll-b')
    if newer_scope:
        sent(newer_scope)
        newer = runs._pending[newer_scope]
    queue = ForwardMsgQueue()
    queue.enqueue(ForwardMsg(script_finished=success.script_finished))
    # Actual Streamlit rewrites A's unflushed success when B starts. A need
    # not run again; another timer tick must not be needed to reach idle.
    queue.clear(retain_lifecycle_msgs=True, fragment_ids_this_run=['poll-b'])
    early, = queue.flush()
    assert early.script_finished == ForwardMsg.FINISHED_EARLY_FOR_RERUN
    runs._received(early.SerializeToString())
    assert not runs.idle and runs._awaiting_restart and not runs._commit_complete
    assert ('poll-a' in runs._pending) is (newer_scope == 'poll-a')
    # A stray finish cannot stand in for the required follow-up start.
    runs._received(success.SerializeToString())
    assert not runs.idle and runs._awaiting_restart
    runs._received(started('poll-b').SerializeToString())
    assert not runs.idle
    runs._received(success.SerializeToString())
    assert runs.idle is (newer_scope is None)
    if newer_scope:
        assert runs._pending == {newer_scope: newer}
        runs._received(started(newer_scope).SerializeToString())
        runs._received(success.SerializeToString())
        assert runs.idle


@pytest.mark.parametrize('text_commit', [False, True])
def test_nonautomatic_early_finish_keeps_user_request_pending(text_commit):
    from types import SimpleNamespace
    from streamlit.proto.BackMsg_pb2 import BackMsg
    from streamlit.proto.ForwardMsg_pb2 import ForwardMsg
    runs = StreamlitReruns(SimpleNamespace(on=lambda *a: None), settle=lambda *a, **kw: None)
    if text_commit:
        runs.expected = ('invented', 'after')
    request = BackMsg()
    request.rerun_script.fragment_id = 'user'
    request.rerun_script.widget_states.widgets.add(id='$$ID-synthetic-invented', string_value='after')
    runs._sent(request.SerializeToString())
    original = runs._pending['user']
    start = ForwardMsg()
    start.new_session.script_run_id = 'invented-user'
    start.new_session.fragment_ids_this_run.append('user')
    runs._received(start.SerializeToString())
    poll = BackMsg()
    poll.rerun_script.fragment_id = 'background'
    poll.rerun_script.is_auto_rerun = True
    runs._sent(poll.SerializeToString())
    runs._received(ForwardMsg(script_finished=ForwardMsg.FINISHED_EARLY_FOR_RERUN).SerializeToString())
    assert not runs.idle and runs._pending['user'] is original
    start.new_session.fragment_ids_this_run[:] = ['background']
    runs._received(start.SerializeToString())
    success = ForwardMsg(script_finished=ForwardMsg.FINISHED_FRAGMENT_RUN_SUCCESSFULLY)
    runs._received(success.SerializeToString())
    assert not runs.idle and not runs._commit_complete and runs._pending == {'user': original}
    start.new_session.fragment_ids_this_run[:] = ['user']
    runs._received(start.SerializeToString())
    runs._received(success.SerializeToString())
    assert runs.idle and runs._commit_complete is text_commit


def test_automatic_early_finish_cannot_hide_an_active_text_conflict():
    from types import SimpleNamespace
    from streamlit.proto.BackMsg_pb2 import BackMsg
    from streamlit.proto.ForwardMsg_pb2 import ForwardMsg
    runs = StreamlitReruns(SimpleNamespace(on=lambda *a: None), settle=lambda *a, **kw: None)
    runs.expected = ('invented', 'after')
    request = BackMsg()
    request.rerun_script.fragment_id = 'editor'
    request.rerun_script.widget_states.widgets.add(id='$$ID-synthetic-invented', string_value='after')
    runs._sent(request.SerializeToString())
    start = ForwardMsg()
    start.new_session.script_run_id = 'invented-editor'
    start.new_session.fragment_ids_this_run.append('editor')
    runs._received(start.SerializeToString())
    request.rerun_script.is_auto_rerun = True
    runs._sent(request.SerializeToString())
    newer = runs._pending['editor']
    runs._received(ForwardMsg(script_finished=ForwardMsg.FINISHED_EARLY_FOR_RERUN).SerializeToString())
    assert runs.error == 'Text commit overlapped an automatic request for the edited scope'
    assert runs._pending['editor'] is newer and not runs._commit_complete


def test_navigation_finish_preserves_a_newer_request_identity():
    from types import SimpleNamespace
    from streamlit.proto.BackMsg_pb2 import BackMsg
    from streamlit.proto.ForwardMsg_pb2 import ForwardMsg
    runs = StreamlitReruns(SimpleNamespace(on=lambda *a: None), settle=lambda *a, **kw: None)
    request = BackMsg()
    request.rerun_script.fragment_id = 'invented-dialog'
    started = ForwardMsg()
    started.new_session.script_run_id = 'invented-first-run'
    started.new_session.fragment_ids_this_run.append('invented-dialog')
    finished = ForwardMsg(script_finished=ForwardMsg.FINISHED_FRAGMENT_RUN_SUCCESSFULLY)
    runs._sent(request.SerializeToString())
    runs._received(started.SerializeToString())
    runs._sent(request.SerializeToString())
    newer = runs._pending['invented-dialog']
    runs._received(finished.SerializeToString())
    assert not runs.idle and runs._pending['invented-dialog'] is newer
    started.new_session.script_run_id = 'invented-newer-run'
    runs._received(started.SerializeToString())
    runs._received(finished.SerializeToString())
    assert runs.idle


@pytest.mark.parametrize('newer_fragment', [None, 'before-start', 'after-start'])
def test_baseline_full_request_supersedes_only_older_fragment_work(newer_fragment):
    from types import SimpleNamespace
    from streamlit.proto.BackMsg_pb2 import BackMsg
    from streamlit.proto.ForwardMsg_pb2 import ForwardMsg
    from streamlit.runtime.scriptrunner_utils.script_requests import RerunData, ScriptRequests
    runs = StreamlitReruns(SimpleNamespace(on=lambda *a: None), settle=lambda *a, **kw: None)
    scheduler = ScriptRequests()
    fragment = BackMsg()
    fragment.rerun_script.fragment_id = 'invented-poll'
    fragment.rerun_script.is_auto_rerun = True
    full = BackMsg()
    full.rerun_script.SetInParent()
    runs._sent(fragment.SerializeToString())
    scheduler.request_rerun(RerunData(fragment_id='invented-poll', is_auto_rerun=True))
    runs._sent(full.SerializeToString())
    scheduler.request_rerun(RerunData())
    # Actual Streamlit coalesces older fragment work into the full rerun.
    assert scheduler.on_scriptrunner_ready().rerun_data.fragment_id_queue == []
    assert set(runs._pending) == {''} and not runs.idle
    start = ForwardMsg()
    start.new_session.script_run_id = 'invented-full'
    if newer_fragment == 'before-start':
        runs._sent(fragment.SerializeToString())
    runs._received(start.SerializeToString())
    if newer_fragment == 'after-start':
        runs._sent(fragment.SerializeToString())
    runs._received(ForwardMsg(script_finished=ForwardMsg.FINISHED_SUCCESSFULLY).SerializeToString())
    assert runs.idle is (newer_fragment is None)
    if newer_fragment:
        assert set(runs._pending) == {'invented-poll'}
        start.new_session.script_run_id = 'invented-newer-poll'
        start.new_session.fragment_ids_this_run.append('invented-poll')
        runs._received(start.SerializeToString())
        runs._received(ForwardMsg(script_finished=ForwardMsg.FINISHED_FRAGMENT_RUN_SUCCESSFULLY).SerializeToString())
        assert runs.idle


def test_unsolicited_full_start_and_retired_early_keep_request_identity():
    from types import SimpleNamespace
    from streamlit.proto.BackMsg_pb2 import BackMsg
    from streamlit.proto.ForwardMsg_pb2 import ForwardMsg
    runs = StreamlitReruns(SimpleNamespace(on=lambda *a: None), settle=lambda *a, **kw: None)
    fragment = BackMsg()
    fragment.rerun_script.fragment_id = 'invented-poll'
    runs._sent(fragment.SerializeToString())
    start = ForwardMsg()
    start.new_session.script_run_id = 'invented-unsolicited-full'
    runs._received(start.SerializeToString())
    runs._received(ForwardMsg(script_finished=ForwardMsg.FINISHED_SUCCESSFULLY).SerializeToString())
    assert not runs.idle and set(runs._pending) == {'invented-poll'}
    start.new_session.fragment_ids_this_run.append('invented-poll')
    runs._received(start.SerializeToString())
    full = BackMsg()
    full.rerun_script.SetInParent()
    runs._sent(full.SerializeToString())
    runs._received(ForwardMsg(script_finished=ForwardMsg.FINISHED_EARLY_FOR_RERUN).SerializeToString())
    assert not runs._early_scopes and set(runs._pending) == {''}
    del start.new_session.fragment_ids_this_run[:]
    runs._received(start.SerializeToString())
    runs._received(ForwardMsg(script_finished=ForwardMsg.FINISHED_SUCCESSFULLY).SerializeToString())
    assert runs.idle


def test_full_request_cannot_supersede_an_active_text_commit():
    from types import SimpleNamespace
    from streamlit.proto.BackMsg_pb2 import BackMsg
    from streamlit.proto.ForwardMsg_pb2 import ForwardMsg
    runs = StreamlitReruns(SimpleNamespace(on=lambda *a: None), settle=lambda *a, **kw: None)
    runs.expected = ('invented', 'after')
    edit = BackMsg()
    edit.rerun_script.fragment_id = 'invented-editor'
    edit.rerun_script.widget_states.widgets.add(id='$$ID-synthetic-invented', string_value='after')
    runs._sent(edit.SerializeToString())
    full = BackMsg()
    full.rerun_script.is_auto_rerun = True
    runs._sent(full.SerializeToString())
    start = ForwardMsg()
    start.new_session.script_run_id = 'invented-background-full'
    runs._received(start.SerializeToString())
    runs._received(ForwardMsg(script_finished=ForwardMsg.FINISHED_SUCCESSFULLY).SerializeToString())
    assert runs.error is None and not runs._commit_complete and not runs.idle
    assert set(runs._pending) == {'invented-editor'}
