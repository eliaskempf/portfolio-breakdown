"""Required browser checks must coexist with platform-specific unit tests."""
from types import SimpleNamespace

import pytest

from conftest import pytest_sessionfinish


@pytest.mark.parametrize('required,collected,skipped,expected', [
    ('1', 10, ['tests/test_posix_desktop.py::test_parent_eof'], 0),
    ('1', 10, ['tests/test_import_browser.py::test_import'], 1),
    ('1', 10, ['tests/test_import_browser.py'], 1),
    ('1', 0, [], 1),
    ('0', 10, ['tests/test_import_browser.py'], 0),
])
def test_browser_gate_distinguishes_platform_and_browser_skips(monkeypatch, required, collected, skipped, expected):
    monkeypatch.setenv('PORTFOLIO_REQUIRE_BROWSER', required)
    reporter = SimpleNamespace(stats={'skipped': [SimpleNamespace(nodeid=node) for node in skipped]})
    session = SimpleNamespace(testscollected=collected, exitstatus=0,
        config=SimpleNamespace(pluginmanager=SimpleNamespace(get_plugin=lambda name: reporter)))
    pytest_sessionfinish(session, 0)
    assert session.exitstatus == expected
