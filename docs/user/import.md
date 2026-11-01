# Import holdings and transactions {#import}

**Positions → Import** opens transaction import. Transactions can be imported into
an existing or empty portfolio; review their mappings and reconstructed balances
before applying. See [Import transactions](#transactions).

Choose **Holdings snapshot** to initialize an **empty portfolio** from a current
holdings report. For replacement balances in existing positions, use
[Update balances](positions.md#balances). Neither import performs ongoing synchronization.

## Export and review {#review}

FinanzManager-compatible column recognition is provisional: no specific tester
export/version has been verified. Look for a current depot/holdings report and
**Bericht exportieren**. Available formats and columns depend on your version.
Use CSV, delimited TXT/TSV or Excel XLS/XLSX. QIF and PDF are unsupported.

1. In **Positions**, choose **Import → Holdings snapshot** (or the empty-list
   **Import holdings** button) and upload one or more files, for example separate depot reports.
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

### Transaction CSV

Choose **Positions → Import**; **Transactions** is selected by default. Upload a CSV, map its
assets to existing holdings or new positions, then review the transactions and
forward-reconstructed balances. New positions are created only when you apply.
An optional price listing connects normal current and historical market data.
Existing holdings are matched by identifier, name, ISIN or ticker; crypto symbols
also match ticker pairs such as `BTC-EUR`. Ambiguous matches require a choice.
New assets receive exact listing suggestions, preferring the reporting currency.
Crypto listings are checked for that currency in mixed portfolios too; other
quote currencies remain available when no matching pair is found.
Click the price-listing field to open its searchable popup. Suggestions appear
immediately, with the reporting currency first; type a name, ticker or ISIN to
search in the same popup. Each result shows its ticker, currency and exchange.
Choosing a result updates the field and closes the popup. Escape or clicking
outside keeps the previous selection. Suggestions, names and types
remain editable; listing changes are saved together with applied transactions.
The listing label identifies the quote pair; its quote currency is not added to
the holding name. The **Holding** dropdown contains existing holdings and **New
position**. Selecting **New position** splits that cell into the dropdown and an
editable holding name beside it. The holding dropdown selects the destination;
the price-listing popup selects the market-data source.
Edited names survive listing changes and navigation. Automatically suggested types
can be reviewed under **Asset types**. **Show transactions for** filters the review
table by asset, including received exchange legs and asset fees. It does not
exclude transactions from the import.

Before upload, there is no review to cancel. After upload, **Discard review** clears
the file and unsaved choices and returns to the uploader. Changing tabs retains
the draft. Saved transactions are not deleted by discarding their review.

The template has seven columns:

| Column | Meaning |
| --- | --- |
| `reference` | Stable unique reference across your transaction files; retain it when correcting a row. |
| `date` | Transaction date, `YYYY-MM-DD`. |
| `type` | `buy`, `sell`, `income`, `fee`, `exchange`, `transfer_in`, `transfer_out`, or `opening`. |
| `asset` | The asset to map to a holding. |
| `quantity` | Positive units; zero for cash income/fees. |
| `amount` | Trade consideration excluding fees, income value, external transfer value, or starting acquisition cost. Blank means unknown. |
| `currency` | Currency of the amount. |

Optional columns are `fee`, `fee_currency`, `fee_asset`, `received_asset`,
`received_quantity`, `acquisition_cost`, `acquisition_currency`, `order`,
`estimated` and `note`. Exchange rows need the received asset and quantity.
In-kind income adds units; cash income has zero quantity. A fee with positive
quantity is paid in that asset; a cash fee has zero quantity and a monetary amount.
For a transfer in, `amount` is its boundary valuation and `acquisition_cost` is
its carried basis. These values need not match. `opening` is a starting balance,
not a purchase or contribution. Split and tax events are also supported.

Omitting the optional fee column declares no additional fees. If included, blank
fees remain unknown; enter zero when none were paid. `estimated=yes` marks supplied
amounts as estimates. Do not use zero to represent an unknown value. File order is
the initial order for same-day transactions; supply `order` when a different
execution order is known. Dates are not converted into invented execution times.
When dependent transactions on one day come from separate files or incompatible
clocks, **Review same-day order** shows the affected entries before balance
review. Enter distinct order numbers, earlier first, and choose **Use reviewed
order**. Original timestamps remain retained; PDF statement times without a
timezone are never treated as UTC. Already applied history keeps its accepted
order until an explicit review changes it.

Use **Save evidence only** to retain the upload without changing active balances.
Mapped transactions appear in the usual Transactions view. Saved imports for new
positions can be reopened from the import screen. **Review against portfolio**
opens the same reconciliation screen used for manual and PDF transaction history.
**Back to import** retains the mapping and name edits. Reviewing writes nothing;
the apply action saves the reviewed holdings and history together.
For assets without a recorded starting balance, it previews zero holdings before
the first movement and states that assumption beside the apply actions. Applying
confirms that starting point. If the export begins after you already held assets,
choose **Import options → Before the first transaction → Earlier holdings** and supply an opening balance in
the CSV or the holding's History before applying.
The ending reference is only a comparison; it never supplies a balancing opening.

The comparison shows whether units and acquisition costs match current holdings.
The final apply action accepts the displayed reconstruction; a matching balance
does not itself prove completeness because missing movements can cancel out.
Similar transactions are shown once as a group, with all entries retained by
default. Open the group to exclude pending entries; balances recompute from that
selection. The final apply action accepts the selection, without separate pairwise
acknowledgments. Excluded source evidence remains saved. The group shows whether
all entries reconcile to an existing holding; a new holding's zero placeholder is
never presented as an independently recorded ending balance.
Different recorded execution times distinguish otherwise identical transactions.
When either entry has only a date, matching economic details still appear in the
similar-transactions group.

**Apply history** makes the reviewed ledger authoritative for the resulting
holding. For multiple holdings, accept each displayed balance and choose
**Apply reviewed holdings**. There is no separate completeness declaration.
Estimates stay labelled, and missing prices or amounts still limit their dependent
metrics. Application replaces the managed balance; it does not add the same
trades to the existing snapshot again.

After import, use the normal **Transactions** list to edit or remove entries,
regardless of whether they came from CSV, PDF or manual entry. Exchanges, transfers
and fees paid from another asset are reviewed with all affected holdings. Original
evidence remains retained. Reimporting the same references does not duplicate
transactions or undo later edits/removals. Moving rows within an export keeps
their previously accepted order; use an explicit `order` to correct chronology.
Changed source rows show their changed fields and require approval individually,
including a warning when they replace a later edit or removal. Economically similar rows from different sources require a
review rather than automatic deletion.

Prepare wallet/staking movements and personal approximations before import when
one asset-level history is sufficient. Preserve fees and income once, and retain
unknown facts as unknown. The application does not infer economic meaning from
export labels. Older transaction formats, checkpoints and unresolved inventory
records remain readable for compatibility. The separate asset-history diagnostic
is retired; all results use normal history, daily TWR and annualized XIRR.
