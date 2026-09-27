from datetime import datetime, timezone

import pytest

from portfolio_app.demo import create_demo_data
from portfolio_app.holdings import load_holdings
from portfolio_app.prices import PriceService, StaticProvider
from portfolio_app.taxonomy import load_classifications
from portfolio_app.valuation import value_holdings


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
    return create_demo_data(tmp_path_factory.mktemp("synthetic-portfolio"))


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
