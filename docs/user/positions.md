# Positions and buy-ins {#positions}

## Add or edit a holding {#edit}

In **Positions**, choose **Add position**. Search for an investment and use
**Select investment**, or **Choose listing** when several exchanges are grouped
under a verified ISIN. Verify the exchange and trading currency. **Enter manually**
opens identity fields without requiring search. **Change investment** reopens
search while retaining your quantity, cost and allocation draft. Select an existing
instrument when recording it in another account so its identity is reused.
The name and price ticker appear together in one row. You can edit the name;
a searched ticker is read-only. Use **Change investment** for another listing.
Saved holdings use **Connect live prices** to review listing changes. Explicit
manual entry permits typing a ticker. Optional ISIN metadata is retained under
**More details**; provider omissions do not prevent ticker-based pricing.
Names alone do not identify the same security.

Select a row for details; use its pencil to edit. A rename applies to all account
rows for that instrument. Quantities can be fractional and nonnegative. Keep
account/portfolio distinctions when the same instrument is held more than once.
Enter quantity explicitly; zero keeps a planned position. Decimal points or
commas are accepted without thousands separators. Average and total buy-in are
visible together, followed by category and its target side by side. Account/broker,
manual pricing and other optional fields are under **More details**. Physical
assets using manual valuation keep their current price visible.

Buy-in and targets are optional. A successful save writes to the active private
workspace. A stale form is rejected; reload it before saving again.

For assets without provider prices, supply a manual unit price with its currency,
date and quantity unit. A manual price overrides provider quotes; clearing it
restores provider pricing. Instrument type and underlying exposure are distinct:
an ETF may hold equity, non-equity or an unknown mixture.

## Record the cost of the remaining holding {#buy-ins}

Enter **Average buy-in per unit** or **Total buy-in**, with the acquisition
currency. The other cost updates automatically. The last cost field you edited
stays fixed when quantity changes; the caption identifies it. **Clear buy-in**
removes both amounts. Unchanged saved amounts retain their precision.

Total buy-in is the cost of the quantity still held, including purchase fees;
it is not lifetime deposits. The app divides total cost by quantity to obtain
average cost. Positive total cost needs a positive quantity. Blank cost remains
unknown and does not prevent allocation analysis.

New buy-ins default to the portfolio reporting currency; existing records keep
their original currency. Quote currency and buy-in currency can differ. For a
foreign-currency buy-in, supply a purchase date, exchange rate or converted total
cost. Rates are labelled as reporting-currency units per original currency unit.
Missing information can be saved, with gains excluded and a warning. Latest-FX
approximations require explicit confirmation and remain labelled.

Use a single date only when it applies to the whole cost. For purchases on different
dates, enter separate purchase rows or supply their combined converted cost. After sales, transfers or splits, reconcile the
remaining quantity and cost with your records. The app does not track tax lots,
sales accounting or realized gains.

## Replace balances {#balances}

Use **Positions → Update balances** for several current summaries at once.
Review quantities and optional buy-ins, select average or total cost entry, then
confirm and save the snapshot. Repeating a balance snapshot replaces quantities;
it does not add purchases. Targets and classifications stay saved. Quantity
confirmation dates are distinct from purchase dates and market-price timestamps. Changed quantities or buy-ins replace active cost components and invalidate incompatible conversions; they do not infer sales or splits. Metadata-only edits preserve conversions.

## Calculate a new holding from purchases

In **Add position → More details**, enable **Calculate from purchases**. Enter
one row per purchase with quantity, unit price, optional date and optional fees.
An optional FX rate records reporting-currency units per buy-in currency unit;
otherwise dated purchases use historical conversion when available. Missing
conversions exclude gains, while available current values remain included.
Use one buy-in currency throughout. The quantity and cost fields above become
read-only and show the calculated result, including fees. Missing purchase prices
keep the resulting cost unknown. Invalid rows must be corrected before saving.

