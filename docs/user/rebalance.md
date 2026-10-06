# Rebalance {#planning}

Configure [targets](allocation.md#targets), then open **Rebalance → Plan**.
The default **Portfolio contribution** scope offers **Contribution** and
**Options**, then **Calculate plan**. To use **Rebalancing mode**, select
**Within a category** and choose a **Planning category**. Legacy portfolios
without categories show the mode selector directly. Source instrument/account rows are the tradable units;
Overview/Exposure filters and ETF display groups do not alter this scope.

## Choose scope and mode {#modes}

Within-category planning uses that category's post-contribution value. Portfolio
contributions first allocate budgets across categories and then within them.
Required sibling targets must be complete and total 100%; unrelated category
position targets are unnecessary for a within-category plan. Relevant valuations
must be complete. Protected categories and their descendants cannot be sold.

The following modes apply within a category (or to a legacy whole portfolio):

| Mode | Meaning |
| --- | --- |
| Fewest trades (buys and sells) | Reach target ranges without new money, minimizing changed rows before turnover. |
| Minimum new money (no sells) | Find the smallest fully invested contribution reaching all ranges, then minimize trades. |
| Allocate new money | Allocate a specified budget subject to chosen constraints and distribution. |

For selected buys, **Rebalance selected positions** accounts for current holdings
and minimizes squared gaps to exact targets. **Spread by target weights** divides
new money proportionally without accounting for existing holdings. **Optimize
rebalancing** minimizes deviation outside ranges and may concentrate the budget
in one position. These distributions serve different purposes.

## Purchase rules and tolerances {#constraints}

Portfolio contribution defaults to 500 in the reporting currency, all positions
eligible, **Allow skipping positions**, and a minimum purchase of 25. Category
and position tolerances each start at ±0.5 percentage points. Within-category
**Allocate new money** uses **New money**, and exposes the distribution controls
below when **Limit buys to selected positions** is enabled. Its selected-position
distribution defaults to **Buy every selected position**.

**Buy every selected position** requires its minimum purchase for each row;
insufficient budgets produce a shortfall. **Allow skipping positions** permits a
subset, still respecting minimum buys. Prefer fewer trades can accept a specified
extra RMS target gap compared with the best plan under the trade limit. That
allowance is separate from target tolerance.

Absolute tolerance is in percentage points; relative tolerance is a percentage
of the target. An invented 10% target with ±0.5 pp or ±5% relative tolerance has
an allowed range of 9.5–10.5%. A zero target has zero relative tolerance.

**Only buy existing positions** forbids buys into zero-quantity rows while keeping
their targets. **Exclude empty positions and redistribute targets** instead
redistributes their targets within each category for the calculation. It does
not change saved targets. An empty category retains planned capacity.

Temporary maximum allocations state their denominator: selected category or
whole portfolio, using final value including unallocated contribution. Blank
means no cap. Caps do not rewrite targets or force sales in a buy-only plan.
Conflicts with minimum buys or required purchases produce an explanation.

## Interpret the result {#results}

Suggested trades contains buys/sells; Portfolio impact compares sibling category
weights. Plan details includes hold rows, budgets and trade-count comparisons.
Unallocated money remains in the final denominator. Reserved category budgets can
remain partly uninvested because of purchase constraints. The preview does not
create a saved cash holding.

Deviation outside allowed ranges can be zero even when positions are not exactly
on target. RMS gap measures distance from exact targets. Review both with the
unallocated amount and scope. Infeasible constraints or an unproven optimizer
result produce no plan.

Plans are read-only. They assume fractional quantities and exclude fees, taxes,
spreads and lot-size restrictions. No orders are placed and holdings are not
updated. After executing your own trades, update quantities/costs separately.
