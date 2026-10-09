<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/assets/readme-brand-dark.png" />
  <img src="docs/assets/readme-brand-light.png" alt="Breakdown" width="352" />
</picture>

---

A local portfolio-tracking and analysis app for understanding what you own,
which companies your ETFs actually expose you to, and whether your money is
allocated as planned. All portfolio data is stored locally on your computer.

![Overview with total value, allocation sunburst and category targets, using an invented demo portfolio](docs/assets/demo-overview.png)

<small>*Demo portfolio with synthetic holdings.*</small>

## What this app can do

This app is designed to help you understand your current holdings, analyze
the underlying exposure of your ETFs, and inform rebalancing decisions. Its
key features are:
- Track stocks, ETFs, crypto, and physical gold in EUR, USD, or GBP using public market quotes or manually entered prices.
- Group assets into categories and optionally define target allocations (e.g., 60% equities / 40% bonds).
- Set targets for individual assets within categories (e.g., 70% MSCI World / 30% MSCI EM within equities).
- Break down supported ETFs into their underlying holdings, combining direct and indirect exposure (e.g., NVIDIA shares held directly and through an MSCI World ETF).
- Analyze portfolio exposure by theme, sector, and geography.
- Examine portfolio beta, annualized volatility, and correlations between assets.

The app focuses on analyzing current holdings rather than tracking historical
performance. It does not reconstruct or maintain a complete transaction or
return history, connect to brokerage accounts, automatically import holdings,
execute trades, or calculate taxes. Automatic ETF breakdowns are limited to
supported providers, and underlying holdings data may be missing or incomplete.

## How to use

### Install a release

Open the [latest release](https://github.com/eliaskempf/portfolio-breakdown/releases/latest)
and download the package for your operating system under **Assets**. Python and
uv are not required for release packages.

- **Windows:** download the file ending in `windows-x64-setup.exe` and run it
  directly—no extraction needed. Follow Setup, then open **Portfolio Breakdown**
  from the Start Menu or desktop shortcut. Setup offers Microsoft's WebView2
  runtime if needed for the standalone window. The installed **Portfolio
  Breakdown (browser)** shortcut opens the browser alternative.
- **Linux (Ubuntu 22.04/24.04, x64):** download the `.deb` installer, install it
  with your package manager, then launch the app from your applications menu.
  It opens in its own standalone window.
- **macOS (experimental, Apple Silicon, macOS 14+):** download the `.dmg`, open
  it, and drag **Portfolio Breakdown Experimental** into **Applications**.
  It opens in its own window. The app is not Developer ID signed or notarized,
  so Gatekeeper may block opening it. Installation approval on a personal Mac
  remains unverified; see the release notes for known limitations.

A portable Windows ZIP is also available: extract it and run
`Portfolio Breakdown.exe`, keeping all accompanying files beside it.
For Linux browser mode, download the file ending in `linux-x64.tar.gz`, extract
it, and run `./portfolio-app`; keep its whole application folder together.
See the [installation guide](docs/user/install.md) for details,
updates and removal.

### Run from source

For development or running a source checkout instead of an installed release,
install [uv](https://docs.astral.sh/uv/getting-started/installation/), then:

```sh
git clone https://github.com/eliaskempf/portfolio-breakdown.git
cd portfolio-breakdown
uv sync --locked
uv run portfolio-app
```

`uv` manages Python and the project environment. On Windows, use
`uv run portfolio-desktop` for the standalone window. `uv run portfolio-app --demo`
opens the live demo; add `--offline-demo` for synthetic offline data.

### Get started and find help

Start with the [getting-started walkthrough](docs/user/getting-started.md), or take
the optional tour inside the app. The [full user guide](docs/user/index.md) covers
setup, imports, calculations, backups and troubleshooting.

Installed builds include matching offline documentation under **? → User guide**.
The [documentation website](https://eliaskempf.github.io/portfolio-breakdown/) is
prepared for GitHub Pages publication; until it is enabled, use the bundled guide
or the source links above.

## Disclaimer

Portfolio Breakdown is a portfolio analysis tool, not investment advice. Market data,
ETF holdings, classifications, and calculations may be delayed, incomplete, or inaccurate.
Always verify important information independently before making investment decisions.

[GPL-3.0-only](LICENSE).
