# Storage, privacy and recovery {#storage}

## Locate the workspace {#location}

The persistent default is independent of the launch directory:

| Platform | Default |
| --- | --- |
| Linux | `$XDG_DATA_HOME/portfolio-breakdown/portfolio`, normally `~/.local/share/portfolio-breakdown/portfolio` |
| Windows | `%LOCALAPPDATA%\portfolio-breakdown\portfolio` |

`--data-dir` overrides it. These commands identify or open the chosen directory:

```sh
uv run portfolio-app --show-data-dir
uv run portfolio-app --data-dir /path/to/workspace --show-data-dir
uv run portfolio-app --data-dir /path/to/workspace --open-data-dir
```

The workspace contains `holdings.csv`, optional `classifications.yaml`, strategic
`allocation.yaml`, ETF snapshots under `etfs/`, private overrides, `.cache` and
per-document `.backups`. Keep the whole directory private. App-managed saves
validate data, check file revisions and back up previous files before atomic
replacement. Those backups do not protect against loss of the entire drive.

## Back up and restore {#backup}

Stop the app and external editors before copying. Replace these placeholders
with distinct private directories; backup and restore destinations must be new.

```sh
uv run portfolio-app --data-dir /path/to/workspace --stop
uv run portfolio-app --data-dir /path/to/workspace --backup-to /path/to/new-backup
uv run portfolio-app --data-dir /path/to/new-restored-workspace --restore-from /path/to/new-backup
uv run portfolio-app --data-dir /path/to/new-restored-workspace
```

Open and verify the restored workspace before adopting it. Restore never
overwrites an existing directory. Copy-based operations preserve source files,
classifications, snapshots, caches and hidden backups, omitting transient locks
and temporary files. Symlinks and ETF references outside the workspace must be
resolved first. A changed or invalid source stops the operation.

## Move an older workspace {#migration}

No repository-local data is migrated automatically. Stop the app, then copy an
old workspace into a new destination:

```sh
uv run portfolio-app --data-dir /path/to/new-workspace --migrate-from /path/to/old-workspace
```

The source is retained. Check the copy before removing anything. See
[update instructions](install.md#update) for version rollback and uninstall.

## Privacy and network access {#privacy}

The application server runs on loopback with Streamlit usage telemetry disabled.
Local storage is not encryption. Protect your workspace, backups and exported
reports using your operating system's access controls and backup policy.

Ordinary operation requests public quotes/history, FX, instrument search and
issuer ETF data. Search text and selected instrument identifiers go to providers;
position quantities and purchase costs are not sent as part of those lookups.
Import files and review drafts are processed locally. The explicit offline demo
avoids market requests; normal demo mode uses the network.

Do not commit personal holdings, accounts, costs, targets, classifications,
imports, screenshots, reports, caches, backups or credentials. The entire `data/`
directory and personal `handoff.md` are private. For source contributors, keep the
pre-commit hook enabled, run `uv run portfolio-check-private --staged`, and inspect
the staged diff. An automated scan cannot recognize all personal details.
