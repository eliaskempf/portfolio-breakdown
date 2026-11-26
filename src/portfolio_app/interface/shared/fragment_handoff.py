"""Deliver queued render output across a fragment runner handoff in Streamlit 1.66.1."""
from functools import wraps

import streamlit


def install_fragment_handoff():
    """Remove when AppSession delivers the retiring fragment's pending output itself."""
    if streamlit.__version__ != '1.66.1':
        return
    from streamlit.runtime.app_session import AppSession, AppSessionState
    from streamlit.runtime.scriptrunner.script_runner import ScriptRunnerEvent

    if getattr(AppSession, '_portfolio_fragment_handoff', False):
        return
    create = AppSession._create_scriptrunner
    handle = AppSession._handle_scriptrunner_event_on_event_loop

    finished = {
        ScriptRunnerEvent.FRAGMENT_STOPPED_WITH_SUCCESS,
        ScriptRunnerEvent.SCRIPT_STOPPED_WITH_SUCCESS,
        ScriptRunnerEvent.SCRIPT_STOPPED_FOR_RERUN,
        ScriptRunnerEvent.SCRIPT_STOPPED_WITH_COMPILE_ERROR,
    }

    @wraps(create)
    def create_runner(session, initial_rerun_data):
        previous = session._scriptrunner
        draining = getattr(session, '_portfolio_draining_runners', None)
        if not initial_rerun_data.fragment_id:
            if draining:
                draining.clear()  # A full redraw supersedes pending render output.
        elif previous is not None:
            # The runner refused the fragment after stopping. Its completed render
            # can still have callbacks queued on the session loop. Only callbacks
            # ahead of this boundary can contribute to that render.
            if draining is None:
                draining = session._portfolio_draining_runners = {}
            draining[previous] = []
            session._event_loop.call_soon(draining.pop, previous, None)
        return create(session, initial_rerun_data)

    @wraps(handle)
    def handle_event(session, sender, event, *args, **kwargs):
        if (sender is not session._scriptrunner and event is not ScriptRunnerEvent.SHUTDOWN
                and session._state is not AppSessionState.SHUTDOWN_REQUESTED):
            pending = getattr(session, '_portfolio_draining_runners', {}).get(sender)
            if pending is not None:
                # One runner may have completed several renders before a stop.
                # Forward only through a finish event. Discard an unfinished tail
                # at the loop boundary; a retired runner cannot tear down its successor.
                pending.append((event, args, kwargs))
                if event in finished:
                    for kind, positional, named in pending:
                        handle(session, session._scriptrunner, kind, *positional, **named)
                    pending.clear()
                return
        return handle(session, sender, event, *args, **kwargs)

    AppSession._create_scriptrunner = create_runner
    AppSession._handle_scriptrunner_event_on_event_loop = handle_event
    AppSession._portfolio_fragment_handoff = True
