"""Compact position chart, using invented cached observations only."""
from pathlib import Path
import json
import pytest
from streamlit.testing.v1 import AppTest
from portfolio_app.futures_returns import RETURN_FILE, load_return_history
from portfolio_app.holdings import DataError
from test_futures_returns import write_synthetic_return_history
from test_ui import by_label


def test_cached_chart_and_periods_without_network_or_position_pnl(tmp_path):
    write_synthetic_return_history(tmp_path, '2026-08-05')
    before = (tmp_path / RETURN_FILE).read_bytes()
    app = AppTest.from_string(f'''
from pathlib import Path
from portfolio_app.futures import FuturePosition
from portfolio_app.futures_position_ui import render_index_chart
render_index_chart(FuturePosition(), Path({str(tmp_path)!r}))
''').run()
    assert not app.exception
    assert any('ETF proxy' in c.value for c in app.caption)
    for period in ['1M', '3M', '1Y', 'All']:
        by_label(app.get('button_group'), 'Chart period').set_value(period).run()
        assert not app.exception
        spec = json.loads(app.get('plotly_chart')[0].proto.spec)
        assert spec['data'][0]['y'][0] == 100.
        assert len(spec['data'][0]['y']) > 2
    assert (tmp_path / RETURN_FILE).read_bytes() == before


def test_missing_and_invalid_proxy_history_is_not_fabricated(tmp_path):
    assert load_return_history(tmp_path) is None
    write_synthetic_return_history(tmp_path, '2026-08-05')
    path = tmp_path / RETURN_FILE
    raw = json.loads(path.read_text())
    raw['prices'][0] = -1.
    path.write_text(json.dumps(raw))
    with pytest.raises(DataError):
        load_return_history(tmp_path)
    app = AppTest.from_string(f'''
from pathlib import Path
from portfolio_app.futures import FuturePosition
from portfolio_app.futures_position_ui import render_index_chart
render_index_chart(FuturePosition(), Path({str(tmp_path)!r}))
''').run()
    assert not app.exception and not app.get('plotly_chart')
    assert app.info
