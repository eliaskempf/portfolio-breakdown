# Your first portfolio {#first-portfolio}

This walkthrough follows the app tour: understand an allocation, look inside
funds, inspect positions, then try a contribution plan. Afterwards, switch to
your own workspace, save a holding and make a backup. If you have not installed
the app, start with [Install and run](install.md#install).

## Explore a temporary demo {#demo}

Open the app and choose **Explore demo** in the welcome dialog. From source,
this command opens a repeatable example with invented prices and no market-data
requests:

```sh
uv run portfolio-app --demo --offline-demo
```

The header should say **Demo portfolio**. Its holdings, quantities, buy-ins,
targets and offline ETF weights are deliberately invented. You can edit them;
they last for this server session and reset when the app restarts. They are
separate from **My portfolio** and are not a starting copy of your own holdings.

Without `--offline-demo`, **Explore demo** and `--demo` use public quotes, history,
analytics and issuer ETF downloads. Initial quantities are sized once from
available quotes and FX; subsequent refreshes preserve quantities and edits.
If initialization cannot obtain prices, use **Refresh prices** or restart with
the offline command above. Missing fund downloads leave that fund whole.

### Take the tour {#tour}

Choose **Take the tour** at the optional welcome prompt, or **? → Take the tour**
anytime. **Not now** dismisses the prompt. The app remembers dismissal for this
computer's user account.

The tour opens its own fresh synthetic demo, even if you started from an empty
or personal portfolio. Use **Back**, **Next**, or the chapter shortcuts. Try the
highlighted chart when invited. Risk and a €500 contribution example are
calculated for you. **Finish**, **Skip tour**, or **Escape** returns to your
original workspace and view, including filters, without changing saved holdings
or targets. You can then repeat these actions yourself:

### 1. Understand Overview

1. Open **Overview → Allocation**. The offline demo starts at about €93,184,
   with deliberately uneven current weights against its saved targets.
2. Select **Equities** in **Category**, or click that chart segment. The chart,
   table and value cards now describe that category. Its World and Emerging
   Markets position targets are 70% and 30% **of Equities**. Click the chart
   center or select **Portfolio** to return to the whole portfolio.
3. Choose **Performance**. Compare **Return (%)** with **Gain**. These figures
   use recorded costs of currently held positions and exclude dividends and
   realized gains. Missing costs leave gaps rather than zero returns.
4. Choose **Analytics**, then **Calculate risk**. The default uses three years
   against a global equity ETF benchmark. Read coverage and exclusions beside
   the result. It models today's allocation held constant, not your personal
   historical return. Offline history is invented.

The demo's top-level targets are Equities 60%, Money market 25%, Gold 10% and
Crypto 5%. Live quotes move current weights, so do not expect live mode to match
the offline figures exactly. See [categories](allocation.md#overview),
[performance](performance.md#performance) and [risk](analytics.md#risk) for details.

### 2. Look inside funds in Exposure

1. Open **Exposure → Assets**. **Break down ETFs** starts enabled. Turn it off
   to see whole securities, then on to see supported fund constituents combined
   with matching direct holdings. Your owned quantities do not change.
2. Open an asset to inspect its contributing positions. **Other** retains the
   uncovered fund weight; it is not another position you own directly.
3. Choose **Themes & sectors**. Money market, gold and crypto appear under
   **No business sector**; Unclassified retains genuinely missing sector data.
   The demo opens on Sector because it has no
   curated theme labels. Then choose **Geography** to explore regions and
   countries. Unknown geography stays explicit; overnight-rate exposure appears
   separately as **Money market**.
4. Use **Data & settings → ETF refresh & snapshots** to check source dates and
   coverage. **Individual ETFs** controls which available funds expand.

Offline World and Emerging Markets snapshots contain invented company weights
and small Other remainders of 1–1.5%. Live coverage depends on issuer downloads.
Bond summaries and overnight-rate substitute baskets have separate meanings;
read [Exposure and ETFs](exposure.md#look-through) before interpreting them.

### 3. Inspect positions

Open **Positions** and select a row for details and available price history.
Use its pencil to edit, or **Add position** to try a new holding. A demo edit
changes only the temporary demo. **Position tools** also offers **Bulk add
purchases**, **Update balances**, **Connect live prices** and **Import portfolio**
(experimental). Import requires an empty portfolio, so try that after switching
to your own empty workspace below.

A price chart is instrument history, not your return history. **Update balances**
replaces current quantities; **Bulk add purchases** adds purchases or calculates
the cost of shares already held. These are different actions. See
[Positions and buy-ins](positions.md#positions).

### 4. Try targets and a contribution plan

1. Open **Rebalance → Targets**. Inspect category targets relative to their
   parent and position targets relative to their own category. Complete sibling
   targets must total 100%; blank means unknown.
2. Return to **Plan**. Keep **Portfolio contribution**, enter **500** in
   **Contribution**, then choose **Calculate plan**. Amounts use the portfolio
   currency. The demo already has the targets needed for this exercise.
3. Read **Suggested trades**, **Portfolio impact**, and **Plan details**.
   The plan proposes purchases; it neither places orders nor saves new holdings.
   Purchase restrictions can leave money unallocated.
4. To explore the three rebalancing modes, choose **Within a category**, then a
   **Planning category**. This reveals **Rebalancing mode**. Portfolio contribution
   has its own options and does not offer that mode selector.

For your own portfolio, missing targets or valuations can block a plan.
[Rebalance](rebalance.md#planning) explains tolerances, minimum purchases and caps.

## Start your own portfolio {#empty}

### Switch out of the demo

Finish or skip the tour first. In the header's **Portfolio workspace** dropdown,
select **My portfolio**. Demo holdings are not copied. An empty workspace offers
**Start my portfolio**; an existing workspace loads its saved holdings.

Open **Portfolio settings → App & workspace** to confirm the actual folder
before saving. A packaged app uses its default persistent workspace. For a
separate source workspace, launch with a new path:

```sh
uv run portfolio-app --data-dir /path/to/my-portfolio
```

Replace the placeholder with your chosen folder. Use the same launch path on
subsequent starts; [Storage](storage.md#location) explains default folders and
remembered restored-workspace selections.

### Choose currency and optional categories

Choose **Start my portfolio**. In **Set up your portfolio**, select **EUR**,
**USD** or **GBP** as the reporting currency; EUR is the default. Add categories
one row at a time with optional targets. Enter adds the row and focuses the next
empty name. Complete targets totaling 100% open **All set?**; review the table
and choose **Continue to first position**. Unfinished targets can instead be
saved with **Save categories & continue** and completed later.

**Skip setup** saves the selected currency without creating categories and opens
Overview after the optional tour prompt. Choose **Positions** to add or import.
If you save categories, the first-position form opens; **Finish later** leaves
the categories saved without creating a holding. After saving or finishing that
form, you return to Overview and the optional tour prompt.

### Save a first holding, or import

For manual entry, choose **Positions → Add position**. Search and select the
correct listing, checking its exchange and currency, or choose **Enter manually**.
Enter the quantity you currently hold. Buy-in cost and allocation targets are
optional. **Save position** writes to My portfolio; check the saved row and its
valuation status. A blank price is unknown, not zero. For an asset without a
quote, provide a dated manual price under **More details**. For physical gold,
choose **Physical asset → Gold spot price** and enter fine-gold weight; see
[physical gold](positions.md#physical-gold).

For a holdings report, keep the portfolio empty and choose **Position tools →
Import portfolio** (experimental), or the empty-list **Import portfolio —
experimental** button. Upload, map and review the report before
**Import reviewed positions**. Check number formats, units and dates; saving
cannot be undone by canceling the review afterwards. FinanzManager recognition
is provisional. See [Import](import.md#import) for supported files and later
live-price linking.

Next, check [prices and cost coverage](performance.md#valuation), then assign
positions and complete targets in **Rebalance → Targets**. Category changes and
position targets have separate saves. Missing buy-ins do not prevent allocation
analysis. Change reporting currency later through **Portfolio settings → Portfolio
currency → Review currency change**; inspect conversion gaps before applying.
New buy-ins default to the reporting currency; original saved costs keep theirs.

## Make a backup and verify persistence {#first-backup}

1. With **My portfolio** selected, save or cancel open edits. Open **Portfolio
   settings → Create backup** and wait for archive verification.
2. Choose **Download backup** and save the `.portfolio-backup.zip` file to a
   protected backup location. It contains saved workspace files, including costs,
   currency, targets and ETF snapshots. Unsaved forms are not included. Demo and
   tour workspaces cannot be backed up through this action.
3. Stop the app using **Portfolio settings → App & workspace → Stop application**
   or close its standalone Windows window. Closing a browser tab alone does not
   stop the server. Relaunch the same shortcut or data path and check your saved
   holding and reporting currency.

Keep backups private: they are unencrypted. [Storage and recovery](storage.md#backup)
explains verified restore into a new folder, review/cancel behavior and switching
workspaces. A backup should exist before you change files outside the app.

Use **? → User guide** for version-matched help. **Portfolio settings** holds
portfolio and workspace controls; the top-right app menu offers Light, Dark and
System appearance. Hover or keyboard-focus help controls for units and scope.
