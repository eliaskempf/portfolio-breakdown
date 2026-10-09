# Windows desktop development

The Windows release opens a native pywebview/WebView2 window. The system
Evergreen WebView2 runtime is required; Setup offers its bootstrapper when missing.
`--browser` selects the browser fallback. Both presentations share the launcher,
workspace lease, loopback server and financial calculations.

## Source and prototype builds

Use Windows 11 x64 and a new temporary synthetic workspace:

```powershell
uv sync --locked --extra window --all-groups
$env:PORTFOLIO_STATE_DIR = Join-Path $env:TEMP "portfolio-window-synthetic-state"
$workspace = Join-Path $env:TEMP "portfolio-window-synthetic-workspace"
uv run portfolio-window --data-dir $workspace --offline-demo
uv run portfolio-app --data-dir $workspace --stop
```

F11 switches between maximized and borderless modes. Closing the window stops
its owned server; repeated launches focus the existing window. Browser fallback
has the browser lifecycle: closing a tab does not stop the managed server.
Stop a window-owned instance before changing its presentation to browser mode.

The official candidate workflow builds the Windows Setup and portable ZIP.
For an isolated prototype, download the matching WebView2 SDK for license notices:

```powershell
$sdk = Join-Path $env:TEMP "microsoft.web.webview2.1.0.3856.49.nupkg"
Invoke-WebRequest "https://api.nuget.org/v3-flatcontainer/microsoft.web.webview2/1.0.3856.49/microsoft.web.webview2.1.0.3856.49.nupkg" -OutFile $sdk
uv run python tools/window_build.py --webview2-sdk $sdk
```

This recipe refuses non-Windows hosts or occupied output directories and writes
under `dist/window-prototype`. Keep the complete extracted application directory
together. Corresponding source, license notices, dependency/DLL inventories and
checksums accompany the prototype. Building it does not publish a release or
establish acceptance of a different candidate.

## Lifecycle and validation

The native adapter starts the contained server, waits for readiness and runs the
renderer on the main thread. Authenticated repeated-launch control requests focus
the existing window. A browser tab cannot take ownership of a window-owned server.
Server cleanup uses owned processes rather than an arbitrary saved PID or port.
Console-free entrypoints initialize private diagnostics before application imports;
those logs are not package inputs. Retain the console companion for CLI recovery.

```powershell
uv run pytest tests/test_window.py tests/test_window_lifecycle.py tests/test_window_build.py
uv run python tools/window_smoke.py --interactive "path/to/Portfolio Breakdown.exe"
```

Use synthetic data. Check actual chart rendering, file dialogs, downloaded bytes,
F11, minimize/restore, repeat launch, startup failures and complete shutdown. Test
the exact Setup/portable files on Windows 11; a Linux browser pass or healthy
endpoint does not establish Windows native acceptance. Follow the
[release checklist](release-checklist.md) for final installation, upgrade and
removal checks, including preservation of the synthetic workspace.
