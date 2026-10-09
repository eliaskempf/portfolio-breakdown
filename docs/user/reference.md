# Data formats

Use the task guides for normal app operation. This page describes private file
formats and calculation contracts for advanced editing and development. Back up
the workspace and stop the app before editing files externally. All numeric
examples here are invented; instrument identities are public metadata.

## Edit your data

`holdings.csv` has one row per position. Required columns are `id`, `name`, and
`shares`; optional columns are `ticker`, `isin`, `acquisition_price`, `portfolio`,
`account`, `acquisition_currency`, and `target_allocation`. The app-managed
`purchase_history` column stores bulk-entry records and is excluded from filters
and allocations. Other additional text columns become metadata filters and allocation
dimensions. Shares must be finite and nonnegative; zero-share positions are
allowed. Acquisition price is optional and is displayed as entered.

```csv
id,name,ticker,isin,shares,acquisition_price,portfolio,account
example,Example Company,DEMO,,1,10.00,Demo portfolio,Demo account
```

The stable `id` identifies an instrument. You may repeat it across account or
portfolio rows; its name, ticker, and ISIN must be consistent. Holdings remain
separate positions in the table and merge by asset ID for holding allocation.
Different exchange listings need distinct IDs. Blank tickers are allowed; without
a manual price or supported gold-spot source, a nonzero holding stays unvalued.
Ordinary listing prices are looked up using tickers, not ISINs.
Tickers and ISINs are trimmed and normalized to uppercase on loading, including
tickers displayed in holding selectors and allocation charts. Stable IDs remain
unchanged so classification links are preserved.

### Strategic and legacy targets