Disabling purchase entry restores the manual quantity and cost draft; purchase
rows remain available for this session. Closing and resuming the dialog retains
the draft. Cancel discards it. **Save position** saves the holding, metadata and
purchase records together once. These must be purchases making up the current
holding without intervening sales or splits; sales accounting is not supported.
For additional purchases into an existing position, use **Bulk add purchases**.
An activated transaction history routes purchases to its history editor instead.

## Add a purchase batch {#purchases}

Open **Bulk add purchases** for one instrument/account/portfolio. Enter rows,
paste spreadsheet cells or upload a UTF-8 CSV/TSV. These figures are invented:

```csv
date,shares,price,fees
2026-01-01,2,100,1
2026-02-01,3,120,2
```

This batch adds five shares and costs 563 currency units, averaging 112.60 per
share. Dates are optional ISO dates; quantities must be positive. Blank fees are
zero; blank prices are unknown. Use one selected currency per batch; later batches may use different currencies. The optional `fx_rate` column supplies a conversion rate to the current reporting currency for each row. Mixed-currency totals are displayed in reporting currency; original amounts remain saved. Decimal
points or commas are supported without thousands separators; quote decimal
commas in comma-delimited CSV or use tabs/semicolons.

**Add new purchases** increases shares. **Calculate buy-in for shares already
held** leaves quantity unchanged and requires purchases accounting for all
current shares with no intervening sales or splits. Review the before/after
preview, then save once. Repeated batches require acknowledgment; identical rows
within one batch all count. Unknown opening cost or a missing purchase price
keeps the resulting cost unknown.

After a balance replacement, additional purchase dates must be after the balance
confirmation date. Retained batches are context, not a complete transaction
ledger; do not sum old and recalculated batches to reconstruct the holding.

## Physical gold {#physical-gold}

In **Add position → Physical asset → Gold spot price**, enter fine-gold weight
in troy ounces, grams or kilograms. One troy ounce is 31.1034768 grams. The app
retrieves gold spot in USD per troy ounce, converts the quantity and values it in
the selected portfolio currency. Prices represent fine-gold spot value, without coin/bar premiums or dealing
costs. Gold spot history is not yet supported. Manual pricing remains available;
existing manual holdings only switch when you explicitly choose live spot pricing.

## Calendar layout

Date pickers show Monday through Sunday by default. Choose **Portfolio settings →
Week starts on** to switch between Monday and Sunday. This preference is saved
with the portfolio and applies to its date pickers without changing recorded dates.

## Optional transaction history {#transaction-history}

To find gaps across current positions, open **Positions → History**. It shows
positions needing attention by default; clear that filter to see every position.
The list separates absent or unapplied transactions, completeness confirmation,
and missing market inputs. Select a row to open its transaction review, holdings
history or account journal. Checks use saved evidence and the recorded interval;
matching balances alone cannot prove that no transactions are missing. Ordinary
interior price gaps do not themselves make a position incomplete.

Total quantity and average buy-in remain sufficient for current portfolio analysis.
Open a position's **Transactions** view to start an empty list with **Add buy**
or **Add sell**. **Other transaction** supports distributions, splits, transfers
and fees. **Save transaction** retains incomplete work without changing the
current holding. You can enter purchases and sales before deciding how much
history you can reconstruct.

