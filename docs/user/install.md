# Install and run {#install}

## Packaged application {#packaged}

Download a release from the repository's Releases page. Before the first official
release, download a successful **Build candidate** Actions artifact while signed
into GitHub. Extract that download once to find the installer and portable archive;
candidates are test builds, not official releases.
Verified local candidates may also be provided directly when Actions is
unavailable. Use the installer supplied with that candidate's checksum and test
record; a Windows Setup EXE does not need an additional ZIP wrapper.

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

Optional **experimental** native-window installers are developed separately:
an Ubuntu 22.04/24.04 x64 `.deb` and an Apple Silicon macOS 14+ DMG. They are not
covered by the supported-release acceptance above. Use only an explicitly
identified experimental candidate; earlier artifacts may omit newer app fixes.
The Mac app has ad-hoc integrity signing only, with no Developer ID signature or
notarization planned for v1. Gatekeeper rejects quarantined copies; interactive
approval and normal browser-download behavior remain unverified. These installers
can be deferred independently of the Windows and Linux browser release.

Initial acceptance targets are Windows 11 and Ubuntu 22.04/24.04 x64. An unsigned
candidate may trigger Windows reputation warnings; a successful build alone is
not native acceptance. Verify that the file came from the intended candidate.

## Launch and stop {#launch-stop}

Launch the installed app from its Start Menu entry or shortcut. To stop it,
close the standalone window, or choose **Portfolio settings → App & workspace →
Stop application** in browser mode. Closing a browser tab alone leaves the
server running; relaunch the same shortcut to reopen it.

### Advanced launch options {#commands}

For recovery or a custom workspace, open a terminal in the **application install
folder**, not the portfolio data folder. On Windows, use PowerShell and the
bundled console companion `portfolio-app.exe` (normally under
`%LOCALAPPDATA%\Programs\Portfolio Breakdown`). These commands use the installed
app and do not require Python or uv:

```powershell
.\portfolio-app.exe --data-dir "C:\path\to\workspace" --server.port=8502
.\portfolio-app.exe --data-dir "C:\path\to\workspace" --stop
```

On Linux, open a terminal in the extracted application folder:

```sh
./portfolio-app --data-dir /path/to/workspace --server.port=8502
./portfolio-app --data-dir /path/to/workspace --stop
```

Other advanced examples in this guide use the Windows console companion.
On Linux use `./portfolio-app` with the same options and Linux paths.
For a normal Windows window launch with custom options, use
`& ".\Portfolio Breakdown.exe" --data-dir "C:\path\to\workspace"`.
Portable installs can create a shortcut with `--install-shortcut`; recreate it
after moving the application folder.

Choose your own directory; the example paths are placeholders. The app binds to
`127.0.0.1`. By default it chooses another free port when 8501 is busy; an
explicit occupied port fails. Repeated window launches focus the existing window; browser launches reopen the
managed instance. Independent workspaces can use separate ports.

Use `--stop` with the same data directory if the interface is unavailable.
Packaged browser launches normally run in the background.
`--foreground` keeps a packaged launch attached for diagnostics; `--no-browser`
suppresses automatic browser opening. Stop an existing instance before changing
its command-line demo/live launch mode.

## Update or remove {#update}

1. Stop the old app and [back up the workspace](storage.md#backup).
2. Run the new Windows Setup to update the installed app, extract a portable
   package into a new application directory.
3. Launch from the same shortcut or with the same custom data directory. Check holdings, targets,
   prices and the reported version before retiring the old application.
4. Recreate shortcuts if its path changed. For rollback after a data-format
   change, restore the old backup into a new directory and use the matching app.

Do not overwrite an older backup. For an installed Windows app, use **Settings →
Apps → Portfolio Breakdown → Uninstall**. For portable apps, stop the app and
remove its application directory and shortcut. Neither method removes your
separate portfolio workspace. Shared WebView2 is retained.

The app is GPL-3.0-only, without warranty under its license. Bundles include
third-party notices; corresponding source and build material accompany releases.

## From source {#source}

This is an alternative for contributors or people choosing to run a checkout.
Skip this section if you installed the packaged app.

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

To open the guide locally from a source checkout, build it before launching:

```sh
uv sync --locked --group docs
uv run mkdocs build --strict
uv run portfolio-app
```

Without a local documentation build, source help links to the published development
guide, which may be unavailable before Pages publication. Packaged candidates
include their matching guide and need no documentation build.
If packaged help reports that the bundled guide is unavailable, reinstall the
complete app. The app will not silently open a different version's guide.

Use a locked checkout or frozen candidate to reproduce a tested environment.
A `uv tool install` installation resolves its dependencies separately and does
not consume the checkout's lockfile.

Source launches run attached to the terminal; Ctrl+C stops them. For an offline
synthetic demo, run `uv run portfolio-app --demo --offline-demo`. To choose a
separate workspace, append `--data-dir /path/to/synthetic-workspace`.

To update a source installation, back up and stop the app, update to the chosen
commit and run `uv sync --locked`. A uv tool installation can be removed with
`uv tool uninstall portfolio-breakdown`. Migrate and verify checkout-local data
before deleting a checkout. See [Source development](development.md) for tests
and preview instructions.
