# Startup and animation development

No executable rebuild is needed for UI, animation or launcher iteration.
Use `uv sync --locked` in the release checkout before starting these commands.

## Animation workbench

```sh
uv run streamlit run src/portfolio_app/intro_preview.py --server.port=8512
```

This preview reads no portfolio files and makes no market requests. It provides
hold/swirl timing sliders and Replay. Timing changes apply to the next replay.
The component respects the system reduced-motion preference. Its default is
950 ms wordmark hold plus 1850 ms transition; no artificial market-data wait.

The runtime asset is `src/portfolio_app/intro_frontend/index.html`. It is a
self-contained SVG/CSS/JavaScript document with no npm build or external requests.
Open it directly in a browser for replay, pause and timeline scrubbing. This same
file is served by the app and bundled in the wheel/frozen application.

The user-supplied runtime component was copied from the animation handoff into
source. The original supplied files remain intact. The final SVG uses the existing
app icon geometry, not the favicon variant. No personal portfolio contents were
included with the animation. Edit the runtime HTML for local iteration; refresh
the preview after changing it. Python wrapper changes require a server restart.

## Complete welcome/header flow

Choose a NEW disposable workspace, separate from any actual portfolio:

```sh
uv run portfolio-app --foreground --offline-demo --data-dir /tmp/portfolio-startup-example
```

On Windows, use an equivalent new temporary path. The app chooses a free loopback
port and opens the browser. The intro precedes a two-choice welcome dialog.
Use Explore demo to check the header, workspace dropdown, question-mark help,
Settings, navigation and position dialogs without market network access. Use
Start my portfolio to check empty Positions and its Add/import actions.

Opening a fresh browser session replays the intro. Ordinary reruns and workspace
switches do not. Skip remains available if the animation component cannot load.
Use `--skip-intro` while iterating on unrelated views, or `--demo` to start with
the demo selected. Omitting `--offline-demo` enables public demo market data.

Source desktop launch uses the same lifecycle as the frozen desktop entry:

```sh
uv run portfolio-desktop --offline-demo --data-dir /tmp/portfolio-startup-example
uv run portfolio-app --data-dir /tmp/portfolio-startup-example --stop
```

Stop only your own instance. Restart it with the same workspace/options after
changing imported Python modules. A browser refresh or healthy endpoint alone
is not verification. Visit the affected actual preview views after restart.
Close the browser tab when finished; closing it does not stop the managed server.

## Windows package contract

`Portfolio Breakdown.exe` is the console-free entry and shortcut target.
`portfolio-app.exe` is the console companion for CLI output and the hidden server.
Both share the same `_internal` directory; keep the complete bundle together.
The console-free entry initializes private diagnostic logging before application
imports and retains native startup-error reporting. The existing browser launch
is still the default UI; a dedicated webview is a separate optional prototype.

Source previews validate the animation and UI rapidly. They cannot establish
that the Windows bootloader/shortcut produces no console flash. Verify that on
a new Windows candidate, including startup failure, repeat launch and stop/restart.
The candidate smoke check exercises the GUI entry on Windows with an isolated
workspace and a browser check of its server, alongside the console CLI checks.

## Automated checks

```sh
uv run pytest tests/test_intro_browser.py tests/test_onboarding_ui.py tests/test_launcher.py
uv run python tools/package_smoke.py
```

Install Chromium with `uv run playwright install chromium` after syncing the
browser dependency group. Tests use invented workspaces. They cover animation
completion/skip/reduced motion, final SVG geometry, replay, two welcome choices,
help/settings, dropdown isolation and narrow layouts. Package smoke verifies the
actual bundled animation, navigation, CSV/XLSX import, manual ETF setup and lifecycle.
