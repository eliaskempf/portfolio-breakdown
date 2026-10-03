# Install, launch and recover

## Packaged candidates (no Python or uv required)

Before the first official release, open the successful **Build candidate** run
under GitHub Actions. Download `candidate-windows-x64` or `candidate-linux-x64`
while signed into GitHub. These are test artifacts, not official releases.
Inside that download, extract the platform application archive. Keep the entire
`portfolio-app` directory together; the executable needs its `_internal` folder.
Use a permanent application location outside your portfolio folder.

On Windows, launch `Portfolio Breakdown.exe` for a console-free desktop start.
Keep `portfolio-app.exe` alongside it for command-line operations and diagnostics.
On Linux, launch `./portfolio-app`. The local browser opens automatically and
plays the intro before showing the app. Once running, **Settings → App & workspace** shows
the version, active folder and a stop button. Closing a browser tab leaves the
server running; launching again reopens that instance.

To add a Windows Start Menu or Linux application-menu shortcut, run the executable
once with `--install-shortcut`. You can pin that entry or copy it to your desktop
using your desktop environment. Repeat after moving the application directory.
Use `--foreground` for terminal diagnostics; private desktop-startup logs are
stored in the platform's per-user application state directory.

## Source installation and development

Install uv, clone the GitHub repository (or extract the source release), then:

```sh
uv sync --locked
uv run portfolio-app
uv run portfolio-app --demo
```

For a persistent isolated installation, `uv tool install` accepts a GitHub Git
URL pinned to a release tag or a downloaded wheel. That installation resolves
package dependencies and does not consume the project's uv.lock. Prefer the
locked checkout or frozen bundle when reproducing a tested release environment.

Developer changes remain editable with `uv run`. Use `--data-dir` for a disposable
workspace, or `--demo --offline-demo` for invented offline data. Restart after changes to imported
Python modules; a browser refresh alone can leave stale imports.

```sh
uv run portfolio-app --data-dir /absolute/path/to/workspace
uv run portfolio-app --data-dir /absolute/path/to/workspace --server.port=8502
uv run portfolio-app --data-dir /absolute/path/to/workspace --stop
```

The app binds to 127.0.0.1 and disables Streamlit usage telemetry. Default launches
choose another free port if 8501 is occupied; an explicitly occupied port produces
an error. Separate workspaces can run separately. The same workspace has one
managed instance. An already-running live/demo mode must be stopped before
switching its launch mode (the UI workspace switch remains available).

## Data location and migration

Defaults are independent of the current working directory:

- Linux: `$XDG_DATA_HOME/portfolio-breakdown/portfolio`, normally
  `~/.local/share/portfolio-breakdown/portfolio`.
- Windows: `%LOCALAPPDATA%\portfolio-breakdown\portfolio`.

`--data-dir` overrides the default. `--show-data-dir` prints the selected path.
`--open-data-dir` opens it. No existing repository data is moved automatically.
An old checkout can still be opened explicitly with `--data-dir data/portfolio`.

To migrate, stop the old app and all external editors, then run:

```sh
portfolio-app --migrate-from /absolute/path/to/old/workspace
```

The destination must not already exist. To choose another new destination, add
`--data-dir /absolute/path/to/new/workspace`. The source is retained. The copy
includes classifications, targets, fund snapshots, caches, hidden backups and
other workspace files; transient locks/temporary files are omitted. Symlinks and
ETF holdings references outside the workspace must be resolved first. A changed
source or invalid portfolio stops the copy rather than silently dropping files.

## Backup, restore, upgrade and uninstall

Stop the app before a complete backup:

```sh
portfolio-app --data-dir /path/to/workspace --stop
portfolio-app --data-dir /path/to/workspace --backup-to /path/to/new-backup
portfolio-app --data-dir /path/to/new-restored-workspace --restore-from /path/to/new-backup
```

Restoration creates a new workspace; it never overwrites an existing one. Open
and check the restored workspace before adopting it. Automatic per-document
`.backups` remain useful for individual edits but do not replace complete backups.
Backup directories contain private financial data; keep them outside Git.

For upgrades, stop the old application, back up the workspace, extract the new
bundle to a new application directory, and launch it against the same workspace.
Recreate shortcuts if the executable path changed. Retain the older bundle and
backup until the new version is verified. Versioned data migrations must be
reviewed before downgrading; restoring a backup is the safe rollback path.

To uninstall a bundle, stop it and remove its application directory and shortcut.
For uv tool installations, use `uv tool uninstall portfolio-breakdown`. Portfolio
folders are separate and remain in place. Source checkouts can likewise be removed
without deleting an external workspace. Never remove a checkout containing old
private data before migration and verification.

## First use and limitations

An empty portfolio opens a welcome dialog with **Explore demo** and **Start my
portfolio**. The demo uses invented positions/buy-ins/targets with public quotes,
real instrument history and supported issuer ETF breakdowns. Explicit
`--offline-demo` uses synthetic offline fixtures instead. Demo edits reset on
application restart. Starting your own portfolio opens the empty Positions view;
use Add position or Import portfolio there. Import retains provisional
FinanzManager CSV/Excel support. Nothing is saved until you accept the form or
reviewed import. Existing portfolios skip the welcome dialog.

The header dropdown switches portfolios, **?** opens help, and **Settings** holds
display and workspace controls. The animation respects reduced motion and has a
Skip button; `--skip-intro` disables it for a server. It plays once per browser
session, not on tab changes or portfolio switches. For source previews and replay
controls, see [Startup development](startup-development.md).

Use **Update balances** to replace quantities and optional average buy-ins. In
**Rebalance → Targets**, configure strategic categories and position targets.
Analytical label hierarchies are maintained in `classifications.yaml`; see the
README's data reference. ETF look-through requires a supported/configured snapshot,
and partial coverage remains visible. Missing prices and buy-ins are unknown,
not zero. The app is for local analysis and planning; it does not execute trades.

## Building and testing packages

```sh
uv sync --locked --all-groups
uv run playwright install chromium
uv run python tools/release.py build
uv run python tools/release.py test
```

Build on the target OS. CI builds Linux on Ubuntu 22.04 and also tests the resulting
archive on Ubuntu 24.04. A successful build is not proof of UI correctness: the
package test starts the extracted executable and visits the actual app in Chromium.
Refer to release-plan.md and release-checklist.md for manual clean-machine gates.

## License

Portfolio Breakdown is distributed under GPL-3.0-only, without warranty, under
the terms in LICENSE. Corresponding application source, build scripts and uv.lock
accompany the binary downloads. Bundled dependencies retain their own licenses;
see THIRD_PARTY_NOTICES.txt and dependencies.json in each application bundle.
