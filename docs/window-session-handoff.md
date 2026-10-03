# Dedicated window feasibility and prototype handoff

## Mode and objective

Start in **Plan mode**. First inspect the existing launcher/packaging and produce
a short technical plan addressing the decisions below. This first phase should
not modify application files. The user can then approve the prototype scope and
switch the session to **Default mode** for implementation and testing.

The objective is a bounded feasibility prototype for opening the existing
Streamlit app in its own desktop window, preferably through pywebview. This is
optional for v1 and must not hold up the mandatory UI/startup work if native
runtime or packaging issues become substantial. No UI rewrite, Electron/Tauri
migration, new database or frontend/backend redesign.

## Checkout and privacy

The integration worktree is `/tmp/portfolio-v1-worktree`, branch `release-v1`.
The confirmed starting commit is:

```text
9f1877d4e2a3222529f979782e0e90c6ee2134dc
```

Verify that worktree's branch/status read-only and inspect the base during Plan
mode. After switching to implementation, create an isolated worktree/branch:

```sh
git -C /tmp/portfolio-v1-worktree branch --show-current
git -C /tmp/portfolio-v1-worktree status --short
git -C /tmp/portfolio-v1-worktree worktree add -b window-prototype-v1 /tmp/portfolio-window-v1 9f1877d4e2a3222529f979782e0e90c6ee2134dc
```

Inspect existing destinations/branches rather than resetting them. Verify your
own branch/status before edits. Do not modify the integration checkout or the
original checkout containing unfinished futures work. Ordinary shared Git
metadata operations are expected; other sessions' working files remain untouched.

Read `AGENTS.md`, `docs/release-plan.md`, `docs/install.md`, README and relevant
tests. Never read personal `handoff.md`, `data/`, real portfolio files, private
caches, exports or credentials. Use only temporary invented workspaces and
isolated application state/log directories. Do not stop another session's app.
Keep `uv` as the environment/dependency workflow. Before any implementation
commit, run `uv run portfolio-check-private --staged`, inspect staged content and
keep the pre-commit hook enabled. No forced staging or history rewriting.

## Inspect first

- `src/portfolio_app/app.py`: source/frozen entrypoint and temporary demo creation.
- `src/portfolio_app/launcher.py`: workspace lease, authenticated instance
  discovery, local server supervision, repeat launch and shutdown.
- `src/portfolio_app/desktop.py`: browser/folder launch, shortcuts, startup errors.
- `src/portfolio_app/settings.py`: paths, version, icons.
- `packaging/portfolio.spec`, `packaging/entrypoint.py`, `tools/release.py` and
  `tools/package_smoke.py`: frozen build, licensing, privacy checks and smoke.
- Launcher/desktop/release tests and candidate workflow.

Current desktop startup spawns a hidden background process and opens the default
browser after server health responds. The main executable is built with
`console=True`, causing a likely initial terminal flash. Browser-tab close does
not stop the server; Stop application does. The proposed window changes these
lifecycle expectations, so inspect the current protections before replacing any.
Health means liveness, not proof that a view rendered successfully.

## Ownership and parallel work

The integration session owns the welcome dialog, sidebar/header, workspace
dropdown, question-mark help, animation integration and production console-free
launcher. The animation session supplies assets separately. The docs session
owns the public guide/site. Do not implement those features here.

Own a small isolated window adapter/prototype, its tests, optional dependency
changes and `docs/window-session-result.md`. Prefer new files and an opt-in
prototype entrypoint. Keep the shipped default launcher/packaging unchanged.
If a launcher hook or separate experimental packaging spec is needed, propose
the smallest interface and isolate its patch for integration review. Do not
independently replace lifecycle management, duplicate the full release system,
or merge conflicting launcher changes.

Use an optional dependency group/extra as appropriate, managed through uv. Report
every native/runtime dependency, packaging hook and lockfile change. The docs
session may also edit dependencies on its own branch; integration reconciles them.

## Plan-mode decisions

Produce a concrete recommendation for:

