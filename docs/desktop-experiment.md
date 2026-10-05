# Linux and macOS desktop experiment

This isolated experiment extends the committed v1 launcher with dedicated native
windows. It does not change production publishing or approve either platform for
v1. Base: `f2db5ab`; branch: `experiment/linux-macos-desktop`.

## Targets and build

- Ubuntu 22.04/24.04 x64: Qt WebEngine, bundled Python, `.deb`, application menu shortcut.
- Apple Silicon macOS 14+: Cocoa/WKWebView, bundled Python, `.app` inside a DMG.
- Intel Macs, other Linux distributions, automatic updates and additional window
  decoration are outside the experiment. Windows retains its existing adapter.

Use an isolated checkout on the target OS: Python 3.12 on Linux and Python
3.13 on macOS. Python 3.12 does not expose `os.waitid` on macOS, which the
supervisor needs to retain process identity during cleanup. Installed bundles
include the required interpreter. On macOS, set `UV_PYTHON=3.13` for both
commands below; otherwise the repository default selects Python 3.12:

```sh
uv sync --locked --extra window --all-groups
uv run python tools/desktop_build.py
```

The build writes only to `dist/desktop-experiment`, refuses a nonempty output,
and includes corresponding source, dependency notices, checksums and build
metadata. Linux installs under `/opt/portfolio-breakdown-experimental`; its
shortcut runs `portfolio-breakdown-experimental`. macOS users would copy the app
to Applications. This prototype has ad-hoc integrity signing only: no Developer
ID signature or notarization. It is not ready for normal public Mac distribution.

The Linux candidate uses the Qt/Chromium version recorded in the dependency lock
and artifact inventory. An earlier local Qt 6.7 run on an older development host
provided supplemental evidence only; the supported Ubuntu jobs test the current
locked runtime. Do not infer Wayland coverage from an Xvfb/X11 pass.

`portfolio-window --browser` explicitly selects the existing browser launcher.
Data-directory and backup options retain their existing semantics. Updates and
uninstallation do not delete per-user portfolio data. Closing the only window
quits the app and its owned server. The POSIX pipe supervisor survives window
crashes, holds the child's process ID until its process group is terminated, and
never signals a process identified by a saved PID or port.
An explicit repeated launch on Wayland remaps the existing window because
xdg-shell provides no unminimize request. The document and its unsaved input
must survive this operation; the native probe checks both.

## Automated verification

The separate **Experimental desktop candidates** workflow builds and tests Linux
and macOS independently. It uploads artifacts without publishing or promoting
releases. The second Linux job installs the Ubuntu 22.04-built package on 24.04.
Existing PR CI supplies the full browser suite and Windows regressions; the
experiment runs each target's unit suite plus native and installed-package
checks. Synthetic Qt and WebKit snapshots support visual review.

```sh
uv run pytest tests/test_posix_desktop.py tests/test_window.py tests/test_window_lifecycle.py tests/test_launcher.py
uv run python tools/desktop_smoke.py /path/to/installed/portfolio-window --output /tmp/new-synthetic-results
```

On Linux run native checks in a real display session or under `xvfb-run -a`.
`QT_QPA_PLATFORM=xcb` selects the tested X11 backend. The fixed native self-test
can also be run directly:

```sh
portfolio-window --native-self-test /tmp/new-synthetic-results render
portfolio-window --native-self-test /tmp/new-early-close-results early-close
portfolio-window --native-self-test /tmp/new-desktop-results desktop
```

These opt-in checks always allocate their own offline synthetic workspace and
state directory. They refuse existing output directories and additional
workspace options. There is no remote debugging/evaluation endpoint. Fixed
JavaScript checks run inside the actual packaged renderer; ordinary Chromium
browser tests and health endpoints are supporting evidence only.

The extended matrix tests native Cocoa file panels and minimize/restore/focus,
an Openbox X11 session and an isolated Weston Wayland session, plus the same
macOS 14-built DMG on macOS 15 and 26. The `desktop` mode requires a window
manager/compositor; bare Xvfb is insufficient for activation checks.
The Cocoa upload and save checks run independently so a picker automation
failure cannot prevent collecting download evidence. Weston is nested inside
Xvfb to supply a keyboard/pointer seat, which the older headless backend lacks.
The app has no `DISPLAY` and must use the private Wayland socket. Its minimize
check inspects Weston's scene graph because xdg-shell does not report minimized
state and Qt deliberately clears its client-side flag. Weston debugging is
enabled only on this disposable compositor, with a private runtime directory.

The workflow's `diagnostics_only` manual input runs native Mac controls, five
independent synthetic browser workflows, and a Wayland source test without
rebuilding installers. Selector
failures retain synthetic screenshots and page state. Mac file-panel keyboard
input uses the system event stream only in an explicitly opted-in GitHub-hosted
job while this test app owns the foreground. Local probes remain process-targeted;
normal startup never enables this input path. No accessibility or Gatekeeper
policy is changed by these tests.

`tools/desktop_gatekeeper.py APP --output NEW_DIRECTORY` checks a disposable
quarantined copy on macOS without changing system policy. For this ad-hoc-signed
prototype, a valid bundle signature combined with Gatekeeper rejection is the
expected result. It does not verify Safari's quarantine propagation or a user's
interactive Open Anyway approval. All detailed results and any unsupported
desktop behavior remain visible in the synthetic reports.

Checks cover native creation, default chart rendering, navigation and exceptions,
authenticated repeated launch, early close, server cleanup after the window is
killed, browser fallback and preservation of synthetic content. Process tests
also cover gate EOF and stubborn server/descendant cleanup without affecting an
unrelated process. Installer jobs check replacement/removal preserves synthetic
workspace files. Results distinguish native successes from outstanding checks.

## Current decision

**Experimental only; shipping approval pending.** No platform is approved merely
because its build or health endpoint succeeds. Evaluate platforms independently;
neither blocks the existing v1 release.

Before shipping, review the exact artifact hash and native reports, Linux runtime
baseline, file-upload/save dialogs and downloaded bytes, actual desktop focus
behavior, and macOS Gatekeeper/signing/notarization. Native probe reports carry
explicit gaps; GUI tests that cannot run are failures, not silent skips. GUI
sessions in hosted CI may impose limits that differ from end-user desktops.

Current local source evidence: on an Ubuntu 20.04 development host with Xvfb and
Qt WebEngine, the default chart, four tabs, repeat-launch request, blocked file
navigation, native upload/save dialogs with exact bytes, external-link routing,
and close/server cleanup passed. This is supplemental evidence, not
acceptance for Ubuntu 22.04/24.04 or macOS. Hosted and packaged results are recorded
in workflow artifacts and the implementation handoff.
