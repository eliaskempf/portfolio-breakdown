# Troubleshooting {#troubleshooting}

| Symptom | What to check |
| --- | --- |
| Browser tab closed but app still runs | Stop the managed instance using the same [workspace and stop command](install.md#launch-stop). |
| Another version or mode opens | Stop that workspace's instance before relaunching; verify the executable/checkout and data directory. |
| Explicit port is occupied | Use a different port or stop the owning app. Do not stop another workspace blindly. |
| Package fails to start | Keep `_internal` beside the executable; try `--foreground` for diagnostics. Desktop startup logs are private per-user state files. |
| Source changes do not appear | Restart the owning server after Python module changes; browser refresh alone can leave imported modules stale. |
| Position value is blank | Inspect ticker, manual price, quote currency, FX and [price status](performance.md#valuation). Buy-in is not a market price. |
| Refresh shows old data | Cached fallback preserves the last successful data. Check provider dates separately from retrieval time. |
| Demo cannot initialize | Normal demo needs public prices/FX. Retry, or restart with `--demo --offline-demo` for an invented offline example. |
| Large Other / whole ETF | Inspect [coverage, source date and support](exposure.md#other). A partial or absent breakdown is not a zero holding. |
| Import is unavailable | It requires an empty portfolio. Use Update balances for existing holdings. |
| Wrong imported decimals or rows | Recheck number format, header, worksheet, delimiter and mappings before accepting. |
| Cost/return is blank | Check [buy-in currency and coverage](performance.md#performance); unknown is not zero. |
| Risk cannot calculate | Check common history, benchmark, exclusions and the 52-week minimum in [Risk](analytics.md#risk). |
| Cannot save an old form | Another write changed the file. Reload the latest data and reapply the intended edit. |
| Plan is infeasible | Review target totals, valuation coverage, minimum buys, protected categories, eligibility and caps. |
| Restore refuses a destination | Use a new directory; [restore](storage.md#backup) intentionally refuses overwrite. |

If reporting a problem, provide the app version/source commit, operating system,
action and a minimal invented reproduction. Review diagnostics before sharing.
Do not attach private workspace files, logs or portfolio screenshots. A successful
server health response confirms liveness only; check the actual affected view.
