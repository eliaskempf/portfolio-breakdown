# Storage, privacy and recovery {#storage}

## Locate the workspace {#location}

The persistent default is independent of the launch directory:

| Platform | Default |
| --- | --- |
| Linux | `$XDG_DATA_HOME/portfolio-breakdown/portfolio`, normally `~/.local/share/portfolio-breakdown/portfolio` |
| Windows | `%LOCALAPPDATA%\portfolio-breakdown\portfolio` |
| macOS | `~/Library/Application Support/portfolio-breakdown/portfolio` |

Open **Portfolio settings → App & workspace** to see the active folder and use
**Open data folder** to open it in your file manager. A confirmed restore can
remember a different active folder for the same shortcut (see below).
For a custom launch directory, see [advanced launch options](install.md#commands).

The workspace contains `holdings.csv` (including saved purchase/cost records),
optional `portfolio.yaml` currency settings, `display-preferences.json` calendar
preferences, `classifications.yaml`, strategic
`allocation.yaml`, ETF snapshots under `etfs/`, private overrides, `.cache` and
per-document `.backups`. Keep the whole directory private. App-managed saves
validate data, check file revisions and back up previous files before atomic
replacement. Those backups do not protect against loss of the entire drive.

### Futures and account records (development)

The futures development branch also keeps these private documents in the same
workspace:

| Document | Contents |
| --- | --- |
| `monitoring.json` | Saved rules and revisions, recorded metric observations, alert episodes and read/acknowledgment state |
| `account-journal.json` | Optional securities records, reviewed history selections and dated capital allocations; accounts, exact contract definitions, position identities, dated transactions and corrections, cash/holding bindings, collateral reservations and entered margin requirements |
| `settlement-archive.json` | Durable exact-contract daily settlement observations, retained source revisions and accepted prices |
| `settlement-collection.json` | Last automatic collection attempts and dated source availability |
| `history-observations.json` | Dated prices, FX, manual total/boundary valuations, correction history and scope coverage reviews |
| `account-observations.json` | Dated valuation marks and manual FX observations; these do not post cash |
| `generalized-study-settings.json` | Named hypothetical scenarios, cash reserves, policies and input assumptions |
| `leverage-settings.json` | Existing leverage settings, legacy liabilities and documented fund exposure factors |

Daily settlements are archived separately from the journal and included in
workspace backups. Clearing `.cache` does not remove this archive; closing or
rolling a position retains its history. Archive writes retain observation versions
in the document rather than creating a full per-download backup copy. Other
public history downloads may remain in caches. Eligible daily futures settlements
are applied automatically from the archive using their recorded quantities and
clearing basis; duplicate dates and manual corrections are preserved. Other market
observations do not create transactions. Missing or failed downloads do not justify
reconstructing account balances from estimated prices. Keep the whole workspace,
including caches and study inputs, private.

When existing cash is adopted by an account, its original holding identity is
retained and linked to one native account balance. Account cash replaces that
holding's valuation contribution. Subsequent balance changes belong in
**Futures → Cash & financing**, not a second cash holding or an ordinary holdings
edit. Linking assets may save persistent position keys without changing their
quantities. Security collateral reservations reference owned share/unit quantities;
they do not create additional assets.

Transactions preserve original native-currency amounts. Corrections retain the
original entries and append replacement/reversal facts. A snapshot can start
accounting at a known cash balance and clearing basis without inventing older
transactions. Delayed cash remains a recorded claim until an explicit receipt or
payment. Changing reporting currency changes valuation, not saved native amounts.

Legacy `futures-sandbox.json` and its comparison/history/funding settings remain
separate simulation documents. They are not automatically converted into actual
positions. Adopting a legacy liability records its existing debt and marks the
old record as already included, preserving the original settings while preventing
duplicate debt. Use the app's reviewed saves to preserve these relationships;
copying individual account files between unrelated holdings workspaces can leave
invalid references. See [Futures and leverage](futures.md) for the workflows.

## Back up and restore {#backup}

Open **Portfolio settings → Create backup** while **My portfolio** is selected.
Wait for verification, then choose **Download backup** and save the
`.portfolio-backup.zip` file. Browser mode uses the browser download flow; the
Windows window uses its native Save dialog. Canceling the download leaves your
portfolio unchanged. Unsaved forms are not included.

The versioned archive preserves saved holdings and purchase-cost components/FX,
reporting currency, categories and targets, classifications, ETF holdings and
substitute baskets, overrides, portfolio caches and hidden document backups.
On the futures development branch it also preserves the account journal,
valuation observations, collateral/account bindings, named study settings and
legacy sandbox documents. Restore validates account references against the saved
holdings; it does not promote simulated positions into real transactions.
Runtime locks and temporary files are excluded. The app briefly coordinates
persistent writes while capturing a consistent snapshot; network requests and
compression do not hold that barrier. Close external editors first: they cannot
participate in the app's locks, and a detected external change aborts the backup.
Third-party provider runtime caches are stored separately from portfolio data.

To restore:

1. Open **Portfolio settings → Restore backup** and select the archive.
2. Choose **Review backup**. The app checks format compatibility, paths, file
   sizes, checksums and portfolio data before showing the review.
3. Review the saved currency, position/category/classification/ETF counts and
   destination. You can edit **New workspace folder** to another new folder on
   the computer running the app; its parent must already exist.
4. Choose **Restore and switch**. The app creates and verifies that new workspace,
   then switches in the same window or browser tab. Your old workspace remains
   intact. Unsaved forms are discarded when switching.

**Cancel restore** or dismissing the review creates no destination and does not
change the active portfolio. Existing destinations are always refused. If files
restore successfully but switching fails, the new folder is preserved and
**Retry switch** retries activation. Cancel at that point leaves that verified
folder available for later use.

The same shortcut or launch path, including the same `--data-dir`, remembers the
confirmed selection after restart. **App & workspace** shows the actual active
folder. Selection is local application state; it is not embedded in the portable
archive. Different original launch paths retain independent selections. Previous
workspace leases remain reserved until this app stops, preventing another app
from opening a folder still referenced by an old tab or background task.

For recovery only: to open the original launch folder directly, stop that app,
then use the installed console companion. See [where to run commands and Linux
equivalents](install.md#commands). Replace the example paths with your folders:

```powershell
.\portfolio-app.exe --data-dir "C:\path\to\original" --stop
.\portfolio-app.exe --data-dir "C:\path\to\original" --ignore-workspace-selection
```

This recovery mode ignores the remembered selection without deleting it. It
supports backup and editing; restore-and-switch requires a normal launch.
A missing/invalid remembered workspace reports an error instead of silently
opening a different portfolio. **Restore backup** also remains available when
portfolio currency or holdings files cannot load in an otherwise available folder.
The temporary demo and tour cannot create or restore backups.

Backups are private and unencrypted. Store them on another protected device or
backup service to protect against drive loss. SHA-256 detects damage; it does
not prove who created an archive. Archives are limited to 200 MiB compressed,
1 GiB expanded and 10,000 files. Links, unsafe/non-portable filenames and absolute
ETF file references are refused. Unsupported archive versions require a compatible
app; they are never silently converted. CSV/Excel **Import holdings** remains a
separate workflow under Positions.

### Advanced folder-copy commands

Prefer **Create backup** and **Restore backup** for everyday use. The installed
[console companion](install.md#commands) can also copy complete folders with the app and external editors
stopped. Their arguments are directories, not `.portfolio-backup.zip` archives.
Use new destinations:

```powershell
.\portfolio-app.exe --data-dir "C:\path\to\workspace" --stop
.\portfolio-app.exe --data-dir "C:\path\to\workspace" --backup-to "C:\path\to\new-backup"
.\portfolio-app.exe --data-dir "C:\path\to\new-restored-workspace" --restore-from "C:\path\to\new-backup"
.\portfolio-app.exe --data-dir "C:\path\to\new-restored-workspace"
```

The backup source follows the remembered selection. Add
`--ignore-workspace-selection` to copy the original folder instead. Folder copies
preserve the source and refuse overwrite, symbolic links, external ETF references
and changed or invalid data. Open and verify a copied workspace before adopting it.

## Move an older workspace {#migration}

No repository-local data is migrated automatically. Stop the app, then copy an
old workspace into a new destination using the
[installed console companion](install.md#commands):

```powershell
.\portfolio-app.exe --data-dir "C:\path\to\new-workspace" --migrate-from "C:\path\to\old-workspace"
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

Futures data requests use public product, contract, date, FX and explicitly chosen
proxy identifiers. Account cash, contract quantities and manually entered taxes
are stored locally; no broker connection is required. Market downloads, account
transaction entry and hypothetical studies are separate operations. Futures
transaction CSV import is not supported by the development account journal.

Do not commit personal holdings, accounts, costs, targets, classifications,
imports, screenshots, reports, caches, backups or credentials. The entire `data/`
directory and personal `handoff.md` are private. Contributor checks are described in [Source development](development.md).
An automated scan cannot recognize all personal details.

### Transaction-history compatibility

Opening a legacy workspace does not convert its purchase batches or rewrite the
journal. The first securities-history or dated-allocation save upgrades the
account journal to version 2 and retains a private backup. Existing native futures
events preserve their meaning. Older application versions cannot read version 2;
use a compatible application or restore the complete pre-upgrade backup into a
separate workspace. Do not replace only the journal while retaining newer history.

History-managed CSV rows retain their source snapshot and metadata. Current
financial balances are projected from the journal, so the CSV alone is not a
current-balance export. Back up the entire workspace. Draft evidence, original
records, observations, coverage reviews and closed positions are included. A
failed first activation may leave stable position keys, but never a partially
applied trade or duplicate cash posting.
