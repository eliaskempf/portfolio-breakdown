# Your first portfolio {#first-portfolio}

## Explore a temporary demo {#demo}

```sh
uv run portfolio-app --demo
```

The demo's positions, costs and targets are invented. Normal demo mode requests
public quotes, price history, analytics and issuer ETF downloads. Its quantities
are sized once using available initial quotes and FX; refreshing prices does not
reset quantities or edits. If initialization lacks prices, use the status/retry
controls rather than treating missing values as zero. Missing ETF downloads leave
the fund whole.

For an explicitly synthetic example without market-data requests, use:

```sh
uv run portfolio-app --demo --offline-demo
```

The offline demo includes invented prices, history and deliberately partial ETF
snapshots. Large residual **Other** values illustrate incomplete coverage; they
are not claims about actual funds. Both demo modes use temporary files separate
from your persistent portfolio. Edits last for that server session and reset on
restart. The app also offers **Explore demo** when starting from an empty
portfolio.

## Start an empty portfolio {#empty}

Launch with a new `--data-dir`, or use your default persistent workspace. Choose **Start my portfolio** in the welcome dialog. The optional guide lets you
choose EUR, USD or GBP as the reporting currency, add categories and targets one row at a time, then add a first position. Enter
adds a category and focuses the next empty name. When complete targets reach
100%, **All set?** shows a confirmation table. Blank or unfinished targets can be
saved for later; skipping setup saves the selected currency and opens **Positions**. There, add a position or open [Import portfolio — experimental](import.md#import).
Save a manual form or accept a reviewed import to persist holdings. Buy-in costs,
classifications and targets can be added later. Existing portfolios load directly.

A useful first pass is:

1. Add or import current quantities and instrument identities.
2. Check prices, currencies and [valuation status](performance.md#prices).
3. In **Rebalance → Targets**, configure categories, assign positions and review
   [within-category targets](allocation.md#targets).
4. Explore Overview, Exposure, Positions and Rebalance. Overview follows owned
   positions; Exposure can show what is inside funds.

Use the portfolio/workspace selector to return from the demo to persistent data.
Confirm the active workspace before saving. The header dropdown selects **My portfolio** or **Demo portfolio**; **?** opens
the guide and **Settings** holds display and workspace controls. The portfolio
currency defaults to EUR. Change it in **Settings → Portfolio currency** and review any missing historical conversions. New buy-in entries default to the selected reporting currency.
