# Your first portfolio {#first-portfolio}

Follow a portfolio through time, look inside its funds, trace transactions,
and preview a contribution. The app tour also walks through Monitoring.
Afterwards, switch to your own workspace, save a holding and make a backup. If you have not installed
the app, start with [Install and run](install.md#install).

## Explore a temporary demo {#demo}

Launch the installed app from your shortcut and choose **Explore demo** in the
welcome dialog. The app offers a guided tour after setup or demo entry. You can also
start the offline tour from **App menu → Help → Take the tour**.

The header shows **Demo portfolio**. Its holdings, transactions, savings
statement and targets are invented. You can edit them;
they last for this server session and reset when the app restarts. They are
separate from **My portfolio** and are not a starting copy of your own holdings.

**Explore demo** uses public quotes, history,
analytics and issuer ETF downloads. Initial quantities are sized once from
available quotes and FX; subsequent refreshes preserve quantities and edits.
The first load shows **Preparing your demo…** while prices arrive, then opens
the overview automatically.
If initialization cannot obtain prices, use **Refresh prices** or choose
**App menu → Help → Take the tour** for the offline example. Missing fund downloads leave that fund whole.

### Take the tour {#tour}

Choose **Take the tour** at the optional welcome prompt, or **App menu → Help → Take the tour**
anytime. **Not now** dismisses the prompt. The app remembers dismissal for this
computer's user account.

The tour uses invented prices, history and ETF weights without market requests.
It opens its own fresh synthetic demo, even if you started from an empty
or personal portfolio. Use **Back**, **Next**, or the chapter shortcuts. Try the
highlighted chart when invited. Risk and a €500 contribution example are
calculated for you. **Finish**, **Skip tour**, or **Escape** returns to your
original workspace and view, including filters, without changing saved holdings
or targets, Monitoring rules, or alert acknowledgments.

The Monitoring chapter first shows a plain MSCI World / 200-session SMA chart.
It then configures an invented example; **Next** saves it only in the temporary
tour workspace. Review its triggered condition, then try **Acknowledge alert**.
The +10% threshold is chosen to demonstrate a trigger on synthetic history; it
is not a suggested investment rule. Tour rules disappear when you leave.
You can then repeat these actions yourself:

### 1. Understand Overview

1. The demo opens on **Overview → Allocation**. Choose **Performance**, then **1M**, **1Y** or **All**. Compare portfolio value with invested capital over 30 months of ownership. Gain includes recorded income and realized gains. Toggle the gain beside the value to see time-weighted return. Annualized return accounts for the timing and size of contributions.
2. Choose **View holdings on a date** and pick a date two years ago. Inspect quantities and cash alongside the historical values. Move the date across a purchase or sale to see the units change. **Back to Performance** restores the chart.
3. Choose **Allocation** and select **Equities** in **Category**, or click its chart segment. The World and Emerging Markets targets are 70% and 30% of Equities. Return to **Portfolio** to see the targets for Equities (60%), Money market (20%), Gold (10%), Crypto (5%) and Cash (5%).
4. In **Analytics**, choose **Calculate risk**. The default compares today's allocation with three years of the MSCI ACWI Net Return index. Check the displayed benchmark and coverage, including any ETF fallback. Offline history is invented.

See [categories](allocation.md#overview), [past portfolio views](performance.md#past-portfolio)
and [risk](analytics.md#risk) for calculation details.

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

Open **Positions** and select the World ETF. In **History**, switch between
**Holdings** for your investment and **Price** for the instrument's market
history. Open **Transactions** to inspect a purchase or the partial sale and
its fee. Correct an applied trade from its transaction row.

Open **Demo savings** to see the opening amount, deposit and paid interest
with a running balance. Deposits add contributed capital and interest adds gain.
The money-market ETF has its own price and holdings history.

Use **Add position** to record a new holding or purchase. **Import** accepts
transactions for an existing portfolio. **Holdings snapshot** initializes an
empty portfolio. See [Positions and buy-ins](positions.md#positions).

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

Open **App menu → Workspace** to confirm the actual folder
before saving. The installed app manages its persistent workspace for you.
Use the same shortcut on subsequent starts;
[Storage](storage.md#location) explains default folders and
remembered restored-workspace selections.

### Choose currency and optional categories

Choose **Start my portfolio**. In **Set up your portfolio**, select **EUR**,
**USD** or **GBP** as the reporting currency; EUR is the default. Add categories
one row at a time with optional targets. Enter adds the row and focuses the next
empty name. Complete targets totaling 100% open **All set?**; review the table
and choose **Continue to first position**. Unfinished targets can instead be
saved with **Save categories & continue** and completed later.

**Skip setup** saves the selected currency without creating categories and opens
Overview after the optional tour prompt. Choose **Add position** on Overview for the first holding, or use **Positions → Import** for files.
If you save categories, the first-position form opens; **Finish later** leaves
the categories saved and keeps the unfinished position in this session. Choose **Resume unsaved edit** in Positions to continue. After saving or finishing that
form, you return to Overview and the optional tour prompt.

### Save a first holding, or import

For a purchase, choose **Positions → Add position → New purchase**. Select the
investment and enter trade date, quantity, unit price, currency and fees.
**Save purchase** saves both the position and its transaction immediately.

To enter a total you already hold, choose **Existing holding**. Search and select
the correct listing, checking its exchange and currency, or choose **Enter manually**.
Enter the quantity you currently hold and its **Holding as of** date. Buy-in cost
and allocation targets are optional. **Save position** writes to My portfolio; check the saved row and its
valuation status. A blank price is unknown, not zero. For an asset without a
quote, provide a dated manual price under **More details**. For a physical holding,
choose **Physical asset**. Gold spot is preselected. Choose the metal you own
and enter its fine weight, or choose **Manual price** for an appraisal. [Physical assets](positions.md#physical-assets)

For a holdings report, keep the portfolio empty and choose **Import →
Holdings snapshot**. Upload, map and review the report before
**Import reviewed positions**. Check number formats, units and dates; saving
cannot be undone by canceling the review afterwards. FinanzManager recognition
is provisional. See [Import](import.md#import) for supported files and later
live-price linking.

Next, check [prices and cost coverage](performance.md#valuation), then assign
positions and complete targets in **Rebalance → Targets**. Choose a parent and use **Edit targets** to save its immediate category and position weights together. Missing buy-ins do not prevent allocation
analysis. Change reporting currency later through **App menu → Preferences → Portfolio
currency → Review currency change**; inspect conversion gaps before applying.
New buy-ins default to the reporting currency; original saved costs keep theirs.

## Make a backup and verify persistence {#first-backup}

1. With **My portfolio** selected, save or cancel open edits. Open **App menu → Workspace →
   Create backup** and wait for archive verification.
2. Choose **Download backup** and save the `.portfolio-backup.zip` file to a
   protected backup location. It contains saved workspace files, including costs,
   currency, targets and ETF snapshots. Unsaved forms are not included. Demo and
   tour workspaces cannot be backed up through this action.
3. Stop the app using **App menu → Workspace → Stop application**
   or close its standalone Windows window. Closing a browser tab alone does not
   stop the server. Relaunch the same shortcut or data path and check your saved
   holding and reporting currency.

Keep backups private: they are unencrypted. [Storage and recovery](storage.md#backup)
explains verified restore into a new folder, review/cancel behavior and switching
workspaces. A backup should exist before you change files outside the app.

Use **App menu → Help → User guide** for version-matched help. **Preferences** holds
Light, Dark and System appearance, portfolio currency and display choices.
**Workspace** holds the data folder, backup, restore and application controls. Hover or keyboard-focus help controls for units and scope.
