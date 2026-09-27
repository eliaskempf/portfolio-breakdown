"""Optional real-browser regressions; all data is invented in a temporary app.

Run with ``uv run --with playwright pytest tests/test_grid_browser.py`` after
installing Playwright's Chromium. PORTFOLIO_TEST_CHROMIUM can select a local binary.
Streamlit AppTest does not execute the grid's JavaScript.
"""

import os
import socket
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import urlopen

import pytest

playwright = pytest.importorskip("playwright.sync_api")


@pytest.fixture(scope="module")
def grid_url(tmp_path_factory):
    directory = tmp_path_factory.mktemp("synthetic-grid")
    app = directory / "app.py"
    app.write_text('''
import pandas as pd
import streamlit as st
from portfolio_app.presentation import apply_style
st.set_page_config(layout="wide")
apply_style()
st.html('<div style="height:350px">Synthetic keyboard test</div>')
with st.form("synthetic"):
    st.data_editor(pd.DataFrame({
        "ID": ["sample-a", "sample-b"],
        "Name": ["Invented A", "Invented B"],
        "Target": [0.0, 0.0],
        "Protected": [False, False],
        "Reference": ["read only", "read only"],
    }), disabled=["Reference"], hide_index=True, column_config={
        "ID": st.column_config.TextColumn(width=150),
        "Name": st.column_config.TextColumn(width=150),
        "Target": st.column_config.NumberColumn(width=150),
        "Protected": st.column_config.CheckboxColumn(width=150),
    })
    st.text_input("First ordinary input")
    st.text_input("Second ordinary input")
    st.form_submit_button("Save synthetic form")
st.html('<div style="height:1200px"></div>')
''')
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    process = subprocess.Popen(
        [sys.executable, "-m", "streamlit", "run", str(app),
         "--server.headless=true", "--server.address=127.0.0.1",
         f"--server.port={port}", "--server.fileWatcherType=none",
         "--browser.gatherUsageStats=false"],
        cwd=directory, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(100):
            assert process.poll() is None, "Synthetic Streamlit server exited"
            try:
                with urlopen(f"{url}/_stcore/health", timeout=1) as response:
                    if response.status == 200:
                        break
            except URLError:
                time.sleep(.1)
        else:
            pytest.fail("Synthetic Streamlit server did not start")
        yield url
    finally:
        process.terminate()
        process.wait(timeout=10)


@pytest.fixture
def page(grid_url):
    with playwright.sync_playwright() as runner:
        browser = runner.chromium.launch(
            executable_path=os.environ.get("PORTFOLIO_TEST_CHROMIUM"),
            args=["--no-sandbox"],
        )
        page = browser.new_page(viewport={"width": 1400, "height": 800})
        page.goto(grid_url)
        page.wait_for_function("!!window.__portfolioGridInteractions")
        # The dashboard uses the shared read-only table; editor tests use Glide.
        page.locator('[data-testid="stDataFrame"], table[aria-label="Allocation"]').first.wait_for()
        yield page
        browser.close()


def open_id(page):
    canvas = page.get_by_test_id("stDataFrame").locator("canvas").first
    canvas.scroll_into_view_if_needed()
    bounds = canvas.bounding_box()
    page.mouse.dblclick(bounds["x"] + 70, bounds["y"] + 48)
    editor = page.locator('[id^="gdg-overlay-"]')
    playwright.expect(editor.locator("textarea,input").first).to_be_focused()
    return canvas, editor, editor.locator("textarea,input").first


def test_editor_follows_cell_and_retains_draft_when_scrolled_offscreen(page):
    canvas, editor, field = open_id(page)
    field.fill("synthetic-draft")
    offset = editor.bounding_box()["y"] - canvas.bounding_box()["y"]
    main = page.get_by_test_id("stMain")
    main.evaluate("el => el.scrollTop += 160")
    page.wait_for_timeout(100)
    assert editor.bounding_box()["y"] - canvas.bounding_box()["y"] == pytest.approx(offset, abs=1)
    playwright.expect(field).to_have_value("synthetic-draft")
    main.evaluate("el => el.scrollTop += 900")
    playwright.expect(editor).to_have_css("opacity", "0")
    assert editor.count() == 1  # Hidden, never closed or committed by scrolling.
    main.evaluate("el => el.scrollTop = 0")
    playwright.expect(editor).to_have_css("opacity", "1")
    playwright.expect(field).to_have_value("synthetic-draft")
    playwright.expect(field).to_be_focused()
    field.press("Escape")
    playwright.expect(editor).to_have_count(0)
    playwright.expect(page.locator('[role="gridcell"]').first).to_have_text("sample-a")


def test_tab_commits_and_opens_next_field_shift_tab_returns_without_toggling(page):
    _, editor, field = open_id(page)
    field.fill("new-synthetic-id")
    field.press("Tab")
    playwright.expect(field).to_have_value("Invented A")
    playwright.expect(field).to_be_focused()
    field.fill("New invented name")
    field.press("Shift+Tab")
    playwright.expect(field).to_have_value("new-synthetic-id")
    field.press("Tab")
    playwright.expect(field).to_have_value("New invented name")
    field.press("Tab")
    playwright.expect(field).to_have_attribute("inputmode", "numeric")
    field.fill("25")
    field.press("Tab")
    playwright.expect(editor).to_have_count(0)
    selected = page.locator('[role="gridcell"][aria-selected="true"]')
    playwright.expect(selected).to_have_text("false")
    playwright.expect(page.locator('[role="gridcell"]').nth(2)).to_have_text("25")
    playwright.expect(selected).to_be_focused()
    page.keyboard.press("Enter")
    page.keyboard.press("Tab")
    playwright.expect(selected).to_have_attribute("aria-readonly", "true")
    playwright.expect(page.locator('[role="gridcell"]').nth(3)).to_have_text("true")
    playwright.expect(editor).to_have_count(0)
    first = page.get_by_role("textbox", name="First ordinary input")
    first.fill("Synthetic plain input")
    first.press("Tab")
    playwright.expect(page.get_by_role("textbox", name="Second ordinary input")).to_be_focused()
