"""Driver-side synchronization for serialized Streamlit text edits.

This module belongs to browser test/smoke drivers, not the application being
tested. Install the observer before navigation and inject the driver's DOM
settling function. The wire protocol has no request IDs: overlapping edits or
automatic requests for the edited scope are diagnosed, not guessed at.

Callers must not prefill fields outside this helper: an unchanged DOM value is
only a no-op when it came from the acknowledged render. Navigation may perform
ordered server-directed early-rerun handoffs across scopes; an active text edit
must finish in its requested scope. Disable source watching in the tested app.
"""
import re
import time
from dataclasses import dataclass

from playwright.sync_api import TimeoutError, expect
from streamlit.proto.BackMsg_pb2 import BackMsg
from streamlit.proto.ForwardMsg_pb2 import ForwardMsg


@dataclass(eq=False)
class _Request:
    scope: str


class StreamlitReruns:
    def __init__(self, page, *, settle, timeout_ms=10000):
        assert 0 < timeout_ms < float('inf'), 'A finite positive action timeout is required'
        self.settle = settle
        self.timeout_ms = timeout_ms
        self.socket = None
        self.expected = None
        self.request_seen = False
        self.error = None
        self._pending = {}
        self._active = None
        self._awaiting_restart = False
        self._early_scopes = set()
        self._completed_once = False
        self._commit_request = None
        self._commit_complete = False
        page.on('websocket', self._connected)

    @property
    def idle(self):
        return (self._completed_once and self._active is None
                and not self._awaiting_restart and not self._pending)

    def _connected(self, socket):
        if '/_stcore/stream' not in socket.url:
            return
        self.socket = socket
        self._pending.clear()
        self._active = None
        self._awaiting_restart = False
        self._early_scopes.clear()
        self._completed_once = False
        socket.on('framesent', self._sent)
        socket.on('framereceived', self._received)

    def _sent(self, payload):
        message = BackMsg.FromString(payload)
        if message.WhichOneof('type') != 'rerun_script':
            return
        rerun = message.rerun_script
        request = _Request(rerun.fragment_id)
        if self.expected is not None:
            if rerun.is_auto_rerun:
                # Independent polling fragments are legitimate. A same-scope
                # poll cannot be distinguished from this edit by NewSession.
                if (self._commit_request is not None and not self._commit_complete
                        and request.scope == self._commit_request.scope):
                    self.error = 'Text commit overlapped an automatic request for the edited scope'
            else:
                key, value = self.expected
                matches = [widget for widget in rerun.widget_states.widgets
                           if re.sub(r'[^a-zA-Z0-9_-]', '-', widget.id.split('-', 2)[-1]) == key
                           and widget.WhichOneof('value') == 'string_value'
                           and widget.string_value == value]
                if self.request_seen or len(matches) != 1 or request.scope in self._pending:
                    self.error = 'Text commit overlapped another request or sent an unexpected widget value'
                self.request_seen = True
                self._commit_request = request
        # A request arriving during a run replaces the pending identity, so that
        # older run's completion cannot clear the newer request.
        self._early_scopes.discard(request.scope)
        self._pending[request.scope] = request

    def _received(self, payload):
        message = ForwardMsg.FromString(payload)
        kind = message.WhichOneof('type')
        if kind == 'new_session':
            scopes = frozenset(message.new_session.fragment_ids_this_run)
            covered = {scope: request for scope, request in self._pending.items()
                       if scope in scopes or (not scopes and not scope)}
            # During baseline navigation, st.rerun can hand a full-page callback
            # off to a dialog fragment. Ordered EARLY/new-session messages are
            # evidence for that continuation, not an echoed request ID. Never
            # use this inference to acknowledge an active text edit in a different
            # scope; nor consume a separately requested polling fragment.
            if not covered or all(scope in self._early_scopes for scope in covered):
                covered.update({scope: self._pending[scope] for scope in self._early_scopes
                                if scope in self._pending
                                and (self.expected is None or self._pending[scope] is not self._commit_request)})
            # Even an unsolicited/background run invalidates the prior idle
            # state. Its finish only acknowledges the scopes it actually ran.
            self._active = (scopes, covered)
            self._awaiting_restart = False
        elif kind == 'script_finished':
            status = message.script_finished
            if status == ForwardMsg.FINISHED_WITH_COMPILE_ERROR:
                self.error = 'Streamlit script failed to compile'
            elif self._active is not None:
                scopes, covered = self._active
                if status == ForwardMsg.FINISHED_EARLY_FOR_RERUN:
                    self._early_scopes.update(covered)
                    self._active = None
                    self._awaiting_restart = True
                elif status == (ForwardMsg.FINISHED_FRAGMENT_RUN_SUCCESSFULLY if scopes
                                else ForwardMsg.FINISHED_SUCCESSFULLY):
                    for scope, request in covered.items():
                        if self._pending.get(scope) is request:
                            del self._pending[scope]
                            self._early_scopes.discard(scope)
                            if request is self._commit_request:
                                self._commit_complete = True
                    self._active = None
                    self._completed_once = True

    def wait(self, *, timeout=None):
        assert self.socket is not None, 'Install StreamlitReruns before navigating'
        if not self.idle and not self.error:
            self.socket.wait_for_event('framereceived', predicate=lambda _: self.idle or self.error,
                                       timeout=self.timeout_ms if timeout is None else timeout)
        assert self.error is None, self.error

    def commit_text(self, page, field, value):
        """Commit one changed, keyed field from an acknowledged idle baseline."""
        deadline = time.monotonic() + self.timeout_ms / 1000
        self._ready(page, deadline)
        if field.input_value(timeout=self._remaining(deadline)) == value:
            return
        key = field.evaluate('''element => {
            for (let node=element; node; node=node.parentElement) {
                const key=[...node.classList].find(name => name.startsWith('st-key-'));
                if (key) return key.slice(7);
            }
        }''', timeout=self._remaining(deadline))
        assert key, 'Text commit synchronization requires a keyed Streamlit widget'
        self.expected = (key, value)
        self.request_seen = False
        self._commit_request = None
        self._commit_complete = False
        try:
            with self.socket.expect_event('framereceived', predicate=lambda _: (
                self.error or (self._commit_complete and self.idle)), timeout=self._remaining(deadline)):
                field.fill(value, timeout=self._remaining(deadline))
                field.press('Enter', timeout=self._remaining(deadline))
            assert self.error is None, self.error
        finally:
            self.expected = None
        self._ready(page, deadline)
        expect(field).to_have_value(value, timeout=self._remaining(deadline))

    @staticmethod
    def _remaining(deadline):
        remaining = (deadline - time.monotonic()) * 1000
        if remaining <= 0:
            raise TimeoutError('Streamlit text commitment exceeded its action timeout')
        return remaining

    def _ready(self, page, deadline):
        # If a polling run starts during DOM settling, finish that run and its
        # render too. This repeats readiness checks, never a user action.
        while True:
            self.wait(timeout=self._remaining(deadline))
            self.settle(page, timeout=self._remaining(deadline))
            if self.idle:
                return
