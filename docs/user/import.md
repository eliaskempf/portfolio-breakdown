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
accepts Generic CSV v1/v2 and the explicitly selected Crypto evidence CSV layout.
It works with empty portfolios and existing holdings. It does not synchronize
with a broker or wallet.

1. Upload UTF-8 comma-separated CSV and review the events. For Crypto evidence
   CSV, give the ledger a stable source name and identify which fee denominations
   are assets. Reuse that source name on subsequent imports.
2. **Save evidence only** retains the rows privately without changing balances.
   Incomplete rows remain available under **Saved evidence** after restarting.
3. Choose **By asset** or **By location**, then map instruments and reviewed owned
   locations explicitly. Symbols and names do not
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

### Asset history and recorded net changes

**Recorded unit movements** includes an asset total across all ledger locations.
Internal transfer and staking principal cancel; rewards, swaps and fees remain.
Openings and checkpoints are displayed separately. Known undated quantities enter
the totals without acquiring invented dates. These are signed changes, not
absolute holdings from an assumed zero.

Choose **By asset** to map every reviewed owned location to one reporting position
per asset. Earlier exchange purchases remain included. **By location** retains the
custody workflow, including linked cash. Aggregate imports use outside tracked
cash. Resolve any active constituent positions before applying an aggregate;
the app will not count both. A location-specific opening cannot establish global
inventory: record a reviewed aggregate boundary in History or explicitly confirm
zero only when supported. Ordering that matters to activation still needs evidence.

**Compare recorded net changes** displays recorded holdings beside the net totals,
with their scope and unknown observation date. The subtraction is arithmetic only;
it does not establish an opening or successful reconciliation. Different scopes,
missing starting inventory and unresolved movements remain visible for later review.
The normal forward comparison and **Apply reviewed transactions** remain separate.

After saving evidence, **Asset history** maps assets and owned locations for the
History view. Before activation it shows net changes. Once a matching forward
book is activated, Value, Gain, Return and Units use that book's opening and
transactions. Ending holdings never determine earlier quantities. Evidence-only
corrections remain separate from activated events until explicitly applied.
Legacy ending-balance settings are retained but no longer route to backwards
history; review their mapping in this panel to enable the forward view.

A separately evidenced deduction may be linked explicitly to its transfer using
source provenance during normalization. The deduction remains an unresolved
inventory removal when its purpose is unknown. It is counted once; the normalized
principal event has no additional charge, while its raw fee evidence is retained.
This linkage is not inferred from similar amounts, timestamps or group IDs.

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

### Inventory, estimates and balance checkpoints: Generic CSV v2

Version 2 retains the v1 event columns and adds generic inventory evidence. It
does not require a particular asset class or provider. Use `schema_version=2`
and **Generic CSV v2**. Existing v1 files and receipt identities remain valid.
Files may contain events or balance checkpoints; separate files are often easier
to review. Reuse stable source and event IDs for both.

| Additional field | Meaning |
| --- | --- |
| `effective_date` | Required `YYYY-MM-DD` reporting boundary for `opening` and `checkpoint` only |
| `acquisition_cost`, `acquisition_currency` | Optional total acquisition cost for opening inventory, a checkpoint or an inventory receipt |
| `cost_quantity` | Optional positive subset of the units covered by that cost; the remaining units have unknown cost |
| `cost_quality`, `cost_source` | `recorded` or `estimated`, plus a source description; required when supplying acquisition cost |
| `valuation_quality`, `valuation_source` | `recorded` or `estimated`, plus a source description; required when supplying a monetary valuation |
| `flow_classification` | `unresolved` or `external`, required for inventory receipts/removals |
| `note` | Optional context, interpretation and source reference |

The extra event types are:

- **opening:** units already held at an explicit starting boundary. Cost and
  original acquisition date may be unknown. This is not a purchase or deposit.
- **inventory_in / inventory_out:** known positive units received or removed,
  without asserting a purchase, sale, reward or fee. An unresolved origin or
  purpose remains unresolved. An explicitly external movement uses its separate
  supplied monetary valuation for performance flows, not acquisition cost.
- **checkpoint:** an accepted balance at a reporting boundary. It replaces
  quantity and remaining cost, retaining all earlier event evidence. The review
  displays the previously explained units, accepted units and difference. Where
  no opening balance is established, explained units and difference are unknown,
  not calculated from an invented zero.

For opening/checkpoint rows, `timestamp_utc` is the observation timestamp and may
be blank. It must not be later than `effective_date`. The effective date is an
explicit end-of-day reporting boundary, **not an inferred observation or
acquisition date**. Transactions following an opening must be on later days.
An undated source balance applied today establishes a reporting boundary today;
it does not establish today's real balance or backfill earlier history.
Use `quantity=0` for a confirmed empty balance and `fee_amount=0` with no fee
denomination for all balance rows. Other movements still require executed
timestamps; an undated event can be retained as evidence but not activated.

Checkpoints are excluded from the default movement selection. Review their
units, effective dates, sources and costs, then explicitly **Accept checkpoints
and retain unresolved differences**. A difference is not a trade, cash flow,
reward or gain. A checkpoint cannot make an invalid earlier replay valid:
unsupported history may remain saved and unapplied while the checkpoint
establishes the supported reporting boundary.

Import checkpoints into the same mapped positions when they describe those
positions. If a source only reports an aggregate scope, use an explicitly named
aggregate reporting position; do not invent per-account allocations or retain
active constituent balances alongside that aggregate. Future activity must use
the same reviewed scope, or require a separate custody reconciliation.

Supplied estimates are preserved with their source and marked in acquisition
costs and gains. Aggregate estimated costs are apportioned proportionally when
units are transferred or sold; this is an explicit approximation, not recovered
historical lot evidence. If an estimate covers only part of a holding, use
`cost_quantity` rather than presenting a known subtotal as the full cost.
Unknown cost remains unknown through transfers and disposals. Where possible,
the app shows partial unrealized gain for the costed units only. Market value is
not substituted for missing acquisition cost.

**Recorded unit movements** shows signed quantity changes independently of
activation, starting inventory and monetary completeness. It includes known
undated quantities without assigning dates, excludes balance rows, and flags
unknown asset charges. Identical rows are counted once; conflicting identities
and malformed quantities do not supply a supported movement. These totals are
diagnostics, not certified balances or percentage returns.

An inventory addition is not a generic corporate-action implementation. Asset
migrations, uncertain transfer ordering and unclassified deductions may still
need review. Completeness and percentage-return requirements remain separate
from successful holdings reconciliation.
