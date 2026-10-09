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

**Linux (Ubuntu 22.04/24.04 x64):** install the `.deb` with your package manager
and launch **Portfolio Breakdown Experimental** from the applications menu.
The package retains the name `portfolio-breakdown-experimental`. Check the release
notes for that installer’s acceptance status and desktop limitations.

For browser mode, extract the application `.tar.gz` archive and run
`./portfolio-app`. Keep its whole folder together.

**macOS (experimental):** on Apple Silicon with macOS 14 or later, open the DMG
and drag **Portfolio Breakdown Experimental** into **Applications**, then launch
it there. Python is bundled; installing Python or uv is unnecessary. Intel Macs are
not supported. This installer has ad-hoc integrity signing only, without a
Developer ID signature or notarization. Gatekeeper rejects quarantined copies;
interactive approval and real browser-download installation remain unverified.
Automated packaged-app checks do not establish that first-install experience.
Consult the release notes before downloading; experimental Mac builds may be
omitted from a release if their automated checks fail.

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

For the Linux browser archive, open a terminal in the extracted application folder:

```sh
./portfolio-app --data-dir /path/to/workspace --server.port=8502
./portfolio-app --data-dir /path/to/workspace --stop
```

For the installed Ubuntu `.deb`, use its launcher from any terminal:

```sh
portfolio-breakdown-experimental --data-dir /path/to/workspace
portfolio-breakdown-experimental --data-dir /path/to/workspace --stop
```

On Mac, use the console companion inside the app copied to Applications:

```sh
"/Applications/Portfolio Breakdown Experimental.app/Contents/MacOS/portfolio-cli" --data-dir /path/to/workspace
"/Applications/Portfolio Breakdown Experimental.app/Contents/MacOS/portfolio-cli" --data-dir /path/to/workspace --stop
```

Adjust the quoted app path if installed elsewhere. Both native launchers accept
`--browser` for browser presentation. Mac command paths follow the bundle layout;
personal-Mac installation and quarantine approval remain unverified.

Other advanced examples in this guide use the Windows console companion. Substitute
the launcher for your installation above, using the same options and OS-appropriate
paths. Recovery commands such as `--stop`, `--backup-to` and `--restore-from` do not
open a native window.
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
2. Run the new Windows Setup, install the new Ubuntu `.deb` with
   `sudo apt install ./portfolio-breakdown-experimental_<version>_amd64.deb`,
   or replace the Mac app in Applications with the copy from the new DMG.
   For portable archives, extract into a new application directory.
3. Launch from the same shortcut or with the same custom data directory. Check holdings, targets,
   prices and the reported version before retiring the old application.
4. Recreate shortcuts if its path changed. For rollback after a data-format
   change, restore the old backup into a new directory and use the matching app.

Do not overwrite an older backup. For an installed Windows app, use **Settings →
Apps → Portfolio Breakdown → Uninstall**. For Ubuntu use
`sudo apt remove portfolio-breakdown-experimental`. On Mac, move the stopped
**Portfolio Breakdown Experimental.app** from Applications to Trash. For portable
apps, remove the stopped app's directory and shortcut. These operations leave the
separate portfolio workspace in place. Shared WebView2 is retained.

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