**Import PDFs** accepts a batch of Consorsbank stock/ETF buy and sale statements
for the selected position and account (up to 100 PDFs / 100 MB per batch,
20 MB per file). Files are read locally; original PDFs are not stored in history
or sent to a service. Ready statements are hidden by default; **Show ready
statements** reveals them. Problems and statements needing review come first,
with newest dates first within each group. Ready, review and excluded counts
always describe the whole batch; hiding a ready statement does not exclude it.
Select a table row to inspect its amounts or change **Include this transaction**. Identical imports and invalid statements cannot be included.
Similar trades start excluded; **Import as a separate trade** confirms only that
statement, not the rest of the batch.
Statement details include the identifier, taxes and settlement amount. If the
position has no stored ISIN/WKN, check the document identifier yourself before
saving; a filename or instrument name alone does not establish a match.
When no identifier can be compared, **I checked the instrument identifiers** is
required before saving. A known ISIN or WKN mismatch cannot be overridden.
Legacy WKN-only statements are supported when the quoted price has an explicit
currency; a currency-free quote still requires manual entry.
For parsing failures, **Statement details → Download masked layout report**
creates a local text report containing fixed financial labels, token types and
reconciliation checks. It identifies which rows were parsed and which amounts
match a settlement discrepancy without exporting those amounts; a match is a
diagnostic clue, not an automatically applied correction.
The report lists blocking parser issues and distinguishes exact settlement or
quoted-price matches from nonzero differences within the rounding tolerance.
Recognized order limits and tax calculation or allowance balances are labelled
as informational and retain page/row references. They do not change execution
prices or become transaction charges. Unexpected amounts on those rows still
require review.
It omits filenames, metadata and original names, identifiers, dates and amounts.
**Statement details** also lists the page and visual row for each extracted field,
including each fee/tax and conflicting candidates. Rows are counted from the top
of each page. Compact source locations and the quoted price remain in saved
transaction notes; raw extracted rows and original PDFs are not retained.
Review the report before sharing it for troubleshooting; it does not upload anything
or establish that the extracted financial values are correct.
The **Document** column beside **File** in Statement details maps report numbers
to local filenames for the current upload batch. Filenames stay out of the masked
report. Reordering or changing the upload batch can change document numbers.
Complete hexadecimal document-reference codes, including margin text, appear as
`<document-reference>` in the report. They are not transaction amounts; other
amounts on the same row still require classification.

Identical files already imported anywhere in this portfolio are blocked. Similar
trades are excluded by default and need explicit confirmation if they are distinct.
Known instrument mismatches, ambiguous amounts, inconsistent totals, scanned or
unsupported statements require manual entry. Corrections/reissues and currency
conversion statements need separate review; this is not universal broker support.
Unexplained transaction amounts, unsupported signs/refunds, repeated financial totals,
extra amounts within a field, and a debit/credit direction contradicting the trade
block saving. Confirming fee completeness cannot override these errors. Amounts
without a currency also block when embedded in a financial field or
accompanied by a charge label, including whole-number charges. Settlement rows
recognize labelled German IBANs (compact or grouped) and legacy `Konto-Nr.`
account numbers; other account layouts require manual entry. Separate
tax-calculation bases are not tax charges; explicitly listed joint-account tax
charges are each retained, even when their amounts agree.
After settlement, unfamiliar single amounts inside a recognized tax-detail section
and separately labelled `ST` quantities are supplementary context. **Check
statement** shows their actual extracted text and page/row locations locally,
beside the trade value, fees, taxes and settlement. They do not supply execution
quantities, fees or taxes. This allows new explanatory labels without
adding a parser rule for each one. Sections end at page boundaries, new trade
headings or footer headings; arbitrary amounts elsewhere remain blocking.
Explicit charges, corrections, malformed known fields and extra amounts remain
subject to validation even inside supplementary sections.

Reconciliation checks gross plus charges against a purchase debit, or gross minus
charges against sale proceeds. It never fills a difference with an invented fee or
tax. Matching totals cannot establish what an unfamiliar row means. Supplementary
rows therefore prevent automatic charge confirmation: review the displayed rows
and confirm that statement, or explicitly keep its charges unchecked.

Listed fees/taxes are retained separately. A complete, unambiguous buy or sale
with explicit debit/credit direction and an exactly reconciled settlement
establishes the statement charges automatically. This includes fee-free savings
plans, purchases with commissions, and sales with listed tax deductions. It
requires fully parsed financial rows and no unresolved supplementary amounts;
matching totals alone are insufficient. A buy is not assumed tax-free merely
because it is a purchase. Separately billed costs need their own expense records.

When a statement needs review, inspect the displayed additional rows. Tax bases,
allowances and explanatory quantities are not additional payments. If all actual
charges are included, choose **Confirm statement charges**. To retain uncertain
history, choose **Keep charges unchecked**, or exclude the transaction. Decisions
apply only to that statement; the save summary identifies any unchecked charges.
**Not listed** means no amount was extracted, not that another tax is expected.
An absent charge on an automatically reconciled or explicitly confirmed statement
is shown as zero. No generic batch confirmation certifies other statements.
Unsupported signs, refunds, fields and settlement discrepancies remain blocking
where required by parser validation; confirmation never bypasses them.

