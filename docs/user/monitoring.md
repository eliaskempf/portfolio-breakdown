# Monitoring and alerts {#monitoring}

**Monitoring** explores indicators without adding an asset to your portfolio.
On a fresh visit, Browse opens MSCI World in EUR with a 200-session SMA and an
empty trigger threshold. This **Untracked preview** shows prices and SMA only,
without rule bands or condition status, and does not enable alerts. Existing
saved rules continue monitoring independently. Enter a threshold to preview
its bands and condition; **Track rule** becomes available once the required inputs
and market history are present. Clearing the threshold returns to the plain chart.

Under **Browse**, **Market indices** offers a fixed set of broad benchmarks:
MSCI World, MSCI Emerging Markets and MSCI ACWI, each with published net-return
levels in EUR, USD or GBP. **Portfolio assets** separately lists the unique
tickers of currently held assets, excluding cash, derivatives and zero holdings.
**Provider ticker** accepts an exact Yahoo Finance symbol for other instruments.
Compare completed closes with an SMA and your trigger/approaching boundaries,
then name the rule and choose **Track rule** in the save row above the chart.
**Tracked rules** provides details, editing,
pause/resume and deletion. Position details offer **Track this asset**; account
and collateral views offer **Create account alert**.

## Moving-average rules {#moving-averages}

The 200-session preset uses 200 completed trading observations, not 200 calendar
days. The 10-month preset uses ten consecutive completed month-end closes, not
all daily observations during ten months. Missing months are not filled. A newly
completed close enters the average. Months finalize when the next local calendar
month begins; a provider bar still developing during the trading session does not
count as a completed close.

Rules compare `100 × (price / SMA − 1)` with an inclusive threshold. For example,
a below rule with a −2% trigger fires at or below 98% of its SMA. A one-percentage-
point approaching band begins at −1%. Above rules reverse the comparison.

Choose completed daily close, completed month-end close, or a timestamped
intraday observation. Intraday mode normally uses the last completed SMA.
**Include developing period**, under **Timing & data freshness**, instead combines the
current observation with the preceding N−1 completed periods. This changes the
reference during the period and is explicitly labeled.

Closing-price history is the default for stocks and ETFs. Dividend-adjusted
history is a separate selection, rebased to current quote units for comparison.
Provider split adjustments remain part of the chosen history. An index retains
its named currency and price/net/gross-return convention. Rules retain their
native series currency when the portfolio reporting currency changes.

Direct MSCI references provide daily net-return levels, not intraday
quotes. Choose an alternative instrument explicitly if you want intraday tracking;
the app never silently substitutes an ETF. Regular-session provider quotes may
be delayed. Freshness limits are editable: defaults are 30 minutes for intraday
rules and seven days for close/account rules, entered in minutes, hours or days.
Intraday rules can therefore show
stale outside trading hours. Missing metadata, insufficient history, stale inputs
or failed requests withhold a new signal and any all-clear.

Yahoo can include entirely empty rows in historical downloads. Rows without any
OHLC prices or trading volume are omitted, without filling prices; the SMA uses
available closing observations. Partial bars with a missing close, or missing
adjusted prices, remain data errors. **Data details** shows omitted-row counts,
the source, its available history and retrieval time.

## Historical browsing {#historical-browsing}

**Chart period** offers one, three, five or ten years, all available history,
dotcom and GFC periods, or custom start and end dates. Indicators are calculated
using the full history before the chart is cropped, preserving their warm-up.
Historical-period metrics describe the final close in that period and are
labeled **Historical condition**. Browsing the past neither creates alerts nor
changes a saved rule's live evaluation period; it is not a strategy backtest.

The MSCI source is requested from 2000 onward; actual availability varies by
index. An ETF or other instrument cannot show prices from before its own available
history. Missing initial SMA values are left blank until sufficient earlier
observations exist. The app never silently substitutes an older proxy instrument.

## Account rules {#account-alerts}

**LTV** divides recorded account loans plus negative cash obligations by the
current gross value of your explicitly selected backing securities and positive
cash balances. Cash uses the canonical account balance, so adopted cash is not
counted twice. Linked securities remain ordinary holdings. No haircuts apply to
this gross LTV measure. Pending transfers and futures notional are excluded.
Choose your own maximum and warning LTV ratios; the app does not infer a broker's
liquidation threshold.

**Maintenance headroom** uses the existing pledged collateral after haircuts and
entered maintenance requirements. Its percentage is
`100 × (eligible collateral − maintenance requirement) / maintenance requirement`.
Choose trigger and warning headroom thresholds. The details show monetary
headroom and the date/source of the entered requirement.

Rules fix their calculation currency and show included inputs. Missing valuation
or FX withholds the result. Positive debt with zero backing triggers an explicit
undefined-ratio alert; zero debt with zero backing and zero maintenance
requirements make their respective percentage metrics inapplicable. These are
user-defined limits and declared coverage, not broker-specific liquidation models.

## Delivery and history {#notifications}

Monitoring runs while an app session is open, checking every five minutes with
a one-minute heartbeat. **Last rule check** and **Check all rules now** appear
only in **Tracked rules** and refer to all enabled saved rules. A completed pass
can include stale or unavailable results; check each rule's Data column.
**Refresh market data** in Browse refreshes the selected instrument's prices and
history without checking saved rules. Closing the app,
suspending the computer or losing the session interrupts monitoring. This release
does not install an operating-system background service or send external messages.

Approaching conditions show a banner inside Monitoring by default. Triggered
conditions also open an acknowledgment dialog on any tab, including Browse and
inline editors. An already open modal defers the alert until it closes. A rule can
instead use banners only or the notification center only. Notifications explain
the affected rule and offer **View rule**. Existing data-quality notices have
specific review actions.

Reading and acknowledging are separate. Opening Notifications marks entries
read; acknowledgment silences that alert and hides its banner. The current
condition remains visible in Tracked rules. Full recovery to normal rearms
the rule. Escalation from approaching to triggered produces a new alert, but
unchanged checks and threshold jitter within one warning episode do not repeat
pop-ups. Data failures preserve the last valid condition with an explicit stale
label rather than treating missing data as normal.

History retains observations and alert episodes from creation onward. Available
missed completed-close events can be recovered on reopening and are labeled with
the original observation date and later recording time. Intraday and account
history is never reconstructed across offline gaps. Charts show source history;
recorded observations show what monitoring actually evaluated. Provider revisions
can change a historical chart without rewriting the observation log.

Editing or pausing/resuming creates a new rule revision and evaluation baseline.
Deleting a rule preserves its recorded history. Data lives in private
`monitoring.json` and is included in workspace backups. Rule edits retain document
backups; frequent observation writes use atomic replacement without making a full
backup copy each time. Market downloads stay in private caches.
