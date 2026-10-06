<img src="src/portfolio_app/assets/portfolio-breakdown.svg" alt="Portfolio Breakdown logo" width="88" align="right" />

# Portfolio Breakdown

A local portfolio-analysis app for understanding what you own, what is inside your
ETFs, and how your allocation compares with your targets. Your portfolio is stored
on your computer. Windows uses a standalone app window; Linux uses your browser.

## What it does

- Add holdings manually or review an experimental CSV/Excel holdings import.
- Explore categories, optional allocation targets, and within-category targets.
- Value stocks, ETFs, crypto and physical gold in EUR, USD or GBP using public quotes or manual prices.
- Break down supported ETFs and combine direct and indirect exposure.
- Review gains against recorded costs, historical risk, and rebalancing suggestions.
- Try an editable demo with invented holdings and live market data.

It does not connect to brokerage accounts, place trades, calculate taxes, or
reconstruct a complete transaction/return history. ETF coverage is limited to
supported sources; missing or partial data stays visible. FinanzManager report
recognition is provisional. Reporting currency defaults to EUR. The futures sandbox is not included in v1.

## Install a release

Download from [Releases](https://github.com/eliaskempf/portfolio-breakdown/releases).
On Windows, run the **windows-x64-setup.exe** installer, then open **Portfolio
Breakdown** from the Start Menu. Python and uv are not required. Setup offers
WebView2 installation when needed; a browser fallback and portable ZIP are also
available. On Linux, extract the **linux-x64.tar.gz** archive and run `portfolio-app`.

Before v1 is published, test packages come from a successful
[Build candidate run](https://github.com/eliaskempf/portfolio-breakdown/actions/workflows/candidate.yml).
Download and extract the candidate artifact to find the installer or application archive.
These candidates are not official releases.

## Run from source

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), then:

```sh
git clone https://github.com/eliaskempf/portfolio-breakdown.git
cd portfolio-breakdown
uv sync --locked
uv run portfolio-app
```

`uv` manages Python and the project environment. On Windows, use
`uv run portfolio-desktop` for the standalone window. `uv run portfolio-app --demo`
opens the demo; add `--offline-demo` for synthetic offline data.

Detailed instructions are in the app's **? → User guide** and the
[documentation site](https://eliaskempf.github.io/portfolio-breakdown/).
The site is prepared for publication; until it is enabled, use the guide bundled
with the candidate or [guide source](docs/user/index.md). Start with the
[guided walkthrough](docs/user/getting-started.md).

[GPL-3.0-only](LICENSE).

Complete portfolio archives are available under **Portfolio settings → Create backup /
Restore backup**. Restore reviews and verifies the archive, creates a new workspace
and switches only after confirmation. The previous workspace remains intact. See
[storage and recovery](docs/user/storage.md#backup) for restart behavior and the
separate stopped-folder CLI commands. CSV/Excel holdings import remains separate.
