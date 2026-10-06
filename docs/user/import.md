# Import a holdings report {#import}

The importer is experimental and requires an **empty portfolio**. It accepts
current holdings reports, not transaction ledgers or ongoing synchronization.
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
