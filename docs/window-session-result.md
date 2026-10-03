# Experimental Windows window prototype

## Recommendation and current evidence

The opt-in window is implemented and its source launch now passes a native
Windows 10 smoke check using an isolated Python 3.12 environment. The initial
user test exposed a startup deadlock, fixed as described below. Overview charts,
all four tabs, an authenticated focus request, window close and server shutdown
were exercised in the actual WebView2 window with synthetic data.

Keep v1 shipping approval pending the remaining Windows 11 and frozen-bundle
acceptance checks. The browser-first v1 launcher and production packaging remain
the default. No release candidate, tag, workflow run, push or publication is
produced. Linux browser checks alone are not native Windows evidence.

Implementation worktree: `/tmp/portfolio-window-v1`, branch `window-prototype-v1`.
Base: `9f1877d4e2a3222529f979782e0e90c6ee2134dc` from `release-v1`.
The integration checkout's concurrent UI/startup work was not copied or edited.
Shared launcher hooks and the optional adapter are separate commits.
Launcher integration: `0ee40fc83130bdc57fb388f0f709f97ee340b68b`.
Adapter and final validation hashes are recorded in the final handoff.

## Launch and build

Run on Windows 11 x64, from this branch's checkout using its own environment:

```powershell
uv sync --locked --extra window --group release
$env:PORTFOLIO_STATE_DIR = Join-Path $env:TEMP "portfolio-window-synthetic-state"
$workspace = Join-Path $env:TEMP "portfolio-window-synthetic-workspace"
uv run --extra window portfolio-window --data-dir $workspace --offline-demo --server.port 8529
```

Use an unused port and a new invented workspace. Omit `--demo` to see the empty
welcome screen; add it to open the temporary demo initially. `--offline-demo`
selects deterministic synthetic quotes and ETF data, including when selecting
Explore demo later. Normal demo mode may fetch public provider data. Do not use
real portfolio files for tests. Source launches use the invoking terminal for
stdout/stderr; private diagnostic logs also live under the selected state folder.

Explicit fallback and stop commands:

```powershell
uv run portfolio-window --browser --data-dir $workspace --offline-demo
uv run portfolio-app --data-dir $workspace --stop
```

Browser fallback retains the existing browser lifecycle. To change from an
existing window-owned server to a browser-owned server, stop it first. A browser
tab attached to a window-owned server does not acquire ownership.

Download the official SDK package for interop license notices (not a runtime
installer). This does not install software or change system settings:

```powershell
$sdk = Join-Path $env:TEMP "microsoft.web.webview2.1.0.3856.49.nupkg"
Invoke-WebRequest "https://api.nuget.org/v3-flatcontainer/microsoft.web.webview2/1.0.3856.49/microsoft.web.webview2.1.0.3856.49.nupkg" -OutFile $sdk
uv run --extra window python tools/window_build.py --webview2-sdk $sdk
```

Output: `dist/window-prototype/portfolio-window-experimental-windows-x64.zip`.
Keep the entire extracted `portfolio-window` directory together. From outside
the checkout, run `portfolio-window.exe` with the same synthetic launch options.
Use `portfolio-window.exe --browser ...` for fallback. The build refuses an
occupied output directory and refuses non-Windows hosts. The directory includes
source wheel/archive, notices, dependency and DLL inventories, checksums and size
measurements in `experimental-build.json`. Production candidate/promotion tools
and `packaging/portfolio.spec` are unchanged.

The separate windowed entrypoint initializes logs before importing application or
renderer modules. The frozen child uses internal dispatch, never another window.
The inherited standard-input pipe is recovered from its Windows handle when
Python's windowed streams are absent. Logs are per-process under `state/window`,
with bounded logging-handler rotation; raw child stdout/stderr append to private
files. They are not included in build inputs. `uv run portfolio-app` remains the
normal source-development path.

## Lifecycle and integration contract

`app.main(presentation=None)` and `launcher.run_server(..., presentation=None)`
retain default browser behavior. The optional `Presentation` protocol supplies
`prepare`, `child`, `run` and `focus`. Existing parsing, command construction,
temporary demo ownership, loopback binding, workspace lease and control endpoint
remain shared. No financial calculation, UI module or data schema changes.

- New window: acquire the existing workspace lease, validate dependencies, start
  a contained server child and run pywebview on the main thread. A worker uses the
  same server monitor as browser launches. Health timeout is 60 seconds; renderer
  initialization after server readiness has a 30-second timeout.
- Repeated launch: authenticated status identifies presentation capability.
  `POST /focus` queues restore/activation on the GUI thread. Windows may restrict
  foreground activation; manual acceptance must check taskbar/focus behavior.
- Existing browser: reopen its URL and exit without acquiring server ownership.
  Old status responses without presentation metadata are treated as browsers.
  Live/demo mismatch remains an error. A competing startup retries lease/discovery
  for five seconds, then asks the user to retry; it never starts a second server.
- Close/Stop/failure: stop monitoring, close the owned child's input pipe, and
  allow its installed Streamlit shutdown handler up to ten seconds. Escalation
  targets only the owned process handle and Job Object. The lease stays held
  through child and descendant cleanup. Closing a window stops its server even
  if a browser tab is attached.
