# Vorabpauschale estimates

**Taxes → Vorabpauschale** estimates the annual fund amount and its German
private-investor tax charge. Select a calculation year, broker/accounts and ETFs.
The result includes capital gains tax, solidarity surcharge and optional church
tax. Results come first; **Filter** selects accounts and funds, **Tax settings**
contains the allowance and church tax, and **Fund details → Edit inputs** opens
overrides for one fund. It does not book transactions or claim to reproduce a
bank's tax statement.

Completed years use ownership at 31 December. The current-year forecast keeps
current shares and the latest available price unchanged through year-end, with
no future trades. It uses observed distributions plus any additional expected
amount entered in **Edit inputs**. The price date and forecast basis remain
visible. The supported calculation years are 2018–2026; a new year needs its own
published rate, never the previous year's rate.

## Inputs and completeness

Applied history supplies acquisition months, remaining quantities, FIFO sales
and transfers. Acquisition months follow recorded transaction dates, not
settlement dates; verify imported dates if the source uses booking dates.
Purchase prices and fees are not needed. Shares bought during the
year receive the remaining calendar months' fraction, including their purchase
month. Sold shares do not contribute. Transfers between your own depots retain
original acquisition dates. FIFO is calculated within a depot/share class before
combining accounts; allocation sleeves are not separate tax depots.

Year-start and year-end **redemption prices** are the legally preferred fund
prices. Market prices are the statutory fallback when no redemption price is set
(or it is not set continuously at least monthly). Otherwise the app's market
prices are an explicitly labelled approximation. **Edit inputs** accepts EUR
per-share price overrides and an evidence source or assumption. Foreign-currency
market observations are converted using dated ECB reference rates, with the last
published rate on or before non-publication days (at most seven days old).

Distributions mean the fund's whole-year gross distributions per share, including
payments before you acquired shares. They are not just your personal net cash
receipts. **Automatic** uses downloaded gross payouts for distributing funds and
zero payouts for a verified accumulating share class. The calculator reuses the
app's existing issuer metadata and private verified income-policy overrides by
exact share class. The verification date records when metadata was checked, not
when the share-class policy began. Calculations use that known policy across the
selected history; supply a year-specific override if the policy changed. Its
source and verification date are shown in **Fund details**. Conflicting payout
observations require review.

Downloaded fund observations remain separate from transactions. An empty download
without a known accumulating policy does not prove zero. **Resolve missing inputs**
opens the affected fund. Select **Accumulating / no distributions** when applicable,
enter a **Manual total**, or explicitly select **Market history** to use the observed
amount as an assumption. Manual overrides take precedence. For a forecast, a manual total is
observed distributions so far; additional expected distributions are added once.
Market distributions use provider event dates, which may differ from payment
dates. Supply the annual EUR total for a more precise calculation. A quote in
subunits such as pence does not establish dividend units; ambiguous downloaded
distributions remain unavailable until reviewed.

Prices, distributions and quantities must use consistent share units. Downloads
reverse provider split normalization and normalize to the calculation boundary.
Conflicting split evidence requires review. Missing acquisition dates, unexplained
balance replacements or unsupported events leave the affected result unavailable.
An applied confirmation that ownership began with the first recorded purchase
establishes zero holdings before that purchase. An ordinary later balance snapshot
does not establish earlier ownership, even when its recorded balance is zero.
Reviewed remaining lots can supply a calculator-only assumption without changing
history; changing the history requires another review. Historical selection
includes zero-share and retained closed positions. Unlinked histories need their
fund status reviewed and may lack a usable name or price listing.

**Filter → Include unclassified funds** exposes positions whose
investment-fund status is not known. Confirm **Investment fund** in their review
before including their amounts. An ETF name or an investment taxonomy does not
establish tax classification.

## Optional tax settings

Teilfreistellung defaults to **None / not applied**. Select the applicable rate
per fund/year; it is shared across that share class's depots. The ordinary private
investor rates are 30% for equity funds, 15% for mixed funds, 60% for property funds
and 80% for foreign property funds. These are legal fund classifications, not
allocation categories or the industries represented by an ETF.

Church tax defaults to zero; 8% and 9% are available. Its deduction reduces the
capital gains tax base rate through the statutory formula. Solidarity surcharge
is included. Loss offsets, foreign-tax credits and personal income-tax assessment
are not modelled.

**Available allowance** is the amount still available for the selected scope in
the *receipt year*, which follows the calculation year. It defaults to €0 and is
applied once to the combined taxable amount. Changing the selection or year resets
it; fund rows show tax before allowance. Do not reuse the same available allowance
in independent estimates and then add the after-allowance tax totals.

The headline is a **partial subtotal** whenever selected funds remain unavailable.
The table identifies the gaps; missing results are never silently treated as zero.
All results remain estimates, including calculations with manually supplied fund
prices. Special cases such as mergers, liquidations, transfers to another owner,
changes of residence or changes of tax classification need separate review.

## Calculation and official sources

For per-share year-start price `P0`, year-end price `P1`, gross annual
distributions `D` and the year's Basiszins `b`:

```text
base = min(P0 × 0.70 × b, P1 − P0 + D)
annual amount = max(0, base − D)
lot amount = annual amount × remaining shares × acquisition-month factor
```

The factor is `(13 − purchase month) / 12` for purchases during the year, otherwise
one. Sum unrounded contributions and round once per depot/share class to cents.
Apply the selected Teilfreistellung, then deduct the available allowance once.
With church-tax rate `k`, capital gains tax is `tax base / (4 + k)`, church tax is
that tax times `k`, and solidarity surcharge is 5.5% of that tax. The surcharge
truncates fractions of a cent. Displayed monetary estimates use cent precision;
bank posting and aggregation conventions can differ.

The rules and annual parameters use only official German documentation:

- [§18 InvStG: calculation, monthly reduction and receipt](https://www.gesetze-im-internet.de/invstg_2018/__18.html).
- [BMF, 21 May 2019, paragraphs 18.2–18.12: precision, currency conversion and prices](https://www.bzst.de/SharedDocs/BMF/DE/Downloads/bmf_schreiben_20190521_InvStG_18_anwendungsfragen.pdf?__blob=publicationFile&v=1).
- [§20 InvStG: Teilfreistellung](https://www.gesetze-im-internet.de/invstg_2018/__20.html).
- [§32d EStG: tax and church-tax formula](https://www.gesetze-im-internet.de/estg/__32d.html), [§4 SolzG: solidarity surcharge](https://www.gesetze-im-internet.de/solzg_1995/__4.html).
- [Bundesbank: relevant annual Basiszins series](https://www.bundesbank.de/de/statistiken/banken-und-andere-finanzielle-unternehmen/investmentgesellschaften), using the first published daily observation of each calculation year.
- [BMF 2025 rate: 2.53%, receipt 2 January 2026](https://www.bundesfinanzministerium.de/Content/DE/Downloads/BMF_Schreiben/Steuerarten/Investmentsteuer/2025-01-10-basiszins-vorabpauschale-zum-2-1-2025.pdf?__blob=publicationFile&v=2).
- [BMF 2026 rate: 3.20%, receipt 4 January 2027](https://www.bundesfinanzministerium.de/Content/DE/Downloads/BMF_Schreiben/Steuerarten/Investmentsteuer/2026-01-13-basiszins-berechnung-vorabpauschale.pdf?__blob=publicationFile&v=1).

Saved overrides, optional settings and reviewed lots live in the private
`tax-inputs.json`; fetched observations live under `.cache/tax/`. Saves use the
workspace's normal revision checks and backups. Market refreshes never overwrite
manual inputs. The feature loads and calculates only when Taxes is open.
