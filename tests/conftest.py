from datetime import datetime, timezone

import pytest

from synthetic_sample import create_sample_data
from portfolio_app.holdings import load_holdings
from portfolio_app.prices import PriceService, StaticProvider
from portfolio_app.taxonomy import load_classifications
from portfolio_app.valuation import value_holdings


@pytest.fixture(autouse=True)
def isolated_application_paths(tmp_path, monkeypatch):
    monkeypatch.setenv('PORTFOLIO_STATE_DIR', str(tmp_path / 'app-state'))
    monkeypatch.setattr('portfolio_app.settings.user_data_path', lambda *a, **kw: tmp_path / 'data')


def pytest_sessionfinish(session, exitstatus):
    # Browser CI must not turn missing Playwright or missing browsers into green skips.
    import os
    if os.environ.get('PORTFOLIO_REQUIRE_BROWSER') == '1':
        reporter = session.config.pluginmanager.get_plugin('terminalreporter')
        skipped_browsers = reporter and any(
            report.nodeid.split('::', 1)[0].endswith('browser.py')
            for report in reporter.stats.get('skipped', [])
        )
        # POSIX-only lifecycle tests legitimately skip on Windows. Browser
        # test/module skips must still fail the required-browser job.
        if skipped_browsers or session.testscollected == 0:
            session.exitstatus = 1


@pytest.fixture(autouse=True)
def stateful_layout_test_support(monkeypatch):
    # Streamlit 1.63's AppTest does not serialize tab-container widget state.
    # Supply the same value the browser sends, without changing app behavior.
    from streamlit.testing.v1.element_tree import ElementTree
    from streamlit.proto.WidgetStates_pb2 import WidgetState
    original = ElementTree.get_widget_states
    def states(tree):
        result = original(tree)
        for node in tree:
            if node.type == 'tab_container' and node.proto.tab_container.id:
                identity = node.proto.tab_container.id
                result.widgets.append(WidgetState(id=identity, string_value=tree.session_state[identity]))
        return result
    monkeypatch.setattr(ElementTree, 'get_widget_states', states)


@pytest.fixture(autouse=True)
def no_background_network(monkeypatch):
    # UI tests use synthetic workspaces even when exercising the live-mode UI.
    # Background refresh is tested separately with injected offline providers.
    from portfolio_app.etf_refresh import coordinator
    monkeypatch.setattr(coordinator, 'schedule', lambda *args, **kwargs: False)


@pytest.fixture(scope="session")
def sample_data_dir(tmp_path_factory):
    return create_sample_data(tmp_path_factory.mktemp("synthetic-portfolio"))


@pytest.fixture
def now():
    return datetime(2026, 9, 5, 12, tzinfo=timezone.utc)


@pytest.fixture
def holdings(sample_data_dir):
    return load_holdings(sample_data_dir / "holdings.csv")


@pytest.fixture
def classifications(sample_data_dir):
    return load_classifications(sample_data_dir / "classifications.yaml")


@pytest.fixture
def service(now, sample_data_dir):
    return PriceService(StaticProvider(sample_data_dir / "demo_prices.json"), now=lambda: now)


@pytest.fixture
def valued(holdings, service):
    return value_holdings(holdings, service)


@pytest.fixture(autouse=True)
def browser_diagnostics(request, monkeypatch):
    """Opt-in slowdown and failure evidence for synthetic browser tests only."""
    import os
    if not request.node.nodeid.split('::')[0].endswith('browser.py'):
        return
    evidence = os.environ.get('PORTFOLIO_BROWSER_EVIDENCE')
    rate = float(os.environ.get('PORTFOLIO_TEST_CPU_RATE', '1'))
    if not evidence and rate == 1:
        return
    from playwright.sync_api import Browser
    original = Browser.new_page
    request.node.synthetic_pages = []
    def new_page(browser, *args, **kwargs):
        page = original(browser, *args, **kwargs)
        request.node.synthetic_pages.append(page)
        if rate != 1:
            page.context.new_cdp_session(page).send('Emulation.setCPUThrottlingRate', {'rate': rate})
        if evidence:
            page.context.tracing.start(screenshots=True, snapshots=True, sources=False)
        return page
    monkeypatch.setattr(Browser, 'new_page', new_page)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    import os
    evidence = os.environ.get('PORTFOLIO_BROWSER_EVIDENCE')
    if not evidence or report.when != 'call' or not report.failed:
        return
    from hashlib import sha256
    from pathlib import Path
    root = Path(evidence) / sha256(item.nodeid.encode()).hexdigest()[:16]
    root.mkdir(parents=True, exist_ok=True)
    (root / 'test.txt').write_text(item.nodeid + '\n' + str(report.longrepr), encoding='utf-8')
    for index, page in enumerate(getattr(item, 'synthetic_pages', [])):
        if page.is_closed():
            continue
        try:
            page.screenshot(path=str(root / f'page-{index}.png'))
            (root / f'page-{index}.html').write_text(page.content(), encoding='utf-8')
            page.context.tracing.stop(path=str(root / f'trace-{index}.zip'))
        except Exception as exc:
            (root / f'capture-{index}.txt').write_text(str(exc), encoding='utf-8')
