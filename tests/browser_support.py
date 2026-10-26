"""Wait for a completed Streamlit render before the next synthetic interaction."""
import pytest

playwright = pytest.importorskip('playwright.sync_api')
expect = playwright.expect


def settle(page):
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
        """)
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
    expect(page.get_by_test_id('stException')).to_have_count(0)


def select_tab(page, name):
    settle(page)
    tab = page.get_by_role('tab', name=name, exact=True)
    tab.click()
    expect(tab).to_have_attribute('aria-selected', 'true')
    settle(page)
    expect(tab).to_have_attribute('aria-selected', 'true')
