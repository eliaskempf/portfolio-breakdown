# Troubleshooting {#troubleshooting}

| Symptom | What to check |
| --- | --- |
| Browser tab closed but app still runs | Relaunch the same shortcut to reopen it, then choose **Portfolio settings → App & workspace → Stop application**. See [launch and stop](install.md#launch-stop) for recovery commands. |
| Another version or mode opens | Stop that workspace's instance before relaunching; verify the executable/checkout and data directory. |
| Explicit port is occupied | Use a different port or stop the owning app. Do not stop another workspace blindly. |
| Package fails to start | Keep the complete application folder together, including its `_internal` or `_internal-<build>` runtime. On Windows, try **Portfolio Breakdown (browser)** in the Start menu. See startup logs below. |
| Source changes do not appear | Restart the owning server after Python module changes; browser refresh alone can leave imported modules stale. |
| Position value is blank | Inspect ticker, manual price, quote currency, FX and [price status](performance.md#valuation). Buy-in is not a market price. |
| Refresh shows old data | Cached fallback preserves the last successful data. Check provider dates separately from retrieval time. |
| Amundi overnight fund reports an unreliable listing | For ISIN LU1190417599, use **Positions → Connect live prices** to review CSH2.PA (Paris) or SMART.MI (Milan). The 0E2B.IL feed is excluded from live prices and history, including old cache entries. Existing quantities and manually supplied prices are retained. |
| Demo cannot initialize | Normal demo needs public prices/FX. Retry, or choose **? → Take the tour** for an invented offline example. |
| Large Other / whole ETF | Inspect [coverage, source date and support](exposure.md#other). A partial or absent breakdown is not a zero holding. |
| Holdings snapshot import is unavailable | Only holdings snapshots require an empty portfolio. Choose Transactions to import trades into existing holdings, or Update balances to replace current quantities. |
| Wrong imported decimals or rows | Recheck number format, header, worksheet, delimiter and mappings before accepting. |
| Cost/return is blank | Check [buy-in currency and coverage](performance.md#performance); unknown is not zero. |
| Risk cannot calculate | Check common history, benchmark, exclusions and the 52-week minimum in [Risk](analytics.md#risk). |
| Cannot save an old form | Another write changed the file. Reload the latest data and reapply the intended edit. |
| Plan is infeasible | Review target totals, valuation coverage, minimum buys, protected categories, eligibility and caps. |
| Restore refuses a destination | Use a new directory; [restore](storage.md#backup) intentionally refuses overwrite. |

## Startup logs

On Windows, the normal **Portfolio Breakdown** shortcut opens the app window;
**Portfolio Breakdown (browser)** opens it in your default browser. Both use the
same local server, so switching presentation may not resolve a server failure.

The window's startup error shows the actual log folder. To open the default
location, paste `%LOCALAPPDATA%\portfolio-breakdown\window` into File Explorer's
address bar. Look at the newest `window-*.log` files: the launcher and server can
write separate logs. Browser-launch diagnostics are in
`%LOCALAPPDATA%\portfolio-breakdown\launcher.log`.

If reporting a problem, provide the app version/source commit, operating system,
action and a minimal invented reproduction. Review diagnostics before sharing.
Share only relevant error lines after removing personal paths and portfolio details;
do not attach complete private logs, workspace files or portfolio screenshots. A successful
server health response confirms liveness only; check the actual affected view.