The original PDFs and additional-row text remain transient local review inputs.
Saved history retains parsed transaction amounts and source locations, not raw
rows or PDFs. **Statement details** contains document identifiers and the optional
masked troubleshooting report. Statement amounts require recognized ISO currency
codes; custody references and bank/page identifiers are not amounts. Unsupported
historical currency codes require manual entry.

**Save transactions and review** retains the batch atomically and opens the usual
reconciliation below. It does not add shares to the current holding or activate
history. Trade times order same-day imported records where available; otherwise
upload order is retained and must be checked in the history review. Cancel/back
before saving changes no portfolio data. Concurrent portfolio edits reject a stale
save and preserve the uploaded draft for review.

**Edit transaction** opens a form directly from this list. Choose the transaction,
change its date, quantity, amount or fees, then **Save changes**. The original
record is retained, and a correction affects the holding only after review and
application. The list shows the latest version, with pending edits labelled.
Money is displayed to two decimal places; the editor and calculations retain the
full saved precision. Complex starting lots remain available in the advanced editor.

When ready, choose **Review against portfolio**. By default, the selected records
start with your first purchase and zero shares beforehand. This assumption is
shown in the review; use **Review options → Add starting balance** if you already
owned shares before the earliest recorded transaction. Applying history records
the zero boundary at the end of the day before the earliest transaction or fee;
merely opening the review saves nothing and does not invent a purchase. The
internal zero boundary is not a transaction and is omitted from the transaction
list and the **Included transactions** picker.
**Review options** also lets you change which records are included and optionally
link a cash account.

Alternatively, **Add starting balance** records a dated, known quantity and optional
total acquisition cost when earlier transactions are unavailable. It is optional,
and saving it also leaves the current holding unchanged. A starting balance is
an end-of-day boundary; its acquisition cost is separate from the market value
used to start performance measurement. Earlier transactions are not inferred.

The review compares shares, remaining acquisition cost and average buy-in with
the current portfolio, and checks linked cash where applicable. **Apply and confirm history** accepts the displayed
holding and any linked cash changes, and confirms transaction completeness for the
shown interval. **Apply incomplete history** applies balances without that declaration.
No separate acceptance checkbox is needed. Backfilled shares are not added to the
current holding a second time. Resolve discrepancies by correcting records or
explicitly accepting the reconstructed balance; **Keep portfolio unchanged**
leaves saved transactions available for later review. **Replace balance** starts
a new interval for an already active history. Advanced record, lot and fee
editing is available under **Edit transactions, starting lots and fees**;
earlier records and corrections remain in its **Retained evidence** view.

Double-click a transaction to edit it directly, or use its **Edit** button.
The list opens newest first; click **Date** to reverse the order.

To remove transactions, select rows in the **Transactions** table and choose
**Remove selected**. The header checkbox selects all rows; starting balances are
kept and edited separately. Confirm the selected entries before removal. Applied
transactions also show the resulting holding and linked cash changes; invalid
remaining histories cannot be applied. Unapplied entries leave portfolio balances
unchanged. Original evidence is retained internally, and a removed PDF can be
imported again. Re-uploading a PDF that is still present does not overwrite it.
Removing the last transaction restores the empty entry view without an empty table.

The quantity comparison uses the smaller of one billionth of a unit and one
billionth of the recorded quantity. Comparable costs use a one-cent tolerance.
These tolerances label differences; they never manufacture an adjustment.
Unknown or incompatible converted costs are shown as unknown, not as agreement.
Opening **History** automatically loads available dated prices and FX in the
background, from the beginning of recorded history through today. A reconstructed
purchase/sale history opens this view even before its first prices have loaded;
snapshot-only positions keep the existing clean default. Saved observations are
reused on later visits. Failed refreshes retain them and offer **Retry prices**.
FX currencies come from saved transactions and observed price currency. Manual
observations are preserved, and downloads never create transactions or confirm
completeness. Missing prices, FX or transaction records still need to be supplied.
Personal value uses the shares held on each date and prices in that date's traded
share units; split-normalized asset-chart prices cannot be reused blindly.
Offline synthetic demos do not start automatic market requests.

