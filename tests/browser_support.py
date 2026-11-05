"""Wait for a completed Streamlit render before the next synthetic interaction."""
import importlib.util
from pathlib import Path
import sys
import time

import pytest

playwright = pytest.importorskip('playwright.sync_api')
expect = playwright.expect

# Load the driver helper explicitly; tests must not rely on tools/ being on
# PYTHONPATH, and installed-app smoke drivers import it beside their entrypoint.
_spec = importlib.util.spec_from_file_location('portfolio_browser_sync',
    Path(__file__).resolve().parents[1] / 'tools' / 'browser_sync.py')
_sync = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _sync
_spec.loader.exec_module(_sync)
StreamlitReruns = _sync.StreamlitReruns



def settle(page, *, timeout=None):
    deadline = None if timeout is None else time.monotonic() + timeout / 1000
    def remaining():
        if deadline is None:
            return None
        milliseconds = (deadline - time.monotonic()) * 1000
        if milliseconds <= 0:
            raise playwright.TimeoutError('Streamlit render exceeded its remaining action timeout')
        return milliseconds
    # Script completion can arrive before React removes stale widgets or paints
    # the replacement tree. A visible control alone is not a completed rerun.
    page.evaluate("""() => {
        window.__testRenderObserver?.disconnect();
        window.__testRenderChanged = performance.now();
        window.__testLastRenderMutation = null;
        window.__testRenderObserver = new MutationObserver(mutations => {
            window.__testRenderChanged = performance.now();
            const last = mutations[mutations.length - 1];
            window.__testLastRenderMutation = {
                type: last.type, attribute: last.attributeName,
                tag: last.target.nodeName,
                testId: last.target.getAttribute?.('data-testid'),
                role: last.target.getAttribute?.('role'),
            };
        });
        // Dialogs and popovers are portalled outside stMain. Include their
        // replacement renders in the same quiet period as the page itself.
        window.__testRenderObserver.observe(document.body,
            {subtree:true, childList:true, attributes:true, characterData:true});
    }""")
    try:
        page.wait_for_function("""() =>
            document.querySelector('[data-testid=stApp]')?.getAttribute('data-test-script-state') === 'notRunning'
            && !document.querySelector('[data-stale="true"]')
            && performance.now() - window.__testRenderChanged >= 200
        """, timeout=remaining())
    except playwright.TimeoutError as exc:
        state = page.evaluate("""() => ({
            scriptState: document.querySelector('[data-testid=stApp]')?.getAttribute('data-test-script-state'),
            quietForMs: Math.round(performance.now() - window.__testRenderChanged),
            lastMutation: window.__testLastRenderMutation,
            stale: [...document.querySelectorAll('[data-stale="true"]')].slice(0, 8).map(el => ({
                tag: el.tagName, testId: el.getAttribute('data-testid'), role: el.getAttribute('role'),
            })),
            exceptions: [...document.querySelectorAll('[data-testid=stException]')].map(el => el.innerText),
        })""")
        raise AssertionError(f'Streamlit render did not settle: {state}') from exc
    finally:
        page.evaluate('window.__testRenderObserver?.disconnect()')
    expect(page.get_by_test_id('stException')).to_have_count(0, timeout=remaining())


def settle_layout(page):
    settle(page)
    # A completed Streamlit render can still leave an expander moving. Wait
    # for finite animations before scrolling or targeting its controls.
    page.wait_for_function("""() => !document.getAnimations().some(animation =>
        (animation.pending || animation.playState === 'running')
        && Number.isFinite(animation.effect?.getComputedTiming().endTime))""")
    settle(page)


def open_app(page, url, **kwargs):
    page.streamlit_reruns = StreamlitReruns(page, settle=settle)
    with page.expect_event('websocket', predicate=lambda socket: '/_stcore/stream' in socket.url):
        page.goto(url, **kwargs)
    page.streamlit_reruns.wait()
    settle(page)


def select_tab(page, name):
    settle(page)
    tab = page.get_by_role('tab', name=name, exact=True)
    runs = getattr(page, 'streamlit_reruns', None)
    if runs and runs.socket and tab.get_attribute('aria-selected') != 'true':
        runs.click(page, tab)
    elif tab.get_attribute('aria-selected') != 'true':
        tab.click()
    expect(tab).to_have_attribute('aria-selected', 'true')
    settle(page)
    expect(tab).to_have_attribute('aria-selected', 'true')
