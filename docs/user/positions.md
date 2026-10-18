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

Total quantity and average buy-in remain sufficient for current portfolio analysis.
Open a position's **Transactions** view to start an empty list with **Add buy**
or **Add sell**. **Other transaction** supports distributions, splits, transfers
and fees. **Save transaction** retains incomplete work without changing the
current holding. You can enter purchases and sales before deciding how much
history you can reconstruct.

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
the current portfolio, and checks linked cash where applicable. **Apply history to portfolio** is the explicit action that accepts the displayed
holding and any linked cash changes; no separate acceptance checkbox is needed. Backfilled shares are not added to the
current holding a second time. Resolve discrepancies by correcting records or
explicitly accepting the reconstructed balance; **Keep portfolio unchanged**
leaves saved transactions available for later review. **Replace balance** starts
a new interval for an already active history. Advanced record, lot and fee
editing is available under **Edit transactions, starting lots and fees**;
earlier records and corrections remain in its **Retained evidence** view.

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

Applying balances does not confirm performance coverage. Review income, fees,
taxes, offsetting trades, transfers and corporate actions in **Transactions → Review
against portfolio**. After applying the selected history, **Confirm complete history**
records this separate declaration for the displayed interval. **History not confirmed** means the
recorded gain is calculable but this completeness check is still outstanding;
**Incomplete gain history** identifies other missing evidence or limited coverage,
with a short explanation in its tooltip. No component count is shown: a calculable
gain contribution does not establish complete history.

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
