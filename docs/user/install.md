# Install and run {#install}

## Packaged application {#packaged}

Download a release from the repository's Releases page. Before the first official
release, download a successful **Build candidate** Actions artifact while signed
into GitHub. Extract that download once to find the installer and portable archive;
candidates are test builds, not official releases.

**Windows:** run `portfolio-breakdown-<version>-windows-x64-setup.exe`. Setup installs
for your current user, creates a Start Menu entry, and optionally a desktop
shortcut. Python and uv are not required. Keep the proposed application directory;
your portfolio lives separately. Setup offers Microsoft's Evergreen WebView2
runtime if it is missing (internet is needed for that prerequisite). You can
instead deselect WebView2 and use the installed **Portfolio Breakdown (browser)**
shortcut. No automatic application updates are installed.

Open **Portfolio Breakdown** from the Start Menu. Its own window displays the
intro while the app loads. **F11** switches between maximized and borderless views;
borderless mode also has an Exit button. Links to other sites open in your normal
browser. Closing the app window stops its owned server. Launching again focuses
the existing window for that workspace. The portable alternative is to extract
the Windows application ZIP and run `Portfolio Breakdown.exe`; keep `_internal`
and all other files beside it. `--browser` selects the browser fallback.

**Linux:** extract the application `.tar.gz` archive and run `./portfolio-app`.
Keep its whole folder together. Linux continues to use your browser.

Initial acceptance targets are Windows 11 and Ubuntu 22.04/24.04 x64. An unsigned
candidate may trigger Windows reputation warnings; a successful build alone is
not native acceptance. Verify that the file came from the intended candidate.

For commands below, substitute the packaged `portfolio-app.exe` (Windows) or
`portfolio-app` (Linux) for `uv run portfolio-app`. The console companion supports
CLI operations and browser diagnostics. Portable installs can create a shortcut
with `--install-shortcut`; recreate it after moving the application folder.

## From source {#source}

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), obtain the
repository at the intended source commit, and run these commands from its root.
The package requires Python 3.12 or newer; uv manages the environment.

```sh
uv sync --locked
uv run portfolio-app --version
uv run portfolio-app
```

On Windows, `uv run portfolio-desktop` starts the standalone window directly from
source; no executable rebuild is needed while iterating. Restart after Python
changes. `uv run portfolio-app` retains the browser development workflow.

Use a locked checkout or frozen candidate to reproduce a tested environment.
A `uv tool install` installation resolves its dependencies separately and does
not consume the checkout's lockfile.

## Launch and stop {#launch-stop}

```sh
uv run portfolio-app --data-dir /path/to/workspace
uv run portfolio-app --data-dir /path/to/workspace --server.port=8502
uv run portfolio-app --data-dir /path/to/workspace --stop
```

Choose your own directory; the example paths are placeholders. The app binds to
`127.0.0.1`. By default it chooses another free port when 8501 is busy; an
explicit occupied port fails. Repeated window launches focus the existing window; browser launches reopen the
managed instance. Independent workspaces can use separate ports.

Closing a browser tab leaves the server running. Use the app's stop action or
`--stop` with the same data directory. Source launches run attached to the
terminal; Ctrl+C stops them. Packaged browser launches normally run in the background.
`--foreground` keeps a packaged launch attached for diagnostics; `--no-browser`
suppresses automatic browser opening. Stop an existing instance before changing
its command-line demo/live launch mode.

## Update or remove {#update}

1. Stop the old app and [back up the workspace](storage.md#backup).
2. Run the new Windows Setup to update the installed app, extract a portable
   package into a new application directory, or update your source
   checkout to the chosen version and run `uv sync --locked`.
3. Launch against the same explicit data directory. Check holdings, targets,
   prices and the reported version before retiring the old application.
4. Recreate shortcuts if its path changed. For rollback after a data-format
   change, restore the old backup into a new directory and use the matching app.

Do not overwrite an older backup. For an installed Windows app, use **Settings →
Apps → Portfolio Breakdown → Uninstall**. For portable apps, stop the app and
remove its application directory and shortcut. Neither method removes your
separate portfolio workspace. Shared WebView2 is retained. A uv tool
installation can be removed with `uv tool uninstall portfolio-breakdown`.
Migrate and verify old checkout-local data before deleting a checkout.

The app is GPL-3.0-only, without warranty under its license. Bundles include
third-party notices; corresponding source and build material accompany releases.