- Crash: a non-inheritable kill-on-close Windows Job Object contains the server
  and descendants. The child waits on a pipe gate before importing Streamlit;
  the parent opens that gate only after successful job assignment. A parent crash
  before assignment closes the gate without starting a server. No persisted PID
  or port is used for termination. Hard-crash native acceptance is still required.
- Recovery: OS locks release on process exit; stale discovery records do not
  authorize shutdown and are replaced only after acquiring the lease. Portfolio
  files are never deleted. Abrupt termination can leave temporary demo/cache files.

Likely integration conflicts: `app.py`, `launcher.py`, `pyproject.toml`, `uv.lock`
and the exact privacy allowlist additions. Reconcile these with the main session's
console-free launcher patch; do not replace its launcher wholesale. The adapter,
process containment, tests, experimental spec and build helper are new files.

External HTTP(S) navigation opens in the default browser; unrelated file/data/
script schemes are blocked. The pinned backend's navigation and popup handlers
are isolated in the adapter. Uploads remain Streamlit file inputs; downloads use
pywebview's destination dialog. No application methods are exposed to JavaScript.
Renderer cache storage uses a separate temporary profile for each window,
under private application state and outside the bundle. Closing one window
cannot delete another window's profile.

## Animation and readiness

The initial surface is static text, with no animation assets. States distinguish
`starting`, `server-reachable`, `document-loaded`, `first-view-rendered` and
`failed`. A value-free DOM probe recognizes the welcome heading or selected
Overview with a rendered Plotly SVG; this is a presentation signal, not proof
that every control works. It also accepts a future
`data-portfolio-view-ready="true"` marker owned by UI integration.

A supplied cold-start animation can replace the initial surface, but cannot
cover executable/Python initialization before the GUI exists without a separate
native splash. A page animation runs after renderer navigation. Do not infer
rendered readiness from the health endpoint or document-loaded event. The current
adapter exposes a state hook for later integration; it does not implement the
main session's animation or change the default Overview/welcome behavior.

## Dependencies, licenses and package impact

The optional Windows-only `window` extra pins pywebview 6.2.1. Lock additions:
pywebview 6.2.1, pythonnet 3.2.0, clr-loader 0.3.1, bottle 0.13.4 and proxy-tools
0.1.0. Existing cffi and typing-extensions satisfy other requirements. Existing
locked dependencies were not upgraded. Default installs and Linux environments
do not install the extra's Windows-only dependencies.

