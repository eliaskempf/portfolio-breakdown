"""Hosted panel input guards and ownership, without loading macOS frameworks."""
from collections import Counter
import os
from types import SimpleNamespace

import pytest

from portfolio_app.cocoa_diagnostics import HostedPanelInput


@pytest.fixture
def hosted(monkeypatch):
    for key, value in [('PORTFOLIO_TEST_HOSTED_INPUT', '1'), ('GITHUB_ACTIONS', 'true'),
                       ('RUNNER_ENVIRONMENT', 'github-hosted')]:
        monkeypatch.setenv(key, value)


@pytest.mark.parametrize('missing', ['PORTFOLIO_TEST_HOSTED_INPUT', 'GITHUB_ACTIONS', 'RUNNER_ENVIRONMENT'])
def test_panel_input_refuses_non_hosted_process_before_loading_frameworks(hosted, monkeypatch, missing):
    monkeypatch.delenv(missing)
    with pytest.raises(RuntimeError, match='disposable hosted probe'):
        HostedPanelInput()


@pytest.fixture
def panel(hosted):
    client = HostedPanelInput.__new__(HostedPanelInput)
    refs, presses = Counter(), []
    def retain(value):
        refs[value] += 1
        return value
    def release(value):
        refs[value] -= 1
        assert refs[value] >= 0
    attributes = {
        'window': {'AXTitle': 'Open', 'AXChildren': ['sidebar', 'cancel', 'open']},
        'sidebar': {'AXChildren': [f'row-{i}' for i in range(40)]},
        'cancel': {'AXRole': 'AXButton', 'AXTitle': 'Cancel', 'AXEnabled': True},
        'open': {'AXRole': 'AXButton', 'AXTitle': 'Open', 'AXEnabled': True},
        'process': {'AXFocusedWindow': 'window'},
    }
    def attribute(element, name, transform=None):
        value = attributes.get(element, {}).get(name, {'ax_error': -25205})
        return transform(value) if transform and not isinstance(value, dict) else value
    client.attribute = attribute
    client.cf = SimpleNamespace(CFRetain=retain, CFRelease=release, CFGetTypeID=lambda value: 'array',
                               CFArrayGetTypeID=lambda: 'array', CFArrayGetCount=len,
                               CFArrayGetValueAtIndex=lambda value, i: value[i],
                               CFStringCreateWithCString=lambda *args: retain('AXPress'))
    client.ax = SimpleNamespace(AXIsProcessTrusted=lambda: True,
                               AXUIElementCreateApplication=lambda pid: retain('process'),
                               AXUIElementPerformAction=lambda button, action: presses.append((button, action)) or 0)
    foreground = SimpleNamespace(processIdentifier=os.getpid)
    app = SimpleNamespace(NSWorkspace=SimpleNamespace(sharedWorkspace=lambda: SimpleNamespace(
        frontmostApplication=lambda: foreground)))
    return client, app, attributes, refs, presses


def test_press_uses_real_enabled_button_and_releases_search_references(panel):
    client, app, _, refs, presses = panel
    assert client.press(app, 'Open') == 0
    assert presses == [('open', 'AXPress')]
    assert not any(refs.values())


@pytest.mark.parametrize('change', ['disabled', 'wrong_title', 'wrong_window', 'permission', 'foreground'])
def test_press_refuses_unavailable_or_unowned_button(panel, change):
    client, app, attributes, refs, presses = panel
    if change == 'disabled':
        attributes['open']['AXEnabled'] = False
    elif change == 'wrong_title':
        attributes['open']['AXTitle'] = 'Delete'
    elif change == 'wrong_window':
        attributes['window']['AXTitle'] = 'Other window'
    elif change == 'permission':
        client.ax.AXIsProcessTrusted = lambda: False
    else:
        # Lose foreground ownership after locating the button but before input.
        pids = iter([os.getpid(), -1])
        app.NSWorkspace.sharedWorkspace = lambda: SimpleNamespace(frontmostApplication=lambda:
            SimpleNamespace(processIdentifier=lambda: next(pids)))
    with pytest.raises(RuntimeError):
        client.press(app, 'Open')
    assert presses == []
    assert not any(refs.values())


@pytest.mark.parametrize('code', [-25204, -25206])
def test_press_never_repeats_an_action_even_if_callback_times_out(panel, code):
    client, app, _, refs, presses = panel
    def perform(button, action):
        presses.append((button, action))
        return code
    client.ax.AXUIElementPerformAction = perform
    if code == -25204:
        assert client.press(app, 'Open') == code  # Caller still requires exact-byte acceptance.
    else:
        with pytest.raises(RuntimeError, match='AX error'):
            client.press(app, 'Open')
    assert len(presses) == 1
    assert not any(refs.values())


def test_not_ready_button_keeps_confirmation_pending_without_duplicate_input(panel):
    from portfolio_app.cocoa_diagnostics import PanelNotReady
    from portfolio_app.desktop_controls import CocoaPanelSequence
    client, app, attributes, refs, presses = panel
    sequence = CocoaPanelSequence()
    sequence.advance(panel_ready=True, location_ready=False, selected=False)
    sequence.advance(panel_ready=False, location_ready=True, selected=False)
    sequence.advance(panel_ready=False, location_ready=True, selected=False)
    def step():
        return sequence.advance(panel_ready=True, location_ready=False, selected=True,
                                confirm=lambda: client.press(app, 'Open'))
    attributes['open']['AXEnabled'] = False
    for _ in range(3):
        with pytest.raises(PanelNotReady, match='observed buttons'):
            step()
        assert sequence.phase == 3
        assert presses == [] and not any(refs.values())
    attributes['open']['AXEnabled'] = True
    assert step() == 'confirm-panel'
    assert sequence.phase == 4
    assert step() is None
    assert presses == [('open', 'AXPress')]
    assert not any(refs.values())
