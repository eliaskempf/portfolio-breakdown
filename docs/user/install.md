# Install and run {#install}

## Packaged application {#packaged}

Before the first official release, packages are test candidates from a successful
**Build candidate** GitHub Actions run. Download the Windows x64 or Linux x64
candidate while signed into GitHub, then extract the application archive inside
the download. A candidate is not an official release. Initial targets are Windows
11 and Ubuntu 22.04/24.04 x64; other platforms are unverified.

Keep the entire application folder together, including `_internal`, outside your
portfolio directory. On Windows launch `portfolio-app.exe`; on Linux launch
`./portfolio-app`. The app opens in your browser. A dedicated desktop window is
not part of this documented build.

For command examples below, use the executable's path in place of
`uv run portfolio-app` when using a packaged application. Python and uv are not
required for the package. `--install-shortcut` creates a Windows Start Menu or
Linux application-menu entry; recreate it after moving the executable.

## From source {#source}

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), obtain the
repository at the intended source commit, and run these commands from its root.
The package requires Python 3.12 or newer; uv manages the environment.

```sh
uv sync --locked
uv run portfolio-app --version
uv run portfolio-app
```

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
explicit occupied port fails. Repeated launches for one workspace reopen the
managed instance. Independent workspaces can use separate ports.

Closing a browser tab leaves the server running. Use the app's stop action or
`--stop` with the same data directory. Source launches run attached to the
terminal; Ctrl+C stops them. Packaged launches normally run in the background.
`--foreground` keeps a packaged launch attached for diagnostics; `--no-browser`
suppresses automatic browser opening. Stop an existing instance before changing
its command-line demo/live launch mode.

## Update or remove {#update}

1. Stop the old app and [back up the workspace](storage.md#backup).
2. Extract the new package into a new application directory, or update your source
   checkout to the chosen version and run `uv sync --locked`.
3. Launch against the same explicit data directory. Check holdings, targets,
   prices and the reported version before retiring the old application.
4. Recreate shortcuts if its path changed. For rollback after a data-format
   change, restore the old backup into a new directory and use the matching app.

Do not overwrite an older backup. To uninstall, stop the app and remove its
application directory and shortcut; preserve the separate workspace. A uv tool
installation can be removed with `uv tool uninstall portfolio-breakdown`.
Migrate and verify old checkout-local data before deleting a checkout.

The app is GPL-3.0-only, without warranty under its license. Bundles include
third-party notices; corresponding source and build material accompany releases.
