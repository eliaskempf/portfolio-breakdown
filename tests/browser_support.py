"""Wait for a completed Streamlit render before the next synthetic interaction."""
import pytest

expect = pytest.importorskip('playwright.sync_api').expect


def settle(page):
    # Script completion can arrive before React removes stale widgets or paints
    # the replacement tree. A visible control alone is not a completed rerun.
    page.evaluate("""() => {
        window.__testRenderObserver?.disconnect();
        window.__testRenderChanged = performance.now();
        window.__testRenderObserver = new MutationObserver(() => {
            window.__testRenderChanged = performance.now();
        });
        window.__testRenderObserver.observe(document.querySelector('[data-testid=stMain]'),
            {subtree:true, childList:true, attributes:true, characterData:true});
    }""")
    try:
        page.wait_for_function("""() =>
            document.querySelector('[data-testid=stApp]')?.getAttribute('data-test-script-state') === 'notRunning'
            && !document.querySelector('[data-stale="true"]')
            && performance.now() - window.__testRenderChanged >= 200
        """)
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
