"""Exercise the locked runtime's fragment handoff with a deterministic event-loop backlog."""
import asyncio
from unittest.mock import Mock

import pytest
from streamlit.proto.ClientState_pb2 import ClientState
from streamlit.proto.ForwardMsg_pb2 import ForwardMsg
from streamlit.runtime.app_session import AppSession, AppSessionState
from streamlit.runtime.script_data import ScriptData
from streamlit.runtime.scriptrunner import ScriptRunner
from streamlit.runtime.scriptrunner.script_cache import ScriptCache
from streamlit.runtime.scriptrunner.script_runner import ScriptRunnerEvent as Event
from streamlit.runtime.scriptrunner_utils.script_requests import ScriptRequestType

from portfolio_app.interface.shared.fragment_handoff import install_fragment_handoff


@pytest.mark.parametrize('request_kind', [
    'timer', 'click', 'two replacements', 'same fragment', 'full redraw',
    'late stop', 'completed stop', 'incomplete trailing run', 'stop button', 'script stop', 'session shutdown',
])
def test_finished_fragment_reaches_browser_before_replacement(monkeypatch, tmp_path, request_kind):
    install_fragment_handoff()
    # Run the actual request queues without starting worker threads. We release
    # the session loop only after the replacement, reproducing a busy renderer.
    monkeypatch.setattr(ScriptRunner, 'start', lambda self: None)
    monkeypatch.setattr(AppSession, 'register_file_watchers', lambda self: None)

    async def exercise():
        session = AppSession(ScriptData(str(tmp_path / 'synthetic.py')), Mock(), ScriptCache(), None, {})
        monkeypatch.setattr(session._fragment_storage, 'contains', lambda key: True)

        def started_message(page_hash, fragments, pages):
            message = ForwardMsg()
            message.new_session.page_script_hash = page_hash
            message.new_session.fragment_ids_this_run.extend(fragments or ())
            return message
        monkeypatch.setattr(session, '_create_new_session_message', started_message)

        def begin(fragment, *, automatic=False):
            request = ClientState(fragment_id=fragment, is_auto_rerun=automatic)
            request.widget_states.widgets.add(id='invented-selection', string_value=fragment)
            session.request_rerun(request)
            runner = session._scriptrunner
            data = runner._requests.on_scriptrunner_ready().rerun_data
            assert data.is_auto_rerun == automatic and data.widget_states == request.widget_states
            runner.on_event.send(runner, event=Event.SCRIPT_STARTED, page_script_hash='test-page',
                                 fragment_ids_this_run=[fragment] if fragment else None)
            return runner

        def emit_marker(runner, label):
            marker = ForwardMsg()
            marker.delta.new_element.text.body = label
            marker.delta.fragment_id = label
            marker.metadata.delta_path.append(len(label))
            runner.on_event.send(runner, event=Event.ENQUEUE_FORWARD_MSG, forward_msg=marker)

        def retire(runner, label, *, stop=False, event=Event.FRAGMENT_STOPPED_WITH_SUCCESS):
            emit_marker(runner, label)
            if stop:
                runner._requests.request_stop()
            runner.on_event.send(runner, event=event)
            assert runner._requests.on_scriptrunner_ready().type is ScriptRequestType.STOP

        try:
            first = begin('first')
            stopped = request_kind in ('stop button', 'script stop')
            if stopped:
                emit_marker(first, 'incomplete')
                if request_kind == 'stop button':
                    session.request_script_stop()
                else:
                    first._requests.request_stop()  # st.stop() inside the user script
            elif request_kind == 'incomplete trailing run':
                # The same worker accepts another request before retiring.
                session.request_rerun(ClientState(fragment_id='interrupted'))
                emit_marker(first, 'first')
                first.on_event.send(first, event=Event.FRAGMENT_STOPPED_WITH_SUCCESS)
                assert first._requests.on_scriptrunner_ready().type is ScriptRequestType.RERUN
                first.on_event.send(first, event=Event.SCRIPT_STARTED, page_script_hash='test-page',
                                    fragment_ids_this_run=['interrupted'])
                emit_marker(first, 'incomplete')
                session.request_script_stop()
            elif request_kind == 'session shutdown':
                retire(first, 'first', event=Event.SCRIPT_STOPPED_FOR_RERUN)
            else:
                retire(first, 'first', stop=request_kind == 'completed stop')
                if request_kind == 'late stop':
                    session.request_script_stop()
            second = begin('first' if request_kind == 'same fragment' else 'second', automatic=request_kind == 'timer')
            assert second is not first
            first.on_event.send(first, event=Event.SHUTDOWN, client_state=ClientState())
            emit_marker(first, 'too late')
            active = second
            labels = [] if stopped else ['first']
            if request_kind == 'two replacements':
                retire(second, 'second')
                active = begin('chart')
                second.on_event.send(second, event=Event.SHUTDOWN, client_state=ClientState())
                labels.append('second')
            if request_kind == 'full redraw':
                active = begin('')
                labels.clear()
            if request_kind == 'session shutdown':
                session.shutdown()
                labels.clear()
            await asyncio.sleep(0)
            if request_kind == 'session shutdown':
                assert session._state is AppSessionState.SHUTDOWN_REQUESTED
            assert session._scriptrunner is active
            messages = session.flush_browser_queue()
            visible = [] if request_kind == 'same fragment' else labels
            assert [m.delta.new_element.text.body for m in messages if m.HasField('delta')] == visible
            assert sum(m.HasField('script_finished') for m in messages) == len(labels)
            render_messages = [m for m in messages if m.HasField('new_session') or m.HasField('delta')]
            assert render_messages[-1].HasField('new_session')
            assert not any('interrupted' in m.new_session.fragment_ids_this_run for m in messages)
        finally:
            session._scriptrunner = None
            session.shutdown()
            session._script_event_loop.close()

    asyncio.run(exercise())
