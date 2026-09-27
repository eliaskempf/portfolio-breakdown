"""Optional Chromium regressions for chart navigation and theme changes.

Run with ``uv run --with playwright pytest tests/test_dashboard_browser.py``.
Only an invented in-memory portfolio is used; no prices or user data are read.
"""

import socket
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import urlopen

import pytest

from test_grid_browser import page as page, playwright


@pytest.fixture(scope="module")
def grid_url(tmp_path_factory):
    directory = tmp_path_factory.mktemp("synthetic-dashboard")
    app = directory / "app.py"
    app.write_text('''
import pandas as pd
import streamlit as st
from portfolio_app.allocation import Allocation, Bucket
from portfolio_app.presentation import apply_style
from portfolio_app.strategic_ui import render_strategic_overview
from portfolio_app.search_widget import render_search_box
st.set_page_config(layout="wide")
apply_style()
config = Allocation((Bucket("first category:α", "First category", target=.65),
                     Bucket("second", "Second category", target=.25),
                     Bucket("empty", "Empty category", target=.1)))
positions = pd.DataFrame([
    dict(position_id="p1", name="Invented Alpha", bucket_id="first category:α", account="", shares=1., current_value_eur=75., within_bucket_target=1.),
    dict(position_id="p2", name="Invented Beta", bucket_id="second", account="", shares=1., current_value_eur=25., within_bucket_target=1.),
])
render_strategic_overview(positions, config)
render_search_box("", [], "")
''')
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    process = subprocess.Popen(
        [sys.executable, "-m", "streamlit", "run", str(app),
         "--server.headless=true", "--server.address=127.0.0.1", f"--server.port={port}",
         "--server.fileWatcherType=none", "--browser.gatherUsageStats=false"],
        cwd=directory, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(100):
            assert process.poll() is None
            try:
                with urlopen(f"{url}/_stcore/health", timeout=1) as response:
                    if response.status == 200:
                        break
            except URLError:
                time.sleep(.1)
        else:
            pytest.fail("Synthetic server did not start")
        yield url
    finally:
        process.terminate()
        process.wait(timeout=10)


def test_chart_click_updates_category_positions_and_back(page):
    chart = page.locator(".js-plotly-plot").first
    page.wait_for_function("!!document.querySelector('.js-plotly-plot')?._ev?._events?.plotly_sunburstclick")
    label = chart.locator('text[data-unformatted="First category"]')
    label.scroll_into_view_if_needed()
    bounds = label.bounding_box()
    page.mouse.click(bounds["x"] + bounds["width"] / 2, bounds["y"] + bounds["height"] / 2)
    page.wait_for_function("document.querySelector('.js-plotly-plot')?.data[0].labels[0] === 'First category'")
    allocation = page.get_by_role('table', name='Allocation', exact=True)
    playwright.expect(allocation.get_by_text('Invented Alpha', exact=True)).to_have_count(1)
    playwright.expect(allocation.get_by_text('Invented Beta', exact=True)).to_have_count(0)
    positions = page.get_by_role('table', name='Positions', exact=True)
    playwright.expect(positions.get_by_text('Invented Alpha', exact=True)).to_have_count(1)
    playwright.expect(positions.get_by_text('Invented Beta', exact=True)).to_have_count(0)
    page.get_by_role("button", name="Back", exact=True).click()
    page.wait_for_function("document.querySelector('.js-plotly-plot')?.data[0].labels[0] === 'Portfolio'")
    page.get_by_role("combobox", name="Category", exact=True).click()
    page.get_by_role("option", name="Empty category", exact=True).click()
    playwright.expect(page.get_by_text("No current holdings in this category.", exact=True)).to_be_visible()
    playwright.expect(chart).to_have_count(0)


def test_dark_mode_reaches_chart_and_search_and_survives_reload(page):
    page.get_by_role("button", name="Main menu", exact=True).click()
    page.get_by_test_id("stMainMenuItem-theme-Dark").click()
    page.keyboard.press("Escape")
    background = page.get_by_test_id("stApp").evaluate("el => getComputedStyle(el).backgroundColor")
    assert sum(int(c) for c in background.removeprefix("rgb(").removesuffix(")").split(",")) < 200
    search_color = page.get_by_role('searchbox', name='Filter positions').evaluate("el => getComputedStyle(el).color")
    assert sum(int(c) for c in search_color.removeprefix("rgb(").removesuffix(")").split(",")) > 500
    chart = page.locator(".js-plotly-plot").first
    playwright.expect(chart).to_be_visible()
    assert chart.evaluate("el => el._fullLayout.paper_bgcolor") == "rgba(0, 0, 0, 0)"
    page.reload()
    page.get_by_role('table', name='Allocation', exact=True).wait_for()
    assert page.get_by_test_id("stApp").evaluate("el => getComputedStyle(el).backgroundColor") == background