Windows requires .NET Framework and Evergreen WebView2. The adapter explicitly
selects `edgechromium` and refuses a renderer fallback to MSHTML. It checks both
per-user/per-machine runtime registry views and directs missing-runtime users to
Microsoft or `--browser`. Nothing provisions a runtime globally. Windows 11
normally includes Evergreen, but detection and missing-runtime acceptance are
still required. See [Microsoft distribution guidance](https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/distribution)
and [pywebview renderers](https://pywebview.flowrl.com/guide/web_engine.html).

Pywebview is BSD-3-Clause; pythonnet/clr-loader and the small supporting packages
retain their own notices, generated from the actual build environment. Pywebview
also contains WebView2 managed/loader DLLs and its WebBrowserInterop DLLs. The
build compares all Microsoft DLL bytes to the supplied official SDK archive and
includes its LICENSE.txt and NOTICE.txt. A missing or mismatched archive fails
the build. This was verified with the pinned wheel and official 1.0.3856.49 SDK;
it does not imply Windows execution acceptance.

The inspected wheel's DLLs total 1,133,960 bytes; this excludes Python/.NET wrapper
dependencies and the separately installed WebView2 Runtime. A Windows frozen size
has not been measured. The recipe records compressed/unpacked sizes and excludes
unused Qt, GTK and CEF renderers. It uses pywebview's bundled PyInstaller hook to
collect JavaScript and interop libraries. See [pywebview freezing guidance](https://pywebview.flowrl.com/guide/freezing.html).

Linux native-window packaging is deferred: GTK requires PyGObject, GTK and
WebKitGTK system libraries; Qt would add a substantial renderer distribution.
Neither has been installed or bundled. Linux browser support remains available.
See [pywebview installation requirements](https://pywebview.flowrl.com/guide/installation.html).

## Native acceptance checklist and deferral boundary

The Windows 10 source checks recorded below cover part of this list. The complete
Windows 11 and frozen-bundle matrix remains UNVERIFIED. Run on Windows 11 x64,
first from source and then from the extracted bundle on a machine without Python/uv:

1. Double-click launch and terminal launch; no unwanted terminal flashes. Test
   readable startup errors and logs, paths with spaces/non-ASCII characters, an
   occupied explicit port and missing/unusable WebView2. Check browser fallback.
2. Empty workspace, persistent synthetic holdings, Explore demo, initial `--demo`,
   workspace switching and save/restart. Verify Overview charts, Exposure with ETF
   breakdown on/off, Positions and dialogs, Rebalance and Targets.
3. Scroll, keyboard input, resize and display scaling at 100%, 150% and 200%.
   Upload synthetic CSV and XLSX, review/cancel/save, check any available download
   and cancellation, open external help/provider links and verify the app stays put.
4. Repeat launch while starting/running/minimized; two simultaneous launches;
   existing browser reuse; mode conflicts; close, Stop application and restart.
5. Force-close the window supervisor and crash the server separately. Check that
   the server and WebView2 children exit, the lock can be reacquired, and unrelated
   browser/apps plus synthetic portfolio files survive. Repeat during startup.
6. Review extracted contents, dependency/native notices, runtime prerequisites,
   checksum/size manifest and private-data exclusions. Record exact OS/runtime,
   commit, artifact checksum and results without private screenshots or values.

Go only after these checks pass. Remaining estimated work: one native Windows
build/interaction pass (roughly half a day), a clean-machine pass (roughly half a
day), and integration with the concurrent launcher/UI patch (roughly half a day).
These are planning estimates, not measured effort. If native containment,
renderer event hooks, frozen pipe handling or packaging need redesign, defer the
feature beyond v1 rather than expanding this prototype. Linux window packaging
and an installer/runtime bootstrapper are outside this prototype.

## Observed validation

- Full suite with required Chromium browser checks: 882 passed, one native Job
  test deliberately deselected on WSL, in 285.82 seconds. Command used:
  `uv run pytest -q -k 'not native_job_close'` with
  `PORTFOLIO_REQUIRE_BROWSER=1` and an explicitly selected cached Chromium.
- Final focused adapter, launcher, startup, release and privacy suite: 145 passed
  in 10.98 seconds with `PORTFOLIO_REQUIRE_BROWSER=1` and no skips. The native Job test now checks explicit refusal
  on non-Windows hosts instead of skipping, preserving the existing candidate
  workflow's strict no-skips rule. Its Windows cleanup branch subsequently passed
  in the native regression run recorded below.
- Release/privacy regression set: 97 passed at that point; Ruff correctness and
  Git diff checks passed. Source wheel and sdist content/privacy checks passed.
- The real pipe-gated Streamlit child started and exited with code zero after
  parent EOF, with synthetic saved files unchanged. Renderer tests use a fake
  backend; no native webview was opened on Linux.
- The pinned pywebview wheel's Microsoft interop DLLs matched the official SDK
  package bytes; license and notice extraction passed. The SDK archive stayed
  outside the checkout and distributable source.
- The standalone installed-app browser smoke did **not** pass. Two attempts
  timed out in the existing manual ETF breakdown dropdown workflow (first the
  fixed-income option after a rerender; then the synthetic fund selection).
  Main browser regressions in the full suite passed. The shared smoke helper
  and financial/UI implementation were not changed to mask these failures.
- The retry's first preview used `http://127.0.0.1:60535`, checkout
  `/tmp/portfolio-window-v1`, and an independently generated synthetic workspace
  under a temporary `portfolio smoke` directory. The later restarted phase used
  port 8501. Every smoke process belonged to this run; existing previews were not
  restarted. The smoke cleanup stopped its owned server and removed its temporary
  workspace. No preview is being handed over as a running native window.

## Native startup failure and fix

The first native source launch timed out before the server became reachable.
A diagnostic stack probe found NumPy's native module import stalled while the
shutdown watcher blocked in a Windows CRT pipe read. Closing the parent pipe
released the import immediately. The watcher now polls `PeekNamedPipe` for parent
closure without holding a blocking CRT read. The startup gate and owned Job
Object remain intact. Unexpected pipe errors initiate shutdown. Startup errors
also include the underlying reason instead of only a generic failure message.

The user's isolated source test copy received the adapter fix, with its original
source preserved separately. No portfolio files were changed. Validation used
new disposable synthetic workspaces, not the user's portfolio or test workspace.

- Actual host: Windows 10 build 19045.6456, isolated Python 3.12, pywebview 6.2.1
  and the host's WebView2 runtime. This is not Windows 11 acceptance.
- Native adapter/lifecycle suite: **40 passed in 12.16 seconds**, including a
  real pipe-gated Streamlit child and kill-on-close Job Object. The latter checks
  that a live child exits; Windows can return zero for kill-on-close termination.
- Linux adapter/lifecycle suite: **40 passed in 6.40 seconds**. Ruff passed for
  the changed source and tests.
- A fresh native window loaded the offline demo from the patched extracted
  source copy, at `http://127.0.0.1:63986`. Its workspace and state were generated
  under a disposable `render-probe-*` directory. The actual WebView2 DOM reached
  `first-view-rendered`, rendered Overview Plotly charts, and visited Overview,
  Exposure, Positions, Rebalance and Overview again without application exceptions.
- An authenticated focus request was accepted. Closing that window stopped its
  owned server and removed instance discovery; the smoke process exited zero.
  Foreground activation while minimized was not checked. No preview was left running.

Frozen execution, Windows 11 behavior, terminal flashes, native file selection/
downloads, external navigation, scaling and supervisor hard-crash cleanup remain
unverified. No Windows bundle or clean-machine claim is made. The earlier
standalone browser smoke failure also remains unresolved; this native check did
not exercise its ETF dropdown sequence.
