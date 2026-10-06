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

## Physical gold

In **Add position → Physical asset → Gold spot price**, enter fine-gold weight
in troy ounces, grams or kilograms. One troy ounce is 31.1034768 grams. The app
retrieves gold spot in USD per troy ounce, converts the quantity and values it in
the selected portfolio currency. Prices represent fine-gold spot value, without coin/bar premiums or dealing
costs. Gold spot history is not yet supported. Manual pricing remains available;
existing manual holdings only switch when you explicitly choose live spot pricing.
