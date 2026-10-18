# Import a holdings report {#import}

Holdings snapshot import is experimental and requires an **empty portfolio**. It
accepts current holdings reports. For transaction ledgers, use
[Import transactions](#transactions); neither workflow performs ongoing synchronization.
For existing positions use [Update balances](positions.md#balances).

## Export and review {#review}

FinanzManager-compatible column recognition is provisional: no specific tester
export/version has been verified. Look for a current depot/holdings report and
**Bericht exportieren**. Available formats and columns depend on your version.
Use CSV, delimited TXT/TSV or Excel XLS/XLSX. QIF and PDF are unsupported.

1. In **Positions**, choose **Position tools → Import portfolio** (or the empty-list
   **Import portfolio — experimental** button) and upload one or more files, for example separate depot reports.
2. Check worksheet, header row, delimiter, encoding and German/English number
   formatting. Inspect the preview before trusting suggested mappings.
3. Map instrument name and quantity. When both Bank and FinanzManager quantities
   exist, explicitly select the intended source. Exclude headings and totals.
4. Confirm quantities are shares/units and prices are amounts per unit. Convert
   nominal bond amounts and percentage quotes before import; the app does not
   infer that conversion.
5. Optionally map identifiers, account, costs and dated snapshot prices. Review
   warnings and resolve duplicate positions. Names alone never merge instruments.
6. Choose **Import reviewed positions** to save accepted fields. Use allocation
   setup afterwards; targets and classifications are not inferred from the report.

Costs may be average per unit **or** total cost of the remaining holding, with
an acquisition currency. Leave unknown values blank. A WKN is not a quote ticker.
An export price needs its date, currency and unit convention. It remains a manual
snapshot, not an automatically refreshing price. Foreign snapshot prices use
available FX, not historical FX on the export date; missing FX leaves value unknown.

Uploads and drafts remain in session memory through tab navigation. Cancel,
completion or workspace switching clears them. Cancel writes no holdings.
Accepted fields are saved locally with revision checking and private backups.

## Connect a live listing later {#live-listings}

Open **Positions → Connect live prices**, select the imported instrument, search
for a listing, and verify the ISIN and exchange. Existing ISINs must match.
Missing results do not invalidate the saved position. WKN-only or ISIN-only
holdings can remain unpriced until a suitable listing is available.

Linking can retain the dated manual price. Explicitly choose the switch to live
pricing to clear manual prices across all account positions for that instrument.
Quantity, buy-in, identity and allocation assignments stay saved. A connected
ticker is not a guarantee that quotes will be available.

ETF discovery can separately use issuer-confirmed identity to install a
[breakdown](exposure.md#sources); import review itself stays offline.

## Import transactions {#transactions}

Choose **Positions → Import portfolio → Transactions**. This optional workflow
accepts Generic CSV v1 and the explicitly selected Crypto evidence CSV layout.
It works with empty portfolios and existing holdings. It does not synchronize
with a broker or wallet.

1. Upload UTF-8 comma-separated CSV and review the events. For Crypto evidence
   CSV, give the ledger a stable source name and identify which fee denominations
   are assets. Reuse that source name on subsequent imports.
2. **Save evidence only** retains the rows privately without changing balances.
   Incomplete rows remain available under **Saved evidence** after restarting.
3. Map each instrument and custody location explicitly. Symbols and names do not
   automatically merge instruments. **Create missing positions** creates empty
   identity records, not purchases or opening inventory.
4. Choose whether fiat payments cross the portfolio boundary or update an
   existing cash account. A wallet does not automatically acquire a fiat balance.
5. Select the movements to apply. Confirm a zero starting inventory only when
   ownership actually began with the supplied history; otherwise record the
   starting inventory in the position's History view.
6. Compare reconstructed units and remaining cost against the recorded balances,
   enter their actual comparison date, and accept any displayed cash changes.
   **Apply reviewed transactions** makes history authoritative for those balances.
   It does not add trades on top of an already-imported final snapshot.
7. Review **History completeness** separately for its displayed interval. Matching
   final quantities does not prove that sales, income or charges are complete.

Unknown monetary amounts stay unknown. **Apply known movements with missing
monetary evidence** permits confirmed units without pretending their costs or
payments are known. An unknown asset-fee quantity still blocks activation because
it prevents an exact unit balance. Unsupported or ambiguous rows stay unapplied.

Identical source/event IDs with identical facts are ignored on reimport. Changed
facts require explicit correction review; the old evidence remains saved. Group
IDs link partial executions and never remove duplicates. Imported events are
corrected through this workflow so swaps, destinations and fees remain together.

Empty positions prepared before activation can remain if a later save fails or
is cancelled. Current financial balances are unchanged until the journal save
succeeds. Uploads and review drafts clear on cancellation, completion or workspace
switching; saved evidence remains in the private account journal and backups.

### Generic CSV v1

Download the blank template from the transaction importer. Each row is one
economic event. All quantities are positive, with direction defined by event type.
Use decimal points without thousands separators. Blank means unknown; `0` is an
explicit zero. Supported fiat denominations in this version are EUR, USD and GBP.

| Fields | Meaning |
| --- | --- |
| `schema_version`, `source_id`, `event_id` | `1`, a stable ledger namespace, and its unique execution ID |
| `timestamp_utc`, `sequence`, `event_type` | Time with timezone, optional integer tie order 0–999999, and event type |
| `account_id`, `asset_id`, `quantity` | Custody location, asset identifier and movement quantity |
| `received_asset_id`, `received_quantity` | Incoming side of a swap |
| `counter_account_id` | Owned destination of a transfer, stake or unstake |
| `gross_amount`, `currency` | Actual fiat consideration before fees, for buy/sell only |
| `fee_amount`, `fee_currency`, `fee_asset_id` | Additional charge; specify either a fiat currency or an asset |
| `valuation_amount`, `valuation_currency` | Supplied monetary valuation of a swap or reward; no cash posting |
| `group_id` | Optional relationship between distinct executions |

Review one source ledger at a time. Preserve execution times; dependent events
with identical timestamps require distinct sequence values. Accounting days use
UTC. Unsupported fields must be mapped to this contract before uploading.

- **buy / sell:** quantity bought/sold and gross fiat consideration. Fees are
  separate. Outside tracked cash, purchase payments are contributions and sale
  receipts are withdrawals. Linked cash follows the existing account-opening rules and requires recorded
  cent-denominated amounts; sub-cent evidence is retained without silently rounding it.
- **swap:** primary asset/quantity is sent; received fields describe the other
  asset. The supplied valuation is outgoing analytical proceeds and incoming cost.
  Both movements apply together without inventing fiat payments.
- **transfer / stake / unstake:** quantity is what arrives at the owned destination;
  any additional asset fee is removed separately. Original lots and acquisition
  ordering survive. Principal and already-recorded rewards are not new income.
- **reward:** received units with optional supplied monetary value. That value is
  acquisition basis and income, not an external contribution or cash purchase.
- **fee:** primary quantity is the standalone asset charge. `fee_amount=0` means
  there is no additional charge; the primary quantity is removed only once.

The explicit accounting policy, `fifo-carrying-fees-v1`, uses FIFO per custody
position. Fiat purchase fees enter cost; sale fees reduce proceeds. Asset fees
consume oldest units before the main movement and expense their carrying cost,
without invented market proceeds. This is portfolio accounting, not tax policy.

The Crypto evidence CSV adapter accepts `fees` and `valuation_eur`. For swaps only,
it interprets that layout's `currency` and `gross_amount` as the received asset and
units. These overloaded fields do not become fiat consideration. It retains the
original row alongside the normalized evidence.

Movements wholly within the selected portfolio cancel before flow valuation.
Transfers crossing a selected scope require dated market value; swaps use their
supplied valuation. Missing prices or valuations affect the metrics that require
them. Import does not manufacture historical market data or enable a percentage
return merely because units reconcile.
