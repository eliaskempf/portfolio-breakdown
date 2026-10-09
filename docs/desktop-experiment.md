# Linux and macOS desktop experiment

This work adds Linux and macOS native windows. macOS is now targeted for v0.1.0
as an experimental download alongside Windows/Linux, subject to same-source
build and automated packaged checks. Official publication still requires explicit
approval.

For installation, recovery and removal, see the [user installation guide](user/install.md).
The app's **? → User guide** opens its matching bundled documentation offline.

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
ID signature or notarization. Distribution must disclose the experimental status
and unverified interactive installation approval.

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
Build candidate supplies the full browser suite and Windows regressions; the
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
job while this test app owns the foreground. In that disposable environment,
the complete synthetic path is pasted once instead of flooding the remote panel
with character events. Local probes remain process-targeted and never use the clipboard;
normal startup never enables this input path. No accessibility or Gatekeeper
policy is changed by these tests.
The Cocoa probe waits for the location sheet to own focus before typing and for
the expected selection before confirming the panel. Launch Services checks
require the child JSON report for the default intro,
welcome screen and server cleanup; `open -W` succeeding alone is not acceptance.
This Finder-style launch does not inject system input or require accessibility
permission. The separate installed-executable desktop check must pass real
native upload/save byte checks and window focus checks in the runner context.
Both checks are mandatory; neither substitutes for the other.

For release regressions, **Build candidate** also supports `diagnostics_only`.
It runs the affected Windows browser suites, four slower-render browser cases,
and three independent native source probes per selected Mac host without
building installers. Mac diagnostics record permission, focus and default-button
state, with a bounded accessibility tree and synthetic panel snapshot on failure.
Every probe must pass; a later success never replaces an earlier failure. Synthetic browser failures retain screenshots, DOM and Playwright
traces. Every check must pass; slower-render cases are additional tests, not
retries that replace failures. Source diagnostics do not accept packaged bytes;
Launch Services acceptance uses the real installed `.app` in the installer job.

```sh
gh workflow run candidate.yml --ref REVIEWED_BRANCH -f diagnostics_only=true -f diagnostics_platforms=all
```

Choose `windows`, `macos` or just `macos14` instead of `all` when the other platform has already
been checked locally. These inputs do not affect normal candidate builds.

Set `mac_candidate_run` to an existing experimental workflow run ID for one
macOS 14 job that verifies and installs its DMG, then runs Launch Services and
full installed native/browser acceptance without rebuilding or repeating tests.
Set `linux_candidate_run` instead to run Linux-only diagnostics: three native
cycles per backend against both the selected installer (under GDB) and current
source, retaining crash backtraces and window-manager state. These runs do not
build new installers. For example:

```sh
gh workflow run desktop-experiment.yml --ref main \
  -f diagnostics_only=true -f linux_candidate_run=SELECTED_RUN_ID
```

The X11 hide/restore probe waits until the window manager removes the hidden
window from its client list before requesting focus. Qt's visibility flag alone
changes before the desktop processes the hide request. The Wayland probe waits
for the equivalent scene-graph change. Qt page-lifetime checks require both the
main document and popup helper to be destroyed before releasing their profile.

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

## Release promotion

Native Ubuntu .deb publication is required alongside the Linux browser archive.
The build stages a schema-2 `candidate-linux-native-x64`; successful installed
checks allow its upload, and Ubuntu 24.04 X11/Wayland jobs test those staged bytes.
`linux_native_candidate_run` selects its desktop workflow run in the publisher.

Mac builds stage `candidate-macos-arm64` after installed checks and Gatekeeper
verification. `macos_candidate_run` plus `experimental_macos` selects the DMG.
The publisher checks each platform's build and compatibility jobs, source SHA,
lock, version, docs and checksums before tag/release mutation. It reuses existing
bytes and retains platform-specific source, notices and manifests. An unrelated
job failure does not invalidate successful checks for another platform, but every
selected platform must pass its own gates; native Linux cannot be omitted.

Follow [the release procedure](release-plan.md) and [acceptance checklist](release-checklist.md).
Linux and Mac installer acceptance is automated only; no manual verification is
required or planned. Mac signing and interactive-installation limitations remain
explicit. Automated checks do not establish personal-machine approval or download
quarantine behavior; these are disclosed limits, not pending manual tasks.