1. Windows renderer/runtime selection and whether a clean supported Windows
   machine needs an additional install. Check current official pywebview,
   WebView2 and PyInstaller guidance rather than assuming runtime availability.
2. Linux feasibility and native libraries/bundle size. Windows-only experimental
   support is acceptable as a finding; do not silently drop existing Linux or
   browser support to make the prototype pass.
3. Ownership of the webview, server and workspace lease. Opening a window must
   not start a second server for an already-running workspace. Closing the window
   should stop the process it owns; it must not kill a pre-existing browser session
   or another process based on a remembered port/PID. Specify reuse and failure
   behavior explicitly.
4. Repeat-launch focus, graceful shutdown, crash recovery, external links, file
   uploads/downloads and browser fallback. The renderer still uses a loopback
   Streamlit server; this is not a conversion into a serverless native UI.
5. How the window can later host the supplied startup animation and hand over to
   the first rendered view. No duplicate animation implementation now. Distinguish
   a cold-start splash from a page animation, and liveness from rendering readiness.
6. Console-free execution without losing logs, native startup error reporting,
   source development or terminal diagnostics. Coordinate with the integration
   session's console-flash fix rather than owning that production change here.

End the plan with the smallest prototype, acceptance checks, likely integration
files and a go/defer decision boundary. Do not request broad architectural
decisions that the existing pipeline and release scope already settle.

## Implementation phase after scope agreement

Build the opt-in prototype with the existing app unchanged behind it. Keep the
server bound to loopback. Do not expose unnecessary Python-to-JavaScript APIs;
external documentation/provider links should open in the normal browser instead
of navigating the app window to unrelated content. Preserve source development
through `uv run portfolio-app` and the normal browser launch path.

Use injected providers or `--offline-demo` for deterministic tests. A live demo
check may use public provider data in a temporary directory, but tests must not
depend on that network availability. Restart only your own preview after imported
module changes, then navigate the actual window. Do not call the prototype ready
based on a browser-only smoke test or the health endpoint.

## Acceptance and deferral criteria

Verify the following where the platform is actually available:

- Source launch and a frozen Windows prototype from outside the checkout, with
  no Python/uv installed for the frozen acceptance case.
- No unwanted terminal windows during normal launch. Errors remain visible and
  private diagnostics are available when startup fails.
- Existing portfolio and fresh/empty synthetic workspace; demo selection.
- Overview charts, Exposure/ETF toggle, Positions/dialogs, Rebalance/Targets,
  scrolling, keyboard input, resizing and normal Windows display scaling.
- Synthetic CSV and XLSX upload; any supported downloads; external help links.
- Window close, repeat launch/focus, stop/restart, occupied port, startup failure
  and crash recovery. No orphaned owned process or deleted portfolio files.
- Missing renderer/runtime behavior is understandable; specify whether fallback
  is available and how it is selected.
- Dependency/license/privacy inventory and packaging impact. No private paths,
  data, logs or runtime caches enter the distributable.

Do not claim Windows acceptance from WSL or Linux tests. If Windows execution is
unavailable, provide a clearly experimental artifact/build recipe and exact
manual checks for the user's Windows machine. Keep unverified checks explicit.
Do not provision runtimes globally or change system settings to hide portability
problems. If a clean-machine install, lifecycle ownership or packaging requires
substantial new work, recommend deferral with the remaining work quantified.

## Return handoff

Return:

- A go/defer recommendation for v1, with evidence and platform limits.
- Full commit hashes, branch/worktree, exact launch/build commands and dependency
  changes. Separate the adapter from shared launcher/packaging integration patches.
- Observed tests/window checks and unverified Windows/Linux acceptance items.
- Window/server lifecycle contract, browser fallback and animation integration hook.
- Bundle/runtime/license implications and a short remaining-risk list.

Keep the result on the prototype branch. Do not merge or push to main/release-v1,
replace production artifacts, modify shared candidate/promotion workflows, publish
a site, create a tag or publish an official release. Integration and release
acceptance remain with the main session and the user.
