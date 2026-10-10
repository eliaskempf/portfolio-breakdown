# Futures and leverage (development)

This feature branch contains experimental futures and leverage functionality.
It is not part of the published v0.1.0 installers. Integrated leverage currently
requires EUR reporting. Ordinary portfolios continue to support EUR, USD and GBP;
USD/GBP leverage calculations are deferred.

## Open an independent sandbox

Stop the source portfolio before making a copy. Choose a new destination:

```sh
uv run portfolio-app --data-dir /path/to/portfolio --fork-futures /path/to/new-sandbox
```

The source is preserved. The copy contains a simulated FMWN position; review its
contract, dated prices and funding before interpreting results. Existing sandboxes
open with `--data-dir`. No broker orders are placed. Settings, quotes and results
remain private workspace data.

Overview remains the initial view. The Futures tab contains **Funding policies**,
**Stress & history** and **Investment outcomes**. The saved position editor is
separate from the hypothetical analysis assumptions.

## Recorded capital and exposure

A funded future is a virtual position, rather than an ordinary holding row:

```text
current P&L = signed contracts × multiplier × (mark − entry)
lifetime gain = closed-contract P&L + current P&L − fees
funded value = reference capital + net additional funding + lifetime gain
signed exposure = signed contracts × multiplier × mark
```

Reference capital stays fixed. Rolls carry forward closed-contract gains; closing
contracts retains funded cash. Additional funding changes wealth, not gain.
Financing assumptions used in analysis are not deducted again from recorded value.

Portfolio net equity includes ordinary assets and funded futures value, less
explicit liabilities. Futures notional never adds to wealth. Collateral is counted
once. Gold and crypto contribute market exposure but not equity exposure. Gross
exposure sums absolute contributions before netting long and short positions.
Missing valuations, incomplete exposure coverage and nonpositive equity suppress
unsupported ratios and plans.

An explicitly linked equity ETF supplies a dated approximation of FMWN's index
composition. Unknown or non-equity weights remain unresolved; known companies are
not renormalized to hide missing coverage. The future follows its companion in
Overview and uses matching chart color with a contrasting hatch. Its position
dialog distinguishes lifetime gains, current-contract details, index-proxy history
and observed daily settlements.

Daily MTM uses cached, observed settlements for the current contract. Missing
observations stay visible. It does not reconstruct historical quantities across
all rolls or a broker cash ledger.

## Optional leverage and planning

Ordinary portfolios retain one allocation workflow. Enable leverage in Rebalance
→ Targets to expose the capital/exposure controls and optional policies. Active
futures, liabilities or fund factors keep the controls enabled; disabling a control
cannot remove an asset or liability from accounting.

Capital targets retain their existing meaning. Exposure targets have an explicit
basis and universe, category composition and overall multiple. Switching the chart
measure does not change the active planning policy. Legacy equity-only policies
remain equity-only; broader market policies require an explicit choice. Categories
use stable IDs, including through renaming.

Category leverage compares selected exposure with selected capital. Unassigned
debt prevents a reliable category leverage estimate. Exposure target amounts and
company-shock contributions retain whole-portfolio net equity as denominator.

Exposure planning requires explicitly permitted instruments and declared funding,
margin and fee assumptions. Funded purchases consume cash; futures changes use
whole contracts and change notional rather than adding notional to wealth. Plans
show remaining gaps or infeasibility. They do not automatically route between an
ETF and its linked future, transfer collateral, borrow or execute trades. The
capital planner continues to operate on recorded holdings, excluding dedicated
futures strategy cash.

Supported fund factors describe approximate current exposure, not a promised
multi-day return multiplier. Risk uses fund price histories without multiplying
embedded leverage twice. Futures risk uses signed notional with an EUR ETF proxy;
collateral and liabilities affect the common equity denominator. Incomplete
integrated history withholds aggregate risk rather than renormalizing known assets.

## Funding policy studies

Funding policies compare finite reserves split between broker cash, bank cash and
transfers in transit. Historical index percentage moves stress today's integer
FMWN position. This is a scenario replay, not a reconstruction of historical fills.

Policies control fixed or notional-relative targets/buffers, monthly or quarterly
resets, early top-up triggers and transfer delays of 1–21 observations. At each
close the maintenance check precedes transfer receipt. Liquidation may therefore
occur before cash arrives; same-close re-entry can affect the next interval.
Endpoint positions close without re-entry. Transfers conserve total wealth and
are not income. Losses can exceed the reserve; they are not clipped to zero.

Comparisons use the same historical window and reserve for the baseline and each
candidate. Attribution separates market P&L, carry, bank interest, broker interest
and costs. Infeasible pairs remain visible. Volatility warm-up and cohort exclusions
are reported separately. Overlapping windows are not independent probabilities,
and different feasible cohorts can limit comparisons between candidates.

The event ledger exposes cash, transfers, rolls, exits and re-entry. Rates and
margin rules are scenario assumptions; historical moves use a translated calendar.
There is no intraday reconstruction, bank holiday calendar, debit interest or tax
model. Study results stay in the session; settings persist in the workspace.

## Investment outcomes and shared assumptions

Single-contract, quarterly rolling and historical comparisons retain common
financial settings. Financing supports ECB €STR plus an editable 0.50 percentage
point spread, or a manual total rate. Forward simulations hold their starting rate
constant; dated historical-rate reconstruction remains distinct from funding
replay's constant-rate assumptions.

Cash, futures and a fully funded index comparison use the same starting capital.
The index alternative remains invested after futures exits and stays long.
Existing Monte Carlo analysis does not inherit the funding study's top-ups or
re-entry rules. Price histories, proxy limitations and source dates remain labelled.

## Currency and storage boundaries

Reporting currency cannot change away from EUR while leverage records require
it. If an existing non-EUR workspace already contains EUR leverage records, those
records remain intact and visible, while integrated totals, exposure, risk and
plans are withheld. Switch reporting to EUR to resume those calculations.

Backup/restore preserves the futures marker, comparison/history settings,
leverage settings, exposure targets and funding-study settings with the workspace.
Revision checks reject stale saves. Switching workspaces clears analysis drafts so
hypothetical results from one portfolio cannot appear in another.