Current category-based portfolios use version 2 `allocation.yaml` for the category
tree, `bucket_id` for each position's leaf category, and `within_bucket_target`
for its fraction of that category. Category targets are relative to their parent;
global targets multiply the fractions along that path. Configure these through
[Rebalance → Targets](allocation.md#targets).

The following whole-portfolio `target_allocation` rules describe **legacy
portfolios without strategic allocation**. They are not a substitute for
within-category targets in current portfolios.

To display legacy targets, add an optional `target_allocation` column. Enter a fraction
such as `0.15` or a percentage such as `15%`; leave unavailable values blank.
Targets apply to each position as a share of the whole portfolio. The holdings
table shows the column only when at least one selected position has a target,
keeps missing targets blank, and does not renormalize targets when filtering.
Targets are not metadata filters or inferred from current weights. Split a
holding's target across its account rows if you maintain multiple positions.

Allocation tables derive label and hierarchy targets by summing the underlying
position targets, using the same membership splits as current allocations.
**Target (% of portfolio)** and **Current (% of portfolio)** share the
whole-portfolio basis. **Current − target (pp)** is positive above target and
negative below. Filtering narrows the contributing positions without rescaling
their targets. Parent targets include their descendants; do not sum parent and
child rows together.

ETF look-through distributes an ETF target using the snapshot's constituent
weights, including residual Other. Targets from direct positions and ETF constituents combine by
asset identity. The optional SMH display group sums its fund and stock targets.
If any contributing position lacks a target, the target and gap stay
blank and a **Known target subtotal (%)** shows the available portion. An
explicit zero target is complete. Missing portfolio prices leave the whole-
portfolio current comparison and gap blank while preserving known targets.

You can save a position with **zero shares and a nonzero target** to plan a new
investment. Its current value is zero without requiring a quote, and its target
contributes to its labels immediately. Add classification labels for new assets
in the private YAML file; otherwise they appear under Unclassified.

The header’s **Portfolio settings → Hide empty positions** changes only visibility in Positions and Exposure.
The separate Rebalance option **Exclude empty positions and redistribute targets**
removes zero-share rows from the planning calculation. Their combined target is divided equally among
remaining unique asset IDs within each category (or the whole legacy portfolio),
then equally among each asset’s held account rows.
This is an equal percentage-point increment, not a proportional scaling. For
example, removing an invented 20% planned target adds 10 pp to each of two
remaining assets. Saved positions and targets remain unchanged and editable in
**Positions**. Visibility never changes targets. Redistribution
requires complete targets if any targets have been entered; it never treats an
unknown target as zero. With no targets, the option simply hides empty positions.

### Planning contracts

The default strategic **Rebalance → Plan → Portfolio contribution** allocates
a contribution across categories and then positions. It defaults to allowing
skipped positions, with a minimum purchase of 25 in the reporting currency.
Choose **Within a category** to access the following three modes. Legacy
portfolios show these modes directly for the whole portfolio:

- **Fewest trades (buys and sells)** reaches every target range with no new money,
  minimizing changed position/account rows first and total turnover second.
- **Minimum new money (no sells)** finds the smallest fully invested contribution
  that reaches every range, then minimizes trades at that contribution.
- **Allocate new money** fully invests a given amount using at most the chosen
  number of trades. It minimizes the sum of distances outside target ranges,
  then trade count, then distance from exact targets. A comparison table and
  plan selector show whether additional trades offer enough improvement.

In **Allocate new money**, enable **Limit buys to selected positions** and
choose the instrument/account rows in **Positions eligible for buying**. Then
choose **Distribution**:

- **Rebalance selected positions** (default for a selected subset): account for
  current holdings and final portfolio value, prioritizing larger shortfalls.
  Minimize the sum of squared percentage-point gaps to exact targets under your
  purchase constraints. Tolerance ranges affect the reported status, not this
  split. Configure **Selection intent** and **Minimum purchase (reporting currency)**:
  - **Buy every selected position** (default): buy at least the minimum amount
    for every selected row, including overweight and zero-target rows. The
    initial minimum is 25 in the portfolio currency and can be changed. Insufficient budgets show the
    required contribution and shortfall; positions are never silently skipped.
  - **Allow skipping positions**: choose the best allocation, with every
    suggested buy meeting the minimum. **Prefer fewer trades** is off by default.
    Enable it to set **Maximum trades** and compare allocation quality across
    trade counts. **Allowed extra target error (pp)** defaults to 0.1 pp: choose
    the fewest trades whose root mean squared (RMS) target gap is within that
    amount of the best plan under the cap. Zero allows no additional error.
    The suggested plan is selected initially; any comparison plan can be inspected.
- **Limit allocations for this rebalance** (within Rebalance selected positions):
  enter an optional **Maximum allocation (%)** for each selected
  instrument/account row. Blank means no cap; zero prevents further buys.
  The cap uses the selected denominator (portfolio or category), including the
  contribution and any unallocated cash. Saved targets keep guiding the allocation
  and are never
  overwritten by temporary caps. A position already above its cap receives no
  buys; this buy-only calculation does not force a sale. A cap that conflicts
  with Buy every selected position or Minimum purchase produces an explanation.
  Caps reset when the selection changes and are not saved to portfolio files.
  The calculator invests as much as the caps, minimum buys and trade limit allow,
  then minimizes squared target gaps. Remaining money appears as **Unallocated
  cash**, included in the percentage denominator; displayed position percentages
  may total less than 100%. Fewer-trade suggestions preserve the maximum amount
  that can be invested; comparison rows also show each plan's unallocated cash.
- **Spread by target weights**: divide the contribution in proportion to the
  selected eligible targets. For example, invented targets of 20% and 10% receive two
  thirds and one third of the new money. Zero targets receive nothing.
- **Optimize rebalancing**: choose buys that improve the whole portfolio's
  allocation, with a trade limit and trade-count comparison. This can put the
  entire contribution into one position.

RMS target gap is the square root of the mean squared percentage-point gap
across the position rows in the selected planning scope. It measures the
squared-deviation objective
on a readable scale; it is separate from deviation outside tolerance ranges.
Without caps, for a fixed number of buys with a uniform minimum, buying the
largest target shortfalls is optimal. With caps, candidate choices also account
for the remaining capacity of each position. The calculator compares feasible
trade counts, including when all positions are in range.

Only **Spread by target weights** ignores existing holdings when splitting the
contribution. Minimum-purchase and selection-intent controls apply to
**Rebalance selected positions**. Both distributions allocate whole cents of reporting currency
so buys plus any unallocated cash sum to the budget. Target-weight splitting rejects an amount too small
to fund every positive-weight recipient with at least one cent.
**Only buy existing positions** excludes selected rows with zero shares. If this conflicts
with **Buy every selected position**, deselect those rows, allow skipping, or
turn off Only buy existing positions; the calculator explains the conflict.
Unselected positions receive no trades. Final weights and deviation still use
targets in the planning scope, so spreading can leave positions outside their ranges.
Changing the selection or distribution hides stale plans. Changing position
identities clears the selection for you to choose again. These controls apply
only to Allocate new money and never change saved holdings or targets.

Choose an absolute tolerance (default ±0.5 percentage points) or a percentage
relative to each target. For a 10% target, ±0.5 pp and ±5% relative both allow
9.5–10.5%. Relative tolerance leaves a zero target at zero; absolute tolerance
can allow a small holding. Bounds are clipped to 0–100%.

Within-category rebalancing requires position targets totaling 100% of that
category; legacy planning requires whole-portfolio targets totaling 100%. Both
require complete valuations in their scope and use final weights including the
contribution. Portfolio contributions also require complete category targets.
Overview filters,
label selections and ETF display groups do not alter the tradable positions.
**Only buy existing positions** forbids buying zero-share account rows while retaining
their targets; **Exclude empty positions and redistribute targets** instead removes and redistributes
them before calculation. Infeasible restrictions produce an explanation.

Amounts assume fractional shares and exclude fees, taxes, spreads and lot-size
constraints. New money is fully invested except when temporary allocation caps
and purchase restrictions leave an unallocated remainder. This is a calculation
result, not a saved cash account.
Reporting-currency amounts are rounded only for display. Plans never place orders or write
holdings. SciPy’s HiGHS linear/mixed-integer optimizer must report a proven
optimum; a solver limit or failed constraint check produces no trade plan.
Each solve has a ten-second limit; interactive calculations support up to 100
position rows. With zero invested capital, choose a budget in **Allocate new
money** instead of asking for a minimum contribution.

### Classification format

`classifications.yaml` maps asset IDs to any number of named taxonomies:

```yaml
nvda:
  classifications:
    ai:
      - [AI, Compute, GPUs]
    sector:
      - [Technology, Semiconductors]
    geography:
      - [North America, United States]
```

Taxonomy names and tree depths are unrestricted by application logic. Add asset
class, tags, sleeves, or other custom classifications using the same format.
Keep the file present; `{}` is valid when you have no classifications yet.
Missing membership in a selected taxonomy appears as `Unclassified`.

An asset with several distinct paths splits its value equally across them.
Identical repeated paths are deduplicated. Explicit path weights are deferred.
Full paths identify categories, so repeated labels under different parents
stay separate. When an internal category also has its own allocation, an
`Assigned here` leaf preserves that value in the visualization. At a truncated
depth, short paths remain visible instead of disappearing.

Use **Positions** in the app to create and edit positions, or edit the
CSV outside the app and reload the page. Classification trees remain editable
in YAML. File data is read on each Streamlit rerun.


## Portfolio currency storage {#currency-storage}

`portfolio.yaml` stores version 1 settings with `reporting_currency` (EUR, USD or
GBP). An absent file means EUR; opening a legacy workspace does not rewrite it.
The optional app-managed `cost_basis_details` holdings column stores versioned
active cost components, original currencies and target-specific conversions.
It is excluded from classification/filter dimensions and exposure payloads.
Legacy purchase batches remain readable. Their dates are used only if the recorded
history reconciles with the current summary. External balance edits supersede
incompatible purchase evidence instead of inventing disposal accounting.

Calculated reporting amounts use currency-neutral interfaces: `current_value_reporting`,
`cost_basis_reporting`, `unrealized_gain_reporting` and `reporting_currency`.
Original native cost and return fields remain separate. Monetary table keys are
currency-neutral; the interface identifies their active reporting currency.
Include the complete private workspace in backups, including settings and cost
records. These files must never be committed to Git.

For architecture, tests and preview commands, see [Source development](development.md).
