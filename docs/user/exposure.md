# Exposure and ETFs {#look-through}

## Read direct and indirect exposure {#filters}

In **Exposure**, Assets combines direct holdings and fund-derived exposure.
**Break down ETFs** starts enabled; supported funds start selected. Use
**Individual ETFs** under **Data & settings** to keep a specific fund whole.
Unsupported, missing or disabled breakdowns remain whole instruments. Expansion
never creates owned positions or changes quantities and strategic categories.

Each constituent contribution equals the fund position's EUR value times the
stored constituent weight. Matching identities combine direct and indirect value;
source details preserve account and portfolio contributions. Source-position
filters apply **before** expansion. Search hides resulting assets without changing
the selected source scope's percentage denominator. Hierarchy navigation drills
into the resulting allocations. Missing source valuations keep full-selection
percentages unavailable.

Classifications use local taxonomy paths, with provider metadata as fallback.
Unclassified is a lack of labels, not a missing price. Company-country geography
is not revenue exposure; listing currency, exchange and fund domicile are not
substitutes. Unknown geography and coverage remain visible. The stock-only view
excludes non-equity sources and reports unresolved coverage separately.

## Understand Other and dates {#other}

Constituent weights are fractions of the **whole fund**. In an invented 80%-covered
snapshot, named holdings retain 80% and the remaining 20% stays **Other**. The
app does not scale named constituents to 100%. Other can represent a partial
snapshot and is not automatically an error. Check the source interpretation and
holdings date before drawing conclusions from a large residual.

A source's holdings date differs from a download/check time. Automatic checks
normally consider snapshots at least one day old, with one attempt per fund per
24 hours. The threshold can be set from 1–30 days or automatic checks disabled.
**Refresh ETF holdings now** bypasses the throttle. Failed updates retain prior
snapshots; a fresh check may return the same old holdings date. Snapshots older
than seven days retain a recency notice. Price refresh and ETF refresh are separate.
The app must be running for its background checks.

## Set up and inspect a source {#sources}

Open **Data & settings → ETF refresh & snapshots** for status, source links and
saved snapshots. Select an asset for its contributing positions and ETF breakdown.
Automatic discovery covers supported physical equity/bond products from official
iShares and Xtrackers listings, alongside existing explicit integrations. It is
not universal ETF support.

ISIN is authoritative; WKN/listing searches need a unique issuer-confirmed match.
Use **Set up a breakdown** with an official iShares/Xtrackers product page when
automatic discovery cannot resolve it. Names and bare tickers are insufficient.
Bare `SMH` is the US-listed fund, not a UCITS listing.

For a physical fund from another source, supply a normalized UTF-8 CSV, exact
fund ISIN, holdings date and asset class. Required columns are
`constituent_id,name,ticker,isin,weight`. Weights use fractions, not percentages.
Bond/money-market rows additionally need `instrument_type`; optional metadata
includes issuer, country, market currency, ISO maturity and credit rating.
Review identity, coverage and interpretation before **Save breakdown**. A manual
CSV disables provider refresh for that snapshot; saving a reviewed official
source re-enables it. Draft uploads remain in memory until saved.

The Amundi Europe Momentum integration is explicitly a same-index iShares proxy,
not the actual Amundi holdings or an exact index file. Proxies approximate
allocation and must remain labeled. Unverified synthetic compositions stay whole.

## Bonds and overnight-rate funds {#bonds}

Physical bond funds offer Summary by issuer, country, denomination currency,
maturity and credit quality, plus individual securities under Holdings. Different
bonds stay distinct from one another and from the issuer's equity. Maturity bands
use the snapshot date. Unknown metadata stays unknown. Published aggregate ratings
are not assigned to individual bonds. Duration, yield and other provider metrics
retain their own dates. Denomination currency does not measure hedged net FX risk.

XEON's economic view represents its EUR overnight-rate benchmark. Its actual
substitute basket is available separately under Holdings with signed weights;
that basket does not enter portfolio company, country or bond allocations.
Economic representation and basket coverage are separate. Provider cash netting
can form a net liquidity pool without a country/currency assignment; it must not
be read as extra equity exposure. Unsupported net borrowing is rejected.

## Review company merges and display groups {#merges}

**Company merges** uses shared security identities and reviewed equivalences.
Where identities are missing, estimated normalized full-name matches are marked
`*`; these are not issuer verification. Inspect source names, identifiers, funds,
dates and match method. **Undo merge** keeps a group separate; **Restore merge**
combines it again. Choices are saved privately. Merging values does not change
owned positions or multiply exposure by ADR receipt ratios.

The optional **Group SMH with related stocks** is a display group containing the
whole selected UCITS fund and selected direct stocks. It follows the fund's labels;
other ETFs can still expand. Effective exposure retains original contributions.
This grouping does not change saved holdings, targets or buy-ins.

## Compare overlapping labels {#labels}

In the label comparison controls, a selected label includes descendants. If an
asset matches several selected labels, explicitly choose **Split equally** or
**Count in each label**. Splitting divides matching value into a 100% allocation;
counting includes the full value in each matching label and percentages can total
more than 100%. Overlap uses bars rather than a pie/sunburst implying a partition.
Coverage identifies value outside the selected labels. AI theme membership
indicates a business role, not a measured percentage of company revenue from AI.
