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
Set `mac_candidate_run` to an existing experimental workflow run ID to repeat
the browser workflows against its verified Mac installer instead of source.
Set `linux_candidate_run` instead to run Linux-only diagnostics: three native
cycles per backend against both the selected installer (under GDB) and current
source, retaining crash backtraces and window-manager state. These runs do not
build new installers. For example:

```sh
gh workflow run desktop-experiment.yml --ref experiment/linux-macos-desktop \
  -f diagnostics_only=true -f linux_candidate_run=37336981212
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

## Current decision

**Experimental only; shipping approval pending.** No platform is approved merely
because its build or health endpoint succeeds. Evaluate platforms independently;
neither blocks the existing v1 release.

Before shipping, review the exact artifact hash and native reports, Linux runtime
baseline, file-upload/save dialogs and downloaded bytes, actual desktop focus
behavior, and macOS Gatekeeper/signing/notarization. Native probe reports carry
explicit gaps; GUI tests that cannot run are failures, not silent skips. GUI
sessions in hosted CI may impose limits that differ from end-user desktops.

Latest completed installer matrix: [run 37336981212 at 8897023](https://github.com/eliaskempf/portfolio-breakdown/actions/runs/37336981212).

| Target | Observed result |
| --- | --- |
| macOS 14, 15 and 26, Apple Silicon | Same DMG passed native rendering, all four tabs, exact upload/download bytes through system file panels, minimize/restore/focus, preserved unsaved input, welcome/early close, process cleanup and packaged browser workflows. Quarantined copies were rejected as expected. |
| Ubuntu 22.04 x64 / X11 | Installed native, browser/lifecycle and reinstall/uninstall checks passed. |
| Ubuntu 24.04 x64 / X11 | Rendering, file dialogs and other workflows passed; hidden-window focus intermittently reported visible but inactive in Qt. |
| Ubuntu 24.04 x64 / Wayland | Actual Wayland desktop interactions, minimize/restore/focus and preserved document/input passed. A subsequent welcome-mode process exited with SIGSEGV. |

That hosted installer matrix failed on the two Linux jobs; all Mac jobs
passed. Subsequent [focused verification at 6b16b96](https://github.com/eliaskempf/portfolio-breakdown/actions/runs/37339481998)
passed Mac native controls, five independent packaged Mac browser workflows,
and the Wayland source workflow. The browser-test change waits for panel motion
to finish and verifies that the nested setup section opened before locating its
selector; captured failures showed the section still collapsed.

Further local investigation reproduced an orphaned Qt popup-helper page and
corrected its ownership and deferred deletion. The new native lifetime assertion
fails against the previous adapter and passes with the correction. This removes
the profile/page shutdown warning, but does not yet prove the hosted Wayland
SIGSEGV is resolved. The local host uses Ubuntu 20.04 and Qt 6.7; it cannot replace
fresh installer checks with the locked runtime on supported targets.

Fresh hosted diagnostics could not start because hosted CI capacity was
unavailable. Rootless containers using checksum-verified official Ubuntu Base
images provide a local alternative. On Ubuntu 24.04 with the locked Qt runtime,
three source cycles each of desktop, welcome and early-close passed on Weston
Wayland. X11 repeated testing confirmed that the server owns keyboard focus even
when Qt's activity flag remains false. The updated probe therefore verifies
server-side focus and actual key delivery to an invented input after both
restores; all nine source runs then passed. These containers use virtual displays
and the host's WSL Linux kernel; they do not emulate macOS or physical hardware.
A fresh Linux installer was then built from clean commit `c76868c` inside Ubuntu
22.04 (Python 3.12.14, glibc 2.35). Its SHA-256 is
`a7227854789d0a431e073fc452cd30827ed8c1d3c73d376b79042b9fe5c722d7`.
The same `.deb` passed the full installed native, browser and lifecycle checks
on Ubuntu 22.04/X11 and Ubuntu 24.04/X11 and Wayland. Nine additional installed
Wayland runs under GDB passed without a crash or profile/page warning, and
reinstall/removal preserved the synthetic workspace. The full local test run had
1,006 passes and one chart-view browser timeout; that test passed in isolation,
and all six tests in its file passed on rerun. The earlier installer also
passed nine debugger runs, so the original hosted SIGSEGV has not been
conclusively attributed to the corrected lifetime defect.

These local tests use Bubblewrap user namespaces, official Ubuntu Base images,
non-root GUI processes, owned Xvfb/Openbox or Weston desktops, and network-isolated
runtime checks. Only a source-only checkout and synthetic outputs are mounted;
no personal portfolio, host display, credentials or Docker socket is exposed.
Docker or a dev container can supply the same Linux userspace and virtual display.
Neither supplies macOS Cocoa/WKWebView or Gatekeeper on a Linux host. Native test
commands are unchanged inside the container; a bare Xvfb display still does not
replace the required window manager/compositor.

Documentation-only pushes no longer rebuild the experimental installers. Use
focused source or existing-artifact diagnostics while investigating failures,
and reserve the full hosted matrix for candidate acceptance.

Keep macOS Developer ID signing /
notarization, interactive Open Anyway approval, real browser quarantine
propagation, physical displays and other desktop environments explicitly outside
the verified evidence. This is stronger evidence for technical feasibility, not
approval to ship either platform as supported in v1.
