# Release and upgrade notes {#releases}

## Version 0.2.0 {#version-020}

Version 0.2.0 extends current-holdings analysis with optional transaction history,
personal performance, monitoring, and manually recorded futures and accounts.
Existing holdings remain usable without adding transactions. The application
continues to run locally; it does not connect to brokerage accounts, execute
orders or calculate taxes.

### Transaction history and balances

[CSV, supported PDF statements and manual transactions](import.md#transactions)
use the same retained evidence and review workflow. Save evidence without
changing balances, or review the reconstructed holdings and explicitly apply
them. Each affected holding must be reviewed before the final combined apply.
Applied history becomes authoritative for that holding's quantity and cost;
reapplying it does not add the trades to the old balance again.
New imports can choose a category independently of instrument type, including
holdings already fully sold. Existing categories and targets remain intact.

Exact reimports preserve existing decisions. Similar legitimate trades remain
available for review, while changed source records require correction review.
Edits and removals retain their original evidence, including changes affecting
multiple assets. An opening snapshot establishes a boundary; it does not invent
earlier transactions or prove an earlier zero balance. Review the stated starting
point before applying an export that begins after you already owned assets.

Closed positions retain their transactions, categories, targets and supported
gains. **Portfolio settings → Show sold positions** reveals them in the relevant
lists. Hiding them does not erase history or redistribute targets. A zero-unit
holding needs no current price to establish its zero current value.

### Personal value, gains and returns

[Performance](performance.md#personal-history) uses dated holdings, external cash flows,
prices and FX where available. The measures answer different questions:

| Measure | Meaning |
| --- | --- |
| Remaining acquisition cost and unrealized gain | Cost still attached to the holding, and current value less that supported cost |
| Realized gain, income and total known gain | Supported historical components; a known subtotal does not imply complete lifetime results |
| Period gain | Ending market value less opening market value and net external contributions |
| Time-weighted return (TWR) | Linked investment returns using supported flow boundaries; ordinary calculations use the documented daily convention |
| Annualized money-weighted return (XIRR) | A uniquely verified annualized rate using dated external cash flows and endpoint values |

Allocation's percentage gain uses full-history TWR, not a silently shortened
chart period. Overview remembers the chosen view per portfolio across refreshes
and restarts. Current value and supported monetary gains can remain available
when a percentage return is unavailable.

Internal movements cancel only within the selected scope. Included-cash purchases,
transfers between included accounts and futures settlements do not create an
extra external contribution. Fees count once. Futures notional, margin and
allocated principal are distinct from account equity.

Missing amounts, FX, prices or unsupported prehistory stay unknown. Explicitly
supported empty inception and complete liquidation are valid; a snapshot alone
does not establish them. TWR requires its flow-boundary evidence. XIRR is withheld
when no unique, numerically reliable result is established, including unsupported
complex schedules; it is not guaranteed for every ledger. See the
[calculation requirements](performance.md#personal-history) for timing, scope and limits.

Historical prices retain source and as-of dates. Documented exchange closures may
use the preceding supported close with provenance; missing scheduled closes are
not silently filled. Reviewed alternative listings and proxies remain disclosed
and do not change the held listing or reconstruct missing trades.
**Quote provenance** preserves or accepts explicitly supplied source, listing,
calendar and timezone details for manual historical corrections. Original
observations remain retained, and downloads cannot overwrite the manual value.

### Futures, accounts and monitoring

[Futures and leverage](futures.md) supports actual positions, account cash and
financing in EUR, USD and GBP, exact-contract settlement evidence and recorded
corrections. Settlements relocate profit or loss into cash; they do not create
it twice. Account bindings preserve ownership and prevent double counting cash
already included in holdings. Hypothetical studies remain separate from actual
transactions, and legacy futures simulations are not automatically adopted.
New account setup distinguishes an existing-balance snapshot from a newly funded
account with explicit zero inception and a dated external contribution. Internal
funding between included accounts uses a transfer instead.
Futures transaction CSV import remains unsupported; enter those events manually.

[Monitoring](monitoring.md) stores rules, observations and alert state locally.
Historical risk estimates and hypothetical funding studies remain distinct from
personal realized performance and do not forecast future returns.

### Upgrade from 0.1.0 {#upgrade-020}

1. Record the active workspace shown in **Portfolio settings → App & workspace**.
   A remembered restore selection can differ from the original `--data-dir`.
   Create and download a [complete workspace backup](storage.md#backup), retain
   the old application, and stop it before installing the update.
2. Follow the [platform update instructions](install.md#update). Keep portable
   application files separate from portfolio data and use the same active
   workspace. No checkout-local data is moved automatically.
3. Verify the application reports version 0.2.0. Check holdings, reporting
   currency, categories, targets, ETF data and any saved history before retiring
   the old installation. Keep the pre-upgrade backup unchanged.
4. Add history only when wanted. Opening a legacy workspace does not convert
   purchase batches or apply an inferred ledger. Review balances before applying
   transactions; existing cost-entry records are not a complete trading history.

Securities-history saves use account journal version 5; this version reads
journal versions 1–5. Other documents have their own schema versions. App-managed
saves retain document backups, and interrupted combined holdings/history
publication has recovery safeguards. These safeguards do not replace a complete
backup or make concurrent external editing safe. Close external editors while
the app saves or captures a backup.

For rollback, stop the new app, restore the **complete pre-upgrade backup into a
new workspace**, and open that copy with the matching older application. A newer
journal may be unreadable by an older app. Do not overwrite your current workspace,
copy only an older journal over newer holdings, or assume uninstalling reverses
data changes. Changes made after the backup remain in the newer workspace and
will not appear in the restored copy. See
[history compatibility](storage.md#transaction-history-compatibility) and the recovery options
for remembered workspace selection.

Backups include history, observations, source evidence and closed positions along
with holdings and settings. For history-managed rows, `holdings.csv` retains the
source snapshot; it is not a standalone export of the latest projected balances.
Keep the entire workspace and its unencrypted backups private.

### Installer limitations

Acceptance applies to each published artifact, not to these notes alone. Ubuntu's
native package retains its experimental package/menu identity. The Apple Silicon
macOS installer remains experimental, with ad-hoc signing, no Developer ID
signature or notarization, and unverified personal-Mac download/approval behavior.
See [installation](install.md#packaged) and the published artifact's acceptance
information. Bundled help identifies the exact app source and is available
offline; it is not replaced by a newer development guide.
