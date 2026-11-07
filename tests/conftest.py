from datetime import datetime, timezone

import pytest

from synthetic_sample import create_sample_data
from portfolio_app.portfolio.holdings import load_holdings
from portfolio_app.market.prices import PriceService, StaticProvider
from portfolio_app.exposure.taxonomy import load_classifications
from portfolio_app.portfolio.valuation import value_holdings


@pytest.fixture(autouse=True)
def preserve_process_main_module(monkeypatch):
    # AppTest replaces __main__ with its temporary application script. Restore
    # pytest's entrypoint so later multiprocessing spawn tests can import it.
    import sys
    monkeypatch.setitem(sys.modules, '__main__', sys.modules['__main__'])


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
    # Streamlit's AppTest does not serialize stateful layout widget state.
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
            elif node.type == 'expander' and node.proto.id:
                identity = node.proto.id
                result.widgets.append(WidgetState(id=identity, bool_value=tree.session_state[identity]))
        return result
    monkeypatch.setattr(ElementTree, 'get_widget_states', states)


@pytest.fixture(autouse=True)
def no_background_network(monkeypatch):
    # UI tests use synthetic workspaces even when exercising the live-mode UI.
    # Background refresh is tested separately with injected offline providers.
    from portfolio_app.funds.etf_refresh import coordinator
    monkeypatch.setattr(coordinator, 'schedule', lambda *args, **kwargs: False)
    from portfolio_app.derivatives.settlement_collection import collector
    monkeypatch.setattr(collector, 'schedule', lambda *args, **kwargs: False)
    from portfolio_app.derivatives.futures_quotes import collector as quotes
    monkeypatch.setattr(quotes, 'schedule', lambda *args, **kwargs: False)
    from portfolio_app.derivatives.derivative_market import make_contract
    monkeypatch.setattr('portfolio_app.interface.views.derivatives.contract_maturity_ui.listed_contracts',
        lambda product: tuple(make_contract(product, day) for day in ('2027-03-19','2027-06-18')))


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


@pytest.fixture(scope='session')
def browser_pool():
    from browser_runtime import BrowserPool
    pool = BrowserPool()
    yield pool
    pool.close()


@pytest.fixture(autouse=True)
def browser_diagnostics(request):
    import os
    if not request.node.nodeid.split('::')[0].endswith('browser.py'):
        yield
        return
    from browser_runtime import Evidence, OWNER
    owner = Evidence(request.node.nodeid, os.environ.get('PORTFOLIO_BROWSER_EVIDENCE'))
    owner.pool = request.getfixturevalue('browser_pool')
    request.node.browser_evidence = owner
    token = OWNER.set(owner)
    yield
    OWNER.reset(token)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    owner = getattr(item, 'browser_evidence', None)
    if owner is None:
        return
    try:
        if report.failed:
            owner.failures.append(dict(phase=report.when, error=str(report.longrepr)))
            owner.capture()
        if report.when == 'teardown':
            owner.close()
    except Exception as exc:
        # Preserve the original assertion/setup/teardown failure.
        import warnings
        warnings.warn(f'Could not retain synthetic browser evidence: {exc}')