In **Transactions → Review against portfolio**, check income, fees, taxes,
offsetting trades, transfers and corporate actions before choosing **Apply and
confirm history**. The interval starts at the selected starting boundary and ends
today. A starting snapshot does not confirm transactions before that boundary.
Matching shares and cost alone does not establish completeness. Use **Apply
incomplete history** while still backfilling. Confirmation does not supply missing
prices, FX or amounts, or override return eligibility requirements.

Previously applied histories can be confirmed through the same **Review against
portfolio → Apply and confirm history** action. Applied shares are not added again.
A position's confirmation survives unrelated position
changes; changes to its own evidence or linked transfers require another review.
Confirmation covers the shown dates, not future transactions. Account and
whole-portfolio scope reviews retain their separate membership requirements.

**History not confirmed** means the completeness declaration is outstanding.
**Incomplete gain history** identifies other missing evidence or limited coverage,
with a short explanation in its tooltip. A calculable gain contribution does not
establish complete history.

Transaction entry offers **Unit price**, **Trade value (excluding fees)** and
**Total paid (including fees)** for purchases or **Total received (after fees)**
for sales. Always enter **Fees** separately: cash totals already include those
fees, so they are not added or deducted again. Enter **0** for no fees; blank means
unknown. There is no separate fee-confirmation checkbox for new transactions.

When an earlier saved transaction has unconfirmed fees, **Complete transaction
fees** in the review lets you supply or confirm them. It saves a correction and
retains the original record without changing the holding until you apply history.
Cost and average buy-in remain unknown when required costs or FX are missing;
the review explains the missing input. **Complete exchange rates** accepts a
manual rate for each missing date without using current FX. Applying history is disabled while the
reconstructed cost or affected cash balance is unknown.

Fees can have their own currency and date; **Starting lots, fees and taxes**
supports multiple fees and recorded taxes. The old purchase-batch editor retains
its established blank-fee-means-zero convention.
Purchase fees enter acquisition cost once; sale fees reduce proceeds. Explicit
taxes are separate expenses, not additions to the remaining FIFO acquisition cost.

Sales use FIFO for analytical remaining costs. Enter actual opening lots where
known. A snapshot's aggregate cost is not an invented set of purchase lots:
a partial disposal from unknown opening lots leaves remaining cost unknown.
Supply lot evidence or a **cost** row to reconcile the remaining total; that total
does not establish individual lots for another partial disposal. A split row's
quantity is the ratio of new units to old units. Internal transfers preserve known lot
provenance and require a destination history for the same instrument.
**transfer_in** and **transfer_out** record movements across the portfolio boundary;
supply their dated market value separately from any known acquisition costs.

A linked cash account is maintained once by the account journal. Trade and charge
postings are derived from the selected securities records. Payments on or before
an account's cash opening are already included in that opening. Explicit deferred
claims remain separate until received or paid. Later settlement
creates a claim until **Accounts & futures → Cash & financing → Receive pending
cash** records receipt/payment. Do not also enter the same trade payment as a
manual cash adjustment. Without linked cash, position-level purchases and sale
proceeds cross the selected position's investment boundary.

Activated holdings obtain current quantities and costs from history. Use **Replace
balance** for a summary replacement, and Rebalance for category/target edits.
**Update balances** continues to edit snapshot-only positions. Closed histories
remain saved. External edits to an activated holding's original CSV balance are
rejected rather than silently overriding its history.

Legacy purchase batches remain unchanged. **Copy batch to evidence** makes a
reviewable candidate with a stable reference to its original row. Cost
recalculations may overlap purchases; inspect the selected rows and acknowledge
any genuinely distinct matching transactions before activation.
