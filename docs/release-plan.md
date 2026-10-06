# First release handoff

## Decisions

- GPL-3.0-only. Browser-first, local application for individuals and technical peers.
- Preserve `uv run` development and source installation alongside frozen packages.
- Initial binary targets: Windows 11 x64 and Ubuntu 22.04/24.04 x64. Other platforms are unverified.
- Preserve the valuation → exposure → taxonomy → aggregation pipeline and financial semantics.
- Build candidates as Actions artifacts BEFORE creating tags or GitHub Releases.
- Publish only explicitly selected, tested candidate bytes; never rebuild during promotion.
- User-supplied artwork is integrated: portfolio-breakdown.svg/.png and favicon.svg/.ico.
  The full icon is used for desktop branding; the favicon SVG is used in the browser.
- V1 includes the experimental holdings import and later live-price linking from
  main (330ef27). Futures sandbox modules, UI and CLI are excluded.
- V1 also includes the isolated ETF discovery, source setup, bond summaries and
  separate overnight-rate basket support described in the integration record below.

## Milestones and status

| Milestone | Implementation | Acceptance |
| --- | --- | --- |
| A: license, handoff, CI | Implemented | Local checks pass; hosted Windows/Linux runs pending |
| B: external storage/recovery | Implemented | Synthetic migration, overwrite refusal and restore tests pass |
| C: launcher/focused cleanup | Implemented | Source and Linux bundle lifecycle/browser tests pass; native shortcuts need manual acceptance |
| D: candidate packaging | Implemented | Linux bundle built and browser-tested outside checkout; Windows/Ubuntu runner matrix pending |
| E: final readiness | Pending | Artwork integrated; clean-machine acceptance and selected-candidate approval pending |

The active v1 branch is `release-v1`, based on main's import commit (330ef27),
with the release infrastructure and artwork from `release-preparation` applied.
It supersedes the earlier futures-based release candidate. The futures branch
and its uncommitted work remain separate and unchanged. No merge of that branch
is required for v1. Generic ETF parsing rules for derivative holdings still apply.

## Workflow contract

CI runs on pull requests and main-branch pushes using locked dependencies.
Candidate builds run manually against a selected commit, upload artifacts for
30 days and create no tag/release. Each platform manifest records source SHA,
version, lock hash, run ID and checksums. Package tests run against extracted
bundles. Publication is a separate manual workflow selecting a successful
candidate run and acknowledging completed manual acceptance. It checks all
identities/checksums, then publishes those files without rebuilding. Expired or
incomplete candidates fail closed; any changed code, dependency or icon requires
a new candidate. No release publication is authorized by implementing this plan.

## Required acceptance

- Fresh/empty workspace, offline demo, saved data after restart.
- Overview, Exposure, Positions and Rebalance navigation; no Futures tab or sandbox CLI.
- CSV and Excel holdings import, review/cancel, save/restart and later listing setup.
- Real chart/list/editor interactions, not just a health endpoint.
- Spaces/non-ASCII paths, independent launch directory, repeated launches and port conflicts.
- Desktop shortcut, stop/restart, reboot, no orphaned server.
- Missing/stale quotes preserve access to holdings.
- Copy-based migration, verified backup/restore, update/uninstall preserve user files.
- Wheel/source/binary contents, licenses, checksums and final icons verified.
- Downloaded candidate tested manually on Windows and Linux without Python/uv.
- Upgrade between candidates; subsequent releases also test upgrading previous official release.

Record evidence in docs/release-checklist.md and the candidate run. Do not record
personal portfolio values or private screenshots. Local tests use temporary
synthetic workspaces only. Never silently migrate the actual working portfolio.

## Local validation and next session

On 2026-10-03, the combined release-v1 suite passed locally with 791 tests and no
skips, including import, workspace isolation, Chromium browser suites, shutdown
lease handling and candidate integrity checks. Ruff correctness checks,
actionlint and the staged Git privacy check passed. The source app's browser
smoke check passed with CSV and real XLSX uploads, reviewed import, editing,
save/restart, repeated launch, detached startup/shutdown and backup/restore.
The same smoke check runs against the extracted candidate bundle, with no
checkout imports available to the app. It also checks the favicon, main tabs,
Targets and absence of the Futures tab/CLI. Only invented data is used.
The local host is older than the supported Ubuntu runner baseline; this is not
a substitute for hosted/clean-machine acceptance. No GitHub workflows have been
run, no Windows build has been verified, and no release/tag has been created.

Next: integrate the audited release-v1 changes into main, push the workflows
and make them available on the default branch, run Build candidate, then download
and test that specific candidate on supported machines. The four supplied artwork
files under src/portfolio_app/assets are included in packages. Publication
refuses missing artwork or an unclean source checkout. Use Publish tested candidate
only after recording the manual checklist for its run ID. A failed/partial rerun
requires rerunning the entire candidate workflow so both platforms share one run
attempt. Interrupted publication leaves a draft for inspection, never an official
release missing uploads.

## Other assessment items retained

Release requirements: concise onboarding, visible workspace/version information,
open-folder action, startup diagnostics, tested recovery and upgrade instructions.
Classification YAML remains documented for v1. Shared ordinary read-only lists,
formatting and theme defaults should be reused; native editable grids and
diagnostics can remain. Centralize locking and workspace reset policy where
needed, without a wholesale reorganization. Streamlit DOM adapters need browser
tests and locked release dependencies.

The holdings importer remains explicitly experimental: current holdings reports,
empty portfolios, reviewed mappings, and optional dated prices. Provider-specific
FinanzManager recognition is provisional; transaction history and portfolio sync
are excluded. Packaging must retain the Excel reader and test it inside the bundle.

Backlog: small classification editor, verified broker-specific import presets, consistent
analysis exports, standalone webview window, additional verified platforms.
Out of scope: transaction/tax accounting, realized gains, reconstructed personal
historical returns, universal ETF ingestion, cloud accounts, database migration.
Existing valuation, targets, rebalancing, ETF exposure and risk features are
sufficient for this first release; no new financial features are required.


## Bounded architecture cleanup (2026-10-03)

Implemented on release-v1 after scope agreement:

- ETF refresh uses the shared cross-platform lock. Nonblocking Windows lock
  contention has the same exception contract as Unix; unrelated I/O failures
  remain errors. Refresh no longer imports Unix-only fcntl at UI startup.
- Import uploads and ordinary review controls have explicit session-only draft
  ownership in import_state.py. Same-workspace navigation retains bytes, mappings,
  parsing choices, confirmations and edited rows. Cancel, completion and workspace
  switching clear drafts; no upload or draft is persisted to disk. The remounted
  Position tools selector also uses the remembered workflow as its default, so
  the next click cannot silently return to Positions before an import saves.
- analytics_service.py owns fundamentals/history loading and orchestration without
  Streamlit. analytics_ui.py retains formatting and controls. exposure_analysis.py
  owns source selection and normalized ETF expansion; rendering retains labels and
  presentation. Existing calculation order, denominators and missing-data handling
  are preserved.
- Spot, chart and risk paths share case-sensitive quote-subunit normalization.
  Performance charts, cards and lists share gain/loss color defaults.
- Suggested trades uses the existing shared read-only list. Full planning details,
  editors and diagnostics remain native grids. Themes tables retain their native
  optional-column controls; converting those would require additional interaction
  work and is deferred.

Deferred: app-wide state-model replacement, persistence transaction consolidation,
large Exposure/UI rewrites, and further table conversion. Release workflows,
locked dependencies, uv run entrypoints, holdings import scope and futures exclusion
are unchanged. The four supplied icons were already integrated and remain intact.

Validation: all 779 non-browser tests passed in the final full-suite run; after
fixing browser synchronization, the complete 25-test Chromium suite passed with
no skips (804 tests covered across those runs). The source application's
fresh-process Chromium smoke check passed,
including CSV/XLSX uploads, favicon decoding, navigation, save/restart, repeated
launch, shutdown, and backup/restore in temporary synthetic workspaces. No existing
preview was restarted. Final smoke previews used temporary demo data and an invented persistent
workspace in the isolated release checkout. Wheel/source archive content checks and artwork inclusion passed;
Ruff correctness and the tracked-file privacy check passed. All new source and
test files were also reviewed for private content. Browser tab-roundtrip tests
now wait for the intermediate view to mount before switching back, and keyboard
metric interaction waits for the chart-triggered rerun to settle. Existing
assertions are preserved. Local validation used uv run with the worktree
environment and an explicitly selected cached Chromium headless shell; no live
portfolio data was used.

These source changes require a new candidate; earlier candidate acceptance does
not cover them.
Native Windows/Ubuntu acceptance and explicit publication approval remain pending.

## First use and demonstration portfolio (2026-10-03)

Empty persistent portfolios now offer Explore demo, Start manually and Import
holdings. These choices use the existing temporary demo workspace, position form
and reviewed importer. Existing holdings skip welcome. Getting started returns
an empty workspace to the choices; switching workspaces resets first-use state.
FinanzManager recognition remains provisional, with no additional import formats.

The default demo is an invented EUR 100,000 portfolio. Its target/current category
weights are equity 60/61%, money market 25/24%, gold 10/11% and crypto 5/4%.
Equity targets are 70/30 Xtrackers MSCI World / iShares Core MSCI EM IMI. The other
positions are Xtrackers EUR Overnight Rate Swap, EUWAX Gold II, Bitcoin and Ethereum;
the crypto target split is 60/40. Fixed invented buy-ins create both gains and
losses (EUR 6,557 net), and saved allocation targets allow immediate rebalancing.
Only instrument identities are public metadata. Quotes and partial ETF snapshots
are explicitly synthetic and work offline; both equity funds expand with residual
Other. The money-market fund stays intact. The tour explains these distinctions.
The previous synthetic dataset remains a test-only fixture for legacy allocation,
duplicate-identity and missing-data regression coverage.

Validation: all 808 tests passed, including all 25 required Chromium browser
tests with no skips. Fresh source-app browser/lifecycle checks exercised welcome,
demo, manual cancellation, CSV/XLSX import, ETF breakdown off/on, rebalancing,
save/restart, repeated launch, detached demo startup/reset, shutdown and
backup/restore. Source previews ran from the isolated release checkout using only
temporary invented workspaces. No existing preview was restarted.
Wheel/source archive content checks, Ruff correctness checks, tracked-file privacy
checks and a separate scan/review of changed and new files passed.

Release dependencies, uv run development, build/promotion workflows and the
futures exclusion remain unchanged. The package smoke test now covers the welcome
routes and new demo, including the packaged --demo launch path. This work still
requires a fresh Windows/Linux candidate and native acceptance; no official
release is approved by these local checks.

## Pre-candidate privacy audit (2026-10-03)

Reviewed the current 194 source/documentation/artwork files and all 40 commits
then reachable from release-v1 (513 unique approved source blobs), including
commit messages. No prohibited portfolio paths or recognized credentials or
personal home-directory paths were found in that source/history scan. Private
workspace files were neither read nor compared. Public instrument catalogs,
provider integrations and explicitly invented fixtures remain; their presence
does not establish personal ownership, but supported instruments can suggest
interests. Normal Git author metadata and older generic temporary-path examples
remain in history. No history was rewritten.

Removed temporary checkout paths from the current handoff. The four icons contain
no embedded private metadata or external SVG references. Added rejection of user
home paths to the privacy guard and content scanning inside wheel/source archives.
Frozen packages now exclude editable-install direct_url.json provenance and check
first-party source/metadata. Linux archive ownership is normalized to zero IDs and
empty names instead of disclosing the builder's account.

The local Linux frozen executable passed the expanded browser/lifecycle smoke
test after removing installation provenance. Scanning 4,811 decompressed Python
modules found local interpreter paths in the third-party sysconfig module; that
development-environment build must not be distributed. Windows/Linux distributable
candidates must be built on clean hosted runners and inspected after download.
No private holdings, reports, caches, credentials or screenshots are packaged.
The audit is not a mathematical guarantee that arbitrary prose or public ticker
selection cannot disclose preferences. Package/privacy regression tests pass;
native Windows acceptance and official publication approval remain pending.

## Extended ETF breakdown integration handoff

Prepared on `feature/etf-release-v1`, based on release-v1 commit
`6fd87c6bd13729bd61fedfd6ef5a285fc90cde5f`. Neither `release-v1` nor `main` was
merged or advanced during this preparation. The original working checkout was
left intact. The extension previously existed as uncommitted changes mixed with
futures experiments; there was no existing ETF extension commit to cherry-pick.

Apply these focused commits in order after reviewing the release branch state:

| Commit | Scope |
| --- | --- |
| `0c2e98f94d3d843ecc3949a21ff957de0eb392ac` | Provider discovery, validated publication and refresh, bond/rate semantics, summaries, basket-aware workspace copying, synthetic backend tests |
| `950b8c70cf9017620287e16865307fb82bca3eca` | Source setup, fund summaries and saved breakdown UI, workspace draft reset, synthetic browser tests, ETF README documentation |

Dependencies are already present in the chosen release base:

- `330ef27`: preliminary holdings import and later live-price linking, including
  python-calamine. Do not copy the older importer from the futures checkout over
  the release version.
- `a7ec302`: cross-platform `locking.write_lock` and release workspace support.
  Both automatic refresh and manual ETF saves use this shared lock; the new
  modules have no unconditional POSIX `fcntl` import.
- `786c723`: release exposure-analysis and workspace-state refactors. UI wiring
  was adapted to these boundaries rather than replacing their modules wholesale.
- `6fd87c6`: retained onboarding, demo behavior and strengthened privacy checks.

No futures-branch commit, futures calculation, leverage setting or experimental
market-exposure module is required. A basket test that referenced the experimental
exposure module now verifies value conservation, non-equity typing, company
exclusion and geography exclusion through the existing release APIs. There are
no changes to `pyproject.toml` or `uv.lock` in the ETF commits.

Supported behavior:

- Newly added/imported funds can discover physical equity and bond holdings from
  supported official iShares/Xtrackers sources. Imported rows need not already
  have a price ticker or instrument type. ISIN is authoritative; WKN/listing
  candidates require issuer confirmation.
- Official product URLs can be previewed and saved, then refreshed through the
  normal held-fund updater. Normalized physical-holdings CSVs provide a manual
  fallback. Failed retrieval keeps the last valid snapshot; unresolved funds
  remain whole instruments. Partial holdings retain an explicit Other remainder.
- Bond views summarize issuer, country, denomination currency, dated maturity
  bands and available credit quality. Missing metadata stays unknown. Provider
  aggregate ratings and duration/yield figures retain their own dates and sources.
- XEON has a specific verified overnight-rate economic representation. Its signed
  substitute basket is stored and displayed separately, excluded from portfolio
  company/country/bond allocations. Existing explicit provider integrations and
  the labeled legacy proxy remain supported.

Validation of the isolated Linux checkout:

- `uv run pytest -q`: **811 passed, 12 skipped**. Browser modules are optional in
  that environment; these skips do not establish browser acceptance.
- Required affected-browser run: **5 passed, no skips** across
  `test_etf_discovery_browser.py` and `test_import_browser.py`, using temporary
  synthetic Streamlit apps and Chromium. These check bond summary defaults and
  charts, security details, the separate overnight basket, settings access without
  a live listing, and import workflows. Servers were stopped after testing.
  Local browser compatibility used Playwright 1.48; the release's locked browser
  group and its full browser suite still need to run on supported CI runners.
- `uv run ruff check src tests tools` and `git diff --check`: passed.
- `uv build` and `uv run python tools/release.py check --directory dist`: passed
  for wheel and source archive. An independently installed wheel, with runtime
  dependencies pinned from the unchanged lockfile, passed ETF/UI imports,
  synthetic snapshot save/reload and partial-coverage summaries outside the
  checkout; the installed command reports version 0.1.0.
- Staged diff review plus `portfolio-check-private --staged` ran before each
  commit; the enabled pre-commit hook audited the tracked index. Package content
  scanning also passed. Added tests invent their provider responses and positions
  in temporary directories. No working portfolio, cache, export, screenshot or
  private handoff content was copied into these commits or archives. Public
  instrument/provider identifiers describe supported behavior, not ownership.

Remaining limitations and release gates:

- Native Windows execution, frozen candidate testing and the full required
  browser suite have not been performed for this branch. Run the existing
  Windows/Linux candidate gates after integration; do not treat Linux tests or
  the portable lock adaptation as Windows acceptance.
- Discovery is bounded by supported provider metadata/export formats. WKN and
  ticker searches may be incomplete; bare tickers/names are not identities.
  Provider changes or unsupported structures can leave a fund unexpanded.
- Synthetic ETF economics are not general: XEON is explicitly handled, alongside
  the existing labeled proxy. There is no generic swap, hedging, duration-risk or
  signed portfolio allocation model. Currency summaries describe denomination.
- Automatic attempts require the app to be running and scheduled through app
  startup/interactions. Defaults are a one-day snapshot age and at most one
  automatic attempt per fund per 24 hours; this is not a separate scheduled job.
- Earlier public-source probes covered World, World ex-USA, DAX, Euro Government
  Bond and XEON. Those probes were not rerun for this isolation; the new validation
  is deterministic and offline. Provider availability must be checked separately
  if live-source acceptance is needed for the release candidate.

The integration commits are ready for release-session review. This handoff does
not approve a merge, tag, candidate distribution or publication.

## ETF integration review and validation (2026-10-03)

Integrated the two ETF commits and their handoff commit (`6a749d2`) into release-v1
by fast-forward from `6fd87c6`. No futures commits or dependency changes were
needed. The original working checkout and the ETF preparation checkout were not
modified. Release tooling, uv development, onboarding and import behavior remain.

Release review added three focused corrections with synthetic regression tests:

- Manual CSV snapshots retain an explicit manual provider choice, including funds
  present in the legacy registry. Automatic/forced refresh cannot overwrite them;
  saving a reviewed official source restores provider updates.
- WKN catalogue matches require the product metadata to confirm the catalogue's
  ISIN before its identifiers are accepted.
- iShares XML parsing preserves inherited namespaces. Malformed disclaimer
  recovery isolates the holdings sheet without repairing its financial contents.

The locked environment's complete 845-test run reported 843 passes and two browser
test failures: an immediate chart-count assertion and an outdated refresh-status
selector. After correcting those checks, all four tests in the affected browser
modules passed. All 845 tests, including the 28 required browser tests, are covered
across these runs with no skips. The refresh fixture now declares its invented
stock type so it cannot trigger live discovery. Ruff and diff checks pass.

The source-app browser/lifecycle smoke test also passed. It now uploads, previews
and saves an invented bond CSV, opens the default summary chart and security list,
and checks that backup/restore preserves its snapshots. All previews use temporary
synthetic workspaces; no existing session was restarted or private data accessed.
The rebuilt, extracted Linux executable passed the same expanded browser/lifecycle
smoke, including the new manual setup and backup/restore route. Wheel/source archive
content checks and frozen source/provenance checks also passed.

The updated privacy review scanned 44 reachable release commits, 558 unique source
blobs and 201 current allowed files, with no prohibited paths or recognized private
content. Public provider identifiers and invented fixtures were reviewed. The
earlier limitation about interpreter paths in locally built binaries still applies;
local builds are validation artifacts, not distributable candidates.

Native Windows/Ubuntu acceptance, fresh live-provider acceptance and explicit
publication approval remain pending. The next distributable candidate must be
built from the final clean integrated commit on the hosted Windows/Linux runners.

## First hosted candidate feedback (2026-10-03)

Candidate run `37121950806` did not pass. The supplied Linux test log reports
844 passes and one browser failure: immediately after saving a price listing,
Streamlit briefly retained both old and new navigation tabs, so the Exposure
locator was ambiguous. The test now waits for rerender completion and exactly one
Exposure tab before clicking, then checks navigation and absence of exceptions.
Both import browser tests passed locally, followed by three consecutive passing
two-test runs using synthetic data and the locked browser environment.

The Windows job exceeded the existing 45-minute limit, but its supplied test log
reached 100% before reporting errors. Two concrete portability issues were visible:
the dashboard fixture wrote Greek source text with the default CP1252 encoding,
and an oversized upload fixture generated a million-character test ID that exceeds
Windows' environment-variable limit. The fixture now uses explicit UTF-8 and
upload cases have short descriptive IDs, retaining the full oversized payload.

The three failure positions correspond to manual ETF lock contention, concurrent
ETF refresh and concurrent price-cache writes. Reviewing their shared lock helper
found a read of the reserved byte before attempting the Windows lock; a competing
lock can deny that read through another handle. Lock acquisition now directly
reserves the byte range, including empty files, as supported by Microsoft's
[_locking contract](https://learn.microsoft.com/en-us/cpp/c-runtime-library/reference/locking?view=msvc-170).
Regression coverage includes Windows contention without a pre-lock read and native
empty/existing lease contention plus release after an exception. The 48 focused
lock, ETF refresh/discovery, market-data and purchase-upload tests pass locally.
Native Windows confirmation still requires a new hosted run.

CI and candidate test commands now print individual test names and the slowest
durations. Time limits and required test coverage are unchanged. The Node.js
action-runtime warning is separate from the reported Linux test failure.

Final local validation: all 848 tests passed in the locked environment, including
all 28 required Chromium tests with no skips. Ruff, workflow YAML parsing, staged
privacy review and diff checks passed. No private workspace data was accessed.

Neither platform has an accepted distributable candidate from this run. A fresh
complete candidate run is required to verify these corrections on Windows; do not
publish or treat this run as release acceptance.

## Windows interpreter license lookup (2026-10-03)

The next supplied Windows log reached `tools/release.py build`, after the required
tests, and passed wheel/source content checks. Packaging then stopped while
generating third-party notices because its interpreter-license search omitted
the base installation's `LICENSE.txt`. Windows CPython installs that file next
to the executable, outside `Lib`.

The lookup now includes that base-interpreter location, retaining the existing
stdlib and extensionless-license locations. A missing interpreter license still
fails the build; the project's GPL license is never used as a substitute.
Synthetic tests reproduced the reported failure before the fix and cover all
three layouts plus missing-license rejection. All 93 release/privacy tests and
Ruff passed. Generating notices from the local installed environment also passed,
including the Python license and dependency inventory, using a temporary output
directory. No application UI or financial calculations changed.

The correction requires a fresh complete hosted candidate run. The Windows
packaged executable, extracted-bundle smoke and native installation acceptance
remain unverified; no release publication is approved.

## Live demo data and equity breakdown correction (2026-10-03)

The user reports that both hosted platform builds now pass, and has launched the
Windows candidate. Its run ID/checksum and remaining native acceptance steps
still need recording. The Windows browser could not be reached from this session;
checks below use a separate temporary source preview.

The reported large Other positions came from the offline demo's invented partial
snapshots (three constituents each). Normal startup and Explore demo now use the
ordinary public quote, history, fundamentals and ETF refresh paths. Demo ownership
and reset behavior remain separate from market-data mode. Nothing reads the
persistent portfolio to construct the demo, and provider downloads stay in its
temporary workspace rather than source or packaged assets.

Initial quantities are sized once from all six available quotes/FX, with invented
uneven values near the requested category allocations. Targets remain 60/25/10/5,
with World/EM 70/30 and BTC/ETH 60/40 within their categories. Invented buy-ins show
both gains and losses. Refreshes and workspace switches preserve quantities and
edits. Missing initial quotes show retry/status instead of synthetic prices;
subsequent requests retain the usual dated-cache behavior. Missing ETF downloads
leave funds whole. Explicit `--offline-demo` preserves deterministic synthetic
prices/history and partial snapshots for offline use and packaging smoke tests.

Live validation also found a small cash liability in the World issuer export.
Xtrackers now nets cash assets/liabilities into one unclassified cash pool when
needed, following the existing unsigned allocation semantics. It leaves equity
weights and total coverage unchanged, rejects net borrowing/negative equity, and
preserves signed substitute baskets. Synthetic regressions cover these rules.
No additional money-market breakdown implementation was added; existing support
can resolve it, otherwise the fund stays whole.

Validation:

- Full locked suite: 855 tests passed, including required Chromium suites without
  skips. Synthetic tests cover one-time sizing, missing FX, edit preservation,
  target/performance behavior, workspace isolation and the ordinary history path.
- Fresh source preview on a separately selected loopback port and temporary demo:
  Overview, Exposure (including chart), Positions with actual instrument history,
  and Rebalance/Targets rendered without application exceptions. World downloaded
  1,258 normalized rows with 99.999994% coverage; EM IMI downloaded 2,958 rows with
  100% coverage. The opened instrument chart had 251 actual daily observations.
  These are transient public-provider checks, not committed market-data fixtures.
- Source launcher/browser/lifecycle smoke passed with explicit offline demo mode,
  including import, manual ETF setup, restart and recovery checks. It keeps the
  candidate packaging checks independent of live network availability.

Next: build a fresh complete hosted candidate for these changes, then repeat the
Windows demo acceptance with a connection and confirm issuer snapshots/history
in that binary. The existing downloaded executable does not receive source
changes automatically. Official publication remains unapproved.


## Welcome, header and startup integration (2026-10-03)

The first-use screen is now a non-dismissable two-card welcome dialog: Explore
demo or Start my portfolio. The latter opens empty Positions; manual add and
FinanzManager-compatible import stay available there. Import is no longer a
welcome choice. The sidebar is replaced by a compact header with a workspace
dropdown, refresh, Settings and question-mark help. Display preferences and
App & workspace recovery controls live in Settings, including on input errors.

The supplied self-contained SVG animation is integrated before the welcome/app
in the browser. Its source asset and wrapper were copied into the package; the
original supplied files remain untouched. No other private data was copied.
It runs once per browser session, respects reduced motion and has a Skip button.
`--skip-intro` bypasses it for development. The animation workbench and direct
HTML preview support replay/timing edits without building an executable; see
[startup development](startup-development.md). Normal demo startup still uses
public market data; automated checks use explicit offline synthetic workspaces.

Windows packaging now provides a windowed `Portfolio Breakdown.exe` and retains
the console `portfolio-app.exe` companion. Shortcuts point to the GUI entry;
owned child processes are hidden and missing stdio is redirected to private
launcher logs before application imports. `uv run portfolio-desktop` uses the
same lifecycle from source. The candidate smoke check additionally launches the
Windows GUI entry and verifies its server in Chromium. Native no-flash behavior,
shortcuts and startup-error dialogs still need Windows candidate/manual acceptance.
The dedicated webview remains a separate optional task, not the default launcher.

Browser/package smoke exposed a stale Positions workflow after import completion.
A pending navigation request now applies when the Positions controls remount,
preserving the intended table or requested next step. Synthetic regression checks
cover stale form selection without changing saved positions or import semantics.

Parallel-session handoffs are in `documentation-session-handoff.md` (Default
mode) and `window-session-handoff.md` (Plan first). They pin the pre-integration
base so those sessions can work independently. Reconcile the final header and
launcher contract with their results before integration. Documentation publication,
a dedicated window and an official release are not part of this change.

Validation:

- All 863 tests verified across the full run and a focused rerun, including
  required Chromium suites with no skips. The full run reported 862 passed and
  one obsolete popover assertion already collected before its correction; the
  corrected six-test module and subsequent `pytest --lf` both passed. The new
  regressions cover intro completion/skip/reduced motion/replay, header workflows,
  invalid-input recovery controls, Windows command routing/stdio, and import
  workflow restoration. A single clean hosted run remains a candidate gate.
- Restarted source preview from this checkout with an isolated temporary workspace
  and `--offline-demo`. Chromium visited the actual intro, welcome, all four main
  tabs, help and Settings, plus a 390px layout. No application exceptions; charts
  rendered and the workspace dropdown preserved isolation. The separate animation
  workbench replayed successfully. Neither preview reads the private portfolio.
- Source launcher/browser/lifecycle smoke passed. A freshly built Linux frozen
  bundle passed the same animation/navigation, CSV/XLSX import, manual bond ETF
  breakdown, save/restart and recovery smoke. The upload smoke now waits for the
  file-triggered rerun before opening the asset-class dropdown.
- Wheel/sdist content checks passed; the wheel contains the intro HTML and desktop
  entrypoint. Ruff, diff checks and staged privacy review passed. No new runtime
  dependency or lockfile change is needed.

A new complete hosted candidate is still required. Verify `Portfolio Breakdown.exe`
on Windows, refresh existing shortcuts, and check repeat launch, missing/occupied
port errors and stop/restart with no terminal flash. This local Linux result is
not Windows or clean-machine acceptance. No official release is published or
approved by this change.

## Startup visual corrections (2026-10-03)

The embedded intro initially measured SVG letters while Streamlit still hid the
iframe, caching zero widths and collapsing the wordmark. It now waits for valid
rendered text metrics and measures again on replay. Reduced motion still skips
directly to the final mark without calculating uninitialized letter transforms.

Welcome cards used an unavailable CSS variable with a white fallback, leaving
white text on pale cards in dark mode. Their accent backgrounds now blend with
the actual dialog surface, retaining the user's selected light or dark theme.

Validation: 11 focused browser/onboarding tests passed, including initial embedded
letter spacing, replay, light/dark body-text contrast (at least 4.5:1), theme
persistence after reload, narrow layout, reduced motion and Skip. Restarted the
owned source preview on port 8513 with the same isolated synthetic workspace and
launch options; checked the actual welcome in dark mode and the animation
workbench on port 8512. No financial behavior or packaging contract changed.
The previous executable does not include these fixes until rebuilt.

## Guided setup, demo sizing and physical-asset entry (2026-10-03)

The visible Skip intro control is removed. Normal completion and reduced motion
advance automatically; a server-side eight-second timeout also advances when the
component cannot load. The development-only `--skip-intro` option remains.

The live demo now starts around EUR 93,184, with uneven invented position values
and mixed gains/losses. Quantities are sized once from public prices, then remain
stable through refreshes and edits. Category targets remain 60/25/10/5, with
70/30 equity and 60/40 crypto targets within their categories. The explicit
offline test fixture remains deterministic at EUR 100,000. The actual preview
was restarted in live mode and displayed EUR 93,184.32 after quantity rounding.

Start my portfolio now offers two steps: editable category names with optional
whole-portfolio targets, then first-position entry with category assignment.
Targets are opt-in; enabling them requires a complete total of 100%. Skipping
setup opens empty Positions; Finish later retains saved categories without
creating holdings. Existing allocations are reused. Category creation uses the
existing allocation format and document writer, checks holdings revisions under
the holdings lock, and refuses to replace an existing allocation. Category and
position saves are explicit separate actions. Within-category targets are still
optional and distinct from category targets.

Position dialogs now separate Holding and Valuation, with buy-ins, targets and
advanced details in optional sections. The empty Existing instrument selector
is omitted. Listed investments retain search and explicit listing identifiers.
Physical asset is a visible alternative, defaulting to a gold name with troy-ounce,
gram or unit quantities and dated manual pricing in the same unit. This exposes
existing valuation support; it adds no gold spot-feed dependency or ETF/futures
price proxy. Quantity can be saved without a price and remains unvalued. Changing
a new draft's unit clears amounts rather than converting them. Existing physical
positions keep their stored unit, including imported custom units. Position
details now display the unit beside the quantity. All examples remain invented.

Source preview on port 8513 was restarted from this checkout with the same
temporary empty workspace, using live demo mode. Browser verification covered
intro, categories/targets, listed and physical forms, cancellation, the new demo
value and all main tabs without application exceptions or persistent workspace
writes. Separate synthetic browser tests save and reload the guided gold example,
including category assignment and buy-in. Light and dark/narrow layouts remain
covered. Source launcher/navigation/lifecycle smoke passed, including CSV/XLSX
import and manual ETF setup. Native Windows acceptance still needs a fresh
candidate. No official publication is approved.

Final validation: all 879 tests were covered across the full run and focused
follow-up. The full run passed 877 tests and exposed a timing failure in an
existing metric keyboard-toggle test; that test now waits for the Streamlit
rerun to finish before dispatching the next key. Its six-test browser module and
eight onboarding UI tests passed together (14 passed), including an additional
regression proving that an older physical holding with no recorded unit does
not acquire an assumed ounce unit. Required browser suites ran without skips.
Ruff, diff checks and wheel/sdist content checks passed. A fresh native candidate
and one complete hosted run remain release gates.


## Category setup interaction revision (2026-10-04)

Replaced the prefilled multiline category field with an empty row-based form.
Examples (Equities, Bonds, Gold) are field help, not preset portfolio choices.
Each addition accepts a label and optional whole-portfolio target; Enter or Add
category appends the row and focuses the next blank name with a themed outline.
Added rows remain editable/removable. Complete targets totaling 100% open a
separate confirmation offering first-position entry or continued editing, without
saving automatically. Returning to edit preserves rows and suppresses repeated
confirmation of the same allocation. Existing allocation files are still reused.

The guide now permits blank and incomplete targets, matching the allocation
model's existing unknown-target semantics. It preserves supplied percentages,
rejects duplicate/blank names and totals above 100%, and never fills or normalizes
missing targets. Overview can show category values/current weights without target
gaps; planning still requires complete targets in the scope being calculated.
Skipping setup leaves ordinary holdings/exposure analysis available. No pricing,
aggregation or rebalancing calculation was changed. Physical gold still uses
manual per-unit prices; live spot pricing is a separate unimplemented integration.

Validation: 38 focused tests passed together, including the required startup
browser suite, onboarding persistence/UI, strategic calculations and performance
allocation. Coverage includes keyboard submission/focus, confirmation/editing,
optional/partial targets, duplicate/removal/skip, dark narrow layout and a saved
synthetic physical holding. Ruff and diff checks passed. This is targeted
validation, not a new complete release-suite or Windows candidate run.

Restarted the owned live source preview with its existing workspace and options;
verified that saved categories bypass setup, first-position cancellation and all
main tabs work without exceptions. Preserved the existing workspace unchanged.
A separate fresh temporary live preview verified blank setup, sequential focus,
confirmation/editing, narrow layout, skip/cancel and demo charts/main tabs. No
setup or position was saved during runtime inspection. Tests use only synthetic
temporary data. Release tooling and uv entrypoints are unchanged. A fresh hosted
candidate and native acceptance remain required; no publication is approved.


## Setup keyboard flow and physical gold spot pricing (2026-10-04)

Category entry now skips help buttons during Tab/Shift+Tab navigation. Pointer
help remains on the new row, with the same text exposed as input descriptions
for assistive technology; added category rows have no repeated tooltips. The
confirmation is titled All set? and uses a compact semantic table with aligned
percentages and escaped category labels. Continuing/editing retains the previous
save contract. Onboarding explicitly states that portfolio currency is EUR;
a selectable portfolio base currency remains deferred, while quotes and buy-ins
in other currencies continue to use the existing EUR conversion.

New physical holdings default to Gold spot price with troy oz, grams or kg of
fine gold. An explicit additive price_source=gold_spot field selects this mode;
the name does not infer the metal. The pure weight conversion uses exactly
31.1034768 grams per troy ounce. A separate Gold API adapter fetches XAU/USD spot
and its observation timestamp, while the existing market provider supplies FX.
No gold ETF/futures proxy, premium model, API key or dependency was added. The
normal 15-minute price cache, Refresh prices, background requests, timestamps,
missing-data behavior and dated cached fallback apply. Quotes are not streaming.

Existing manual holdings remain manual; users can explicitly change valuation
method in Edit position. Switching to spot clears the saved manual price. Stored
units remain fixed on edit; unsupported/unknown units cannot activate gold spot.
Changing new draft units clears quantity/costs, including switching a draft from
manual item counts to gold weight. Gold spot inputs reject exchange identifiers,
nonphysical instrument types and conflicting manual prices. Provider-specific
fetching remains separate from pure valuation and no aggregation rules changed.
Spot gold has no history chart yet. Requests transmit only the fixed XAU symbol,
not position names, weights, costs, categories or account/storage information.

Provider contract: https://gold-api.com/docs and https://gold-api.com/llms.txt.
Weight reference: https://www.royalmint.com/faqs/bullion/what-is-a-troy-ounce/.
A temporary, invented holding passed an actual spot-fetch → kilogram conversion
→ USD/EUR valuation check. Network availability is not required by unit/browser
tests; their prices and FX are injected synthetic fixtures.

Both owned source previews were restarted with their existing directories and
launch options. Read-only checks on the existing workspace verified saved-setup
reuse/cancellation and main tabs. The fresh preview verified keyboard navigation,
All set? table, narrow gold entry and kg selection, cancellation, live demo charts
and main tabs without saving holdings or categories. The original checkout and
private portfolio data were not modified. Wheel and source archive content checks passed; a fresh hosted candidate
and Windows acceptance are still required, with no publication approved.

Final validation: all 905 tests were covered across the full run and focused
follow-up. The full run passed 903 tests; one browser test had already loaded the
previous confirmation title when it was renamed, and another asserted while
Streamlit briefly retained old and new category rows during a rerun. The latter
now waits for the new row and completed rerun before asserting unique fields.
All 10 startup/gold browser tests passed together afterward, with required browser
suites enabled. Synthetic tests also cover equivalent weight units, FX conversion,
cache failures, invalid spot input, manual preservation, explicit method switching
and save/reload. Ruff, diff checks and staged privacy review passed. A single
complete hosted candidate run remains a release gate.

## Reconciled Windows window, documentation and installer candidate

Windows standalone shipping is approved and supersedes the prototype-only scope.
The isolated integration includes the complete window branch through
`1a3b26843eb6b24647b14d2bfaf19d5ba3b106a5` and documentation branch through
`0976779`, on top of release-v1 `7cafe2c`. The newer category guide, keyboard flow,
All set? confirmation, EUR policy and weight-based physical gold spot valuation
are retained. Futures branches and private portfolio files are excluded.

Windows desktop launch now enters the window; the console companion remains a
CLI/browser diagnostic entry. Production packaging includes pywebview and its
Windows dependencies, assets and matched Microsoft SDK notices. Setup uses
Inno Setup 6.5.4, installs per user, creates shortcuts and offers a signed
Microsoft Evergreen bootstrapper when WebView2 is missing. The portable bundle,
`--browser`, Linux browser launch and `uv run` development remain available.

Guide source stays beside app source for source-matched review; only generated
static documentation belongs on `gh-pages`. Publication remains manual and gated.
Candidate schema 2 adds the Windows Setup artifact and frozen documentation ZIP,
with checksums and documentation source identity. Installed help serves the exact
bundled public guide locally, independent of Pages availability. README is reduced
to product scope, limits and installation; data/calculation reference moved to
the guide. Main/PR documentation checks and human docs-impact review remain required.

The allocation palette follows the icon's blue, teal and purple sequence and
ranks categories by current value rather than category IDs. The full portfolio's
colors are reused while drilling into its categories.

Validation is in progress. Record the final commit/run/checksums and native results
below before candidate acceptance. Windows 10 with Python installed is available
for native packaging checks; Windows 11 acceptance on another machine remains a
separate user test. Neither this integration nor producing a candidate authorizes
an official release or Pages publication.

### Combined regression and native installation validation

The full native Windows regression run passed **977 tests, no skips**, including
required Chromium suites. Linux covered all 977 tests across the full run and
focused follow-up: the full run passed 976, and its ETF chart test passed after
waiting for the selected chart before opening the next dropdown. The 83 affected
privacy, exposure, refresh and position browser tests also passed together after
the Windows portability fixes. Browser fixtures now explicitly use en-US where
their assertions assume decimal points. Privacy tests stage a real Git symlink
entry and reuse the current uv environment without requiring OS symlink privileges;
the real hook still rejects force-staged synthetic data.

Both locally built, extracted packages passed browser navigation and lifecycle
checks. The Windows package additionally passed startup, all tabs/charts, actual
F11 input, exact borderless monitor/client bounds, repeated-instance focus,
bundled offline help and normal shutdown. The installed executable passed native
Open/Save dialogs, CSV import/save, an invented attachment download with checked
bytes, and an external link opened by the system browser. There is no in-app
export feature: the download check injects a same-origin synthetic attachment
through the test controller. These optional desktop checks can be repeated with
`uv run python tools/window_smoke.py --interactive "path/to/Portfolio Breakdown.exe"`.
They require an interactive Windows desktop and can briefly move keyboard focus.

Setup installed and reinstalled into a dedicated test directory, with the normal
and browser-fallback shortcuts verified. Uninstall removed the app and shortcuts
while preserving a separate invented workspace. Tests used temporary synthetic
workspaces only. The executable was launched outside the checkout with Python/uv
removed from PATH and PYTHONHOME/PYTHONPATH cleared. The host was Windows 10 with
Python installed: this does **not** establish clean-machine or Windows 11 acceptance.

The final test/tool fixes are committed before rebuilding both local candidates;
their manifests identify the exact source commit and matching documentation.
Local candidates are for validation, not public distribution: the established
clean hosted Windows/Linux candidate process and its privacy/content gates are
still required before publication. No GitHub credentials, push, workflow dispatch,
Pages publication or official release are part of this local validation step.
The next user check is Windows 11 Setup, Start Menu launch, display scaling and
normal interaction, then reinstall/uninstall with an invented workspace. Record
its results and exact Setup checksum in the release checklist.

The rebuilt-package smoke additionally exposed a test-controller selection issue:
typing a fund class into the combobox search was not evidence that its option had
been selected. The smoke now clicks the actual fixed_income option, checks the
closed selector, and waits for save completion before opening the summary. This
changes test synchronization only; the app and saved-data semantics are unchanged.

### Hosted candidate build corrections

Candidate run `37190781253` at `ce0dd55` passed all Windows tests, then failed
while collecting Inno Setup notices: PATH resolved Chocolatey's compiler shim,
which has no adjacent installer license. Compiler discovery now requires a real
compiler with its adjacent license, checks both standard installation locations,
and preserves an explicit `PORTFOLIO_ISCC` override without silently substituting
another installation. Synthetic regression tests cover shims, actual PATH
installations, explicit overrides and missing licenses.

The Linux job failed one onboarding browser check while Streamlit briefly kept
the previous and replacement Save buttons. The check now waits for the completed
rerun and a unique Save control. The separate main CI dialog-close and category
roundtrip fixes are also included; these retain all existing behavior assertions.
No application behavior or portfolio semantics change.

The corrected candidate must complete the existing hosted build, packaged smoke
and Linux compatibility gates. Windows 11 installation acceptance remains a
separate manual gate, and no official release or Pages publication is authorized.

Follow-up run `37192870139` passed all 982 Linux tests, Linux package checks and
Windows installer creation. Its Windows packaged browser smoke lost the nested
fund option list during pointer selection. Slowed Chromium reproduced the issue
on Linux and the frozen Windows app; DOM diagnostics showed the server was idle
when the list closed. The smoke now waits for the completed Exposure view,
selects directly from the fixture's four-item list without filtering/resizing
the nested popup, and checks both the committed selection and bound synthetic
ISIN before uploading. This passed with 4× Chromium CPU slowdown against main
source, release source and the existing frozen Windows executable. The
superseded candidate run `37194260306` was canceled during this investigation.
Normal-speed isolation then identified the remaining trigger: nested expanders
were still animating and scrolling when the selector opened, and React Aria
closed its list on that ancestor scroll. A focused synthetic probe failed all
eight attempts before waiting for finite animations and delivered scroll events,
then passed all eight afterward. The package smoke now uses that settling step
before both setup selectors. The same wait protects the exposure dialog's Close
button; run `37194767789` had reproduced that failure after server-idle checks
alone. Five repeated settled dialog-close checks passed, as did source smoke and
the frozen Windows smoke with 4× CPU slowdown. No forced clicks, fixed sleeps or
automatic test retries are introduced.

The separate main CI repair also backports the already-present release fix for
post-import navigation: stale browser state must not restore the import form on
returning to Positions. Its new synthetic regression fails before the backport
and passes afterward, including when run against release source. The release
application and frozen executable code are unchanged by these CI corrections.

Run `37195647864` passed both complete regression suites, Linux packaging/smoke
and Windows packaging/browser smoke. Native Windows smoke then attached to CDP
before the window navigation arrived: its `time.sleep` loop polled cached page
URLs without letting Playwright dispatch navigation events. It now waits for the
page/navigation through Playwright. A delayed synthetic navigation test fails
with the original loop and passes with the correction. The updated controller
also passed locally against the frozen Windows executable, including real F11,
monitor edges, repeat launch, bundled help and shutdown. This remains a test-tool
correction; fresh hosted validation is required for the final candidate.

Run `37196886950` exposed an independent exposure-chart test race: the Detail
view selector had updated while Plotly still displayed its previous root. The
test now waits for the expected chart root on both drill-down and return before
continuing. It retains the original chart, selector and exception assertions.

The Windows job in that run passed all 983 tests, installer creation, packaged
browser smoke and native startup/F11/monitor-edge/relaunch/help/shutdown checks.
Run `37197766978` subsequently exposed another dialog replacement race in the
stale-edit test: the reloaded quantity arrived before the warning form's Cancel
control was replaced. The test now waits for the old reload control to disappear
and the rerun to finish before canceling; stale-write protection and dismissal
assertions remain unchanged. The six UX browser tests and a deliberately delayed
synthetic form passed. Main CI run `37197753233` passed all jobs; the additional
reload synchronization is also being carried into its repair PR.

### Verified hosted candidate — 2026-10-04

[Candidate run 37198442048](https://github.com/eliaskempf/portfolio-breakdown/actions/runs/37198442048)
passed every job at source commit `9d52cf84cb4d5d0f216eea92d18ad39edecb57f0`:

- Windows 2022 and Ubuntu 22.04: 983 tests each, with no failures or skips.
- Both platforms: source/package privacy checks, build and installed browser,
  navigation and lifecycle smoke checks.
- Windows: installer creation and native startup, charts/tabs, actual F11,
  monitor edges, repeated launch, bundled help and shutdown checks.
- Ubuntu 24.04: the downloaded Linux candidate passed compatibility smoke.

Use the `candidate-windows-x64` artifact from this exact run for Windows 11
installation acceptance. Its Setup executable is the installer; the portable
ZIP is an alternative. Verify the installer checksum below and record the manual
machine's results when checking Setup, Start Menu launch, scaling, file dialogs, external
links, reinstall and uninstall. Hosted checks do not establish clean-machine
Windows 11 acceptance. No official release or Pages publication has occurred.

Downloaded Windows artifact checksums match `SHA256SUMS`. The installer is
`portfolio-breakdown-0.1.0-windows-x64-setup.exe`, SHA-256
`bbd7dede49e30705f991a25a9e015f3be203ba5a60ee918832e3b440ef6f6cd5`.

The separate main repair is [PR #3](https://github.com/eliaskempf/portfolio-breakdown/pull/3),
still unmerged. [CI run 37198430094](https://github.com/eliaskempf/portfolio-breakdown/actions/runs/37198430094)
passed every job at `5db6e4480cde4d83a1fd908b79dace46ecf5a62a`.
This handoff entry is documentation-only and is newer than the candidate's
recorded source commit; it does not change its binaries.

### Welcome-screen shutdown correction

The installer was reported to work, but closing before selecting a workspace
briefly displayed a native error dialog. A synthetic native Windows probe
reproduced it in three of three launches, despite exit code zero. The supervisor
requested `destroy()` after WinForms had started closing but before its closed
callback, re-entering pywebview's close handler (`KeyError: 'master'`).

The presentation now marks closing before signaling the supervisor, which leaves
an in-progress native close alone. A regression test fails with the original code
and passes with the correction; all 56 window/launcher tests pass. The packaged
native smoke now closes at the welcome screen and watches for transient dialogs,
also checking shutdown after entering the demo. That stronger smoke detects the
error in the previous frozen executable. A rebuilt candidate must pass it before
this correction is considered packaged and ready for another installation test.

Validated in [candidate run 37203297546](https://github.com/eliaskempf/portfolio-breakdown/actions/runs/37203297546)
at `c4e2399ee4f046daae4af7967c8cbf9428ed067f`: all jobs passed, including
984 tests on each platform, packaged browser checks, both native shutdown paths,
F11/monitor edges/relaunch/help and Ubuntu 24.04 compatibility. The corrected
source also passed three native Windows welcome-screen closes with no error
dialogs; the 19 startup/browser checks passed locally. All native probes used
new synthetic workspaces, and their previews were stopped.

Use this run's [Windows artifact](https://github.com/eliaskempf/portfolio-breakdown/actions/runs/37203297546/artifacts/11304455883)
for the next installation test, replacing the earlier candidate. Check its own
`SHA256SUMS` and manifest when identifying the Setup executable. Windows 11
retesting remains a user acceptance step; no official release was published.

## ETF discovery and breakdown fixes

Prepared on `fix/v1-etf-breakdown` from release-v1 commit `f2db5ab`.
The shared release checkout and other sessions' previews were not modified.

### Changes

- Search and discovery reuse exchange-verified Xetra aliases for DAX
  (`DE0005933931`, `EXS1.DE`) and Global Aggregate Bond EUR Hedged
  (`IE00BDBRDM35`, `EUNA.DE`). Issuer identity is still verified before importing.
- The bond fund retains strict full-export validation. A reconciliation failure
  can produce a labeled top-ten bond snapshot at published whole-fund weights,
  with the remaining allocation in Other. Malformed data remains an error.
  Missing security ISINs retain stable provider-specific identities.
- Amundi Smart Overnight Return (`LU1190417599`) has an exact-product adapter
  using its public product API. The snapshot represents EUR overnight-rate
  exposure; the partial substitute basket is separately stored and displayed.
  Xtrackers' distinct overnight benchmark remains unchanged.
- The internal source/downloader supports optional JSON POST requests. Existing
  GET integrations and snapshot storage remain compatible; no migration or new
  dependency is required.

### Verification

- Broad pytest run excluding the dedicated ETF browser file: 1,009 passed.
- After the final parser adjustments: 99 relevant calculation, discovery and
  refresh tests passed. These include missing-ISIN handling and 29 new regression
  cases using invented compositions and public fund identifiers.
- All five ETF browser scenarios passed: bond summary/navigation, economic
  benchmark and separate basket, settings access, partial coverage/Other, and
  DAX ISIN search filling the Xetra identity. The search scenario was rerun after
  correcting its test selector to the existing `ISIN (optional)` field label.
- Read-only live provider probes succeeded for all three fund identifiers;
  no provider downloads were saved or committed as fixtures.
- The task's synthetic preview was restarted after module changes and checked in
  Chromium: DAX Holdings default, bond Summary chart and partial coverage,
  Amundi economic Summary and separate basket, setup controls, no app exceptions.
- Ruff, diff whitespace checks, privacy checks, and wheel/source archive content
  checks passed. Tests and previews used temporary synthetic workspaces.

### Release integration

Integrate the focused fix branch into the current release branch without resetting
other sessions' changes. Existing saved snapshots and positions remain intact;
use **Refresh ETF holdings now** to retry previously unavailable breakdowns.

A fresh hosted Windows/Linux candidate build is required. Native Windows and
installer acceptance have not been performed for this change. Neither source
tests nor Linux browser checks establish that acceptance; no installer or release
was published by this task.

## Additional ETF provider adapters (2026-10-04)

Continues the isolated `fix/v1-etf-breakdown` worktree at
`/tmp/portfolio-etf-v1-fixes`, after original regression fix `cacd96b`.
The requested additional providers are Amundi, Vanguard and State Street/SPDR.
The shared release-v1 checkout and its other sessions were not modified.

- Added exact-ISIN discovery through Amundi's product API, Vanguard's official
  sitemap/GPX catalogue, and State Street's European fund finder. Product-page
  setup and subsequent refresh use the same provider adapters; no individual
  ETF registration is required for these supported formats.
- Amundi physical equity/bond compositions preserve whole-fund weights. Cash
  borrowing or unsupported security types yield labeled top holdings plus Other.
  The reviewed overnight economics and legacy same-index proxy remain separate.
- Vanguard retrieves every holdings page, checking share-class identity, total
  row counts, dates and repeated cursors before publication. Matching ISIN lots
  combine; conflicting source geography remains unspecified. Derivatives/rights
  are excluded, and short securities remain unsupported.
- State Street validates the product page's replication and asset class, the
  official daily XLSX link, and the workbook's ISIN/date/header. Cash placeholders
  do not collapse different currencies into one security. Trading-country labels
  are not treated as company geography.
- A small excess in published weights yields explicitly partial top holdings,
  never a rescaled full allocation. Malformed exports fail without replacing a
  valid snapshot. New adapters use existing dependencies and aggregation models.

Validation: 998 non-browser tests and all five ETF Chromium scenarios passed;
139 focused ETF tests passed. Provider tests generate invented JSON, HTML and
XLSX payloads entirely offline. Read-only live checks succeeded for physical
Amundi equity/government bonds, Vanguard All-World/Eurozone government bonds,
and SPDR S&P 500/Euro government bonds. Tested Amundi equity and SPDR exports
required partial coverage; Vanguard Global Aggregate's short mortgage positions
were correctly rejected rather than silently converted to unsigned exposure.

Ruff, documentation build, wheel/sdist build and archive content checks passed.
The owned preview at `http://127.0.0.1:60639` was restarted using the same synthetic
workspace `/tmp/portfolio-etf-v1-preview` and offline launch options. Actual
browser navigation verified provider setup, the partial bond chart and overnight
summary without application exceptions. No private portfolio was used. A native
Windows installer has not been built or verified for these commits.

### ETF/search integration into release-v1 (2026-10-04)

Integrated the complete `fix/v1-etf-breakdown` branch through
`56c41034fec6c1e154a3326032e8498585e77502`, including
`cacd96b6e88eb3b3db16044febb7e3311f661f5c`, from release base
`f2db5ab1cf81a5c45e43e5f4d5c5fad58c97fc15`. This is a conflict-free
fast-forward preserving the current guided setup, gold valuation, Windows
installer and welcome-screen shutdown fix. The experimental Linux/macOS desktop
branch is not part of this integration. Dependencies and release tooling are
unchanged.

Validation in the isolated `/tmp/portfolio-v1-etf-integration` worktree on
`integrate/v1-etf-fixes`:

- `uv run pytest -q` with required Chromium suites: **1,047 passed**, no skips.
  Browser fixtures launch this checkout on temporary loopback ports with
  synthetic workspaces, including ISIN search, partial bond summaries and Other,
  overnight economic exposure and its separate substitute basket, onboarding,
  imports and navigation. No private portfolio or another session's preview was
  used.
- Ruff and tracked-file privacy checks passed.
- Strict documentation build, all 30 help-topic checks, and the documentation
  browser smoke passed (navigation, search and narrow layouts). The temporary
  docs preview at `http://127.0.0.1:39665/portfolio-breakdown/dev/` was stopped
  after verification.
- Wheel and source archive builds and release content checks passed.

Next validation: build a fresh hosted candidate from the integrated release
commit. Previous Windows installer results do not validate these new adapters.
Record its run and packaged smoke results before treating the new installer as
verified. Existing portfolios can use **Refresh ETF holdings now** to retry
previously unavailable breakdowns. No official release or tag is authorized by
this integration.

### Optional Linux/macOS desktop integration (2026-10-05)

Merged PR #4, `experiment/linux-macos-desktop` through
`4b90f91ba2d8c3df657eef0830395d93075aac1a`, into the release based on
`dbaca0172ea0dd9599bb43da727b961d453fc609`. Integration used the isolated
`/tmp/portfolio-v1-desktop-integration` worktree and
`integrate/v1-desktop-experiment` branch. The merge was conflict-free and retains
the ETF/search adapters, setup, gold valuation and Windows shutdown correction.

The Qt Linux `.deb` and Cocoa Apple Silicon macOS DMG remain optional experiments.
Their workflow, dependency extra and acceptance are separate from supported
Windows-window/Linux-browser candidates and official publishing. Developer ID
signing/notarization is not planned for v1; ad-hoc signing does not establish
Gatekeeper trust. Historical native evidence and remaining gaps are documented
in `docs/desktop-experiment.md`; earlier experimental installers are not final
integrated v1 artifacts.

Integration review found that the existing required-browser gate would reject
the new POSIX-only test skips on Windows. It now rejects skipped browser tests
and modules while allowing platform-specific unit skips. Five synthetic gate
regressions cover this distinction and missing-browser/empty-suite failures;
the Windows-skip case failed before the correction and passes afterward.

The combined full suite recorded **1,069 passed and one browser timing failure**
in category-to-Performance navigation. That unchanged browser file passed all
six tests on rerun. Review found that the category value arrives before the
view switch and scoped chart finish rendering; the test now waits for the scoped
chart and completed rerun before clicking Performance. No application behavior,
assertions or timeouts were weakened. After this test-only correction, all six
UX browser tests and all five new CI-gate regressions passed together (**11
passed**). The full suite was not repeated after these test-harness changes.

Local verification uses only synthetic workspaces; no other session's preview
or private portfolio is used. Ruff, privacy, strict documentation and all help
links, wheel/source archive builds and release content checks pass. The revised
shared package smoke passes against source, including lifecycle and ETF setup.
The documentation browser smoke passes navigation, search and narrow layout at
the temporary `http://127.0.0.1:41817/portfolio-breakdown/dev/` preview, stopped
after verification. These source checks do not establish native acceptance of
new packaged installers.

The earlier ETF/search candidate [37232831332](https://github.com/eliaskempf/portfolio-breakdown/actions/runs/37232831332)
at `dbaca01` passed Windows, Ubuntu 22.04 and Ubuntu 24.04 compatibility. It
predates this desktop integration. GitHub's latest PR #4 job annotations confirm
that jobs could not start because of account payments/spending limits; this is
not an application test result. Fresh supported candidate and optional desktop
builds/native checks remain pending until hosted execution is available.
No old installer is promoted, and no tag, official release or Pages publication
is authorized by this merge.

## Demo breakdown and allocation correction (2026-10-05)

Both public demo modes now share invented initial values around EUR 93,184.35,
with equity/money-market gaps of approximately +5.37/-3.06 percentage points
and uneven allocations within equity and crypto. Targets and one-time live
quote sizing remain unchanged. The offline demo has 19 named equity constituents
across sectors and regions, 1–1.5% residual fund weights, and XEON overnight-rate
economic exposure. All example quantities, costs and fund weights are invented.

Themes & sectors prefers populated sector metadata when curated labels are absent,
including after an initial live download; explicit selections remain unchanged.
Geography separates verified overnight-rate exposure as Money market and never
uses the substitute basket's countries. The registered Emerging Markets source
now uses the existing iShares JSON parser: the older XML document lacks country
metadata. First-download failures surface a direction to the retry controls.

The geography mapper also accepts the iShares country label “Korea (South)”.
Read-only live checks successfully resolved World, Emerging Markets and XEON
with issuer snapshots dated 2026-10-02. Browser checks of separately launched
live and offline synthetic portfolios covered assets, sector charts, geography
and drill-down, ETF toggling, and Rebalance navigation. No provider exports or
runtime portfolios are included in source. New synthetic regressions cover
initial download failures/retry, country metadata on install/refresh, residual
conservation, geography coverage and delayed classification defaults.

Validation: 1,023 non-browser tests passed (one skipped); the new demo browser
regression, existing geography/ETF browser checks and onboarding browser check
passed. Ruff, diff checks and the staged privacy check passed. Installer binaries
were not rebuilt as part of this source change.

### Building candidates locally while Actions is unavailable

GitHub Actions is an orchestration option, not a packaging dependency. Use the
existing build scripts on clean, isolated clones of the same exact commit, with
the committed `uv.lock`. Do not build from a personal working checkout or copy
its `data/`. Record the commit, OS, Python and uv versions, test results and
artifact checksums. Local supported candidates identify their run as `local`;
do not forge a GitHub run ID or treat older hosted artifacts as these builds.

For Windows x64, use native Windows Python 3.12 and Inno Setup 6.5.4 (the existing
candidate compiler version). Run from PowerShell in the isolated clone; use a
fresh clone/output directory for each candidate. Git, uv and Git's `sh.exe` must
be on this process's PATH; a minimal Git installation's `cmd` directory alone
does not expose the shell needed to verify the real privacy hook. Add its
`usr/bin` directory for the build process as needed:

```powershell
git config core.autocrlf false
git config core.hooksPath .githooks
Get-Command git, sh, uv
# Set this to the actual compiler installation with its adjacent license.txt.
$env:PORTFOLIO_ISCC = 'C:\build-tools\Inno Setup 6\ISCC.exe'
$env:PORTFOLIO_REQUIRE_BROWSER = '1'
uv sync --locked --all-groups --python 3.12
uv run portfolio-check-private --tracked
uv run ruff check src tests tools
uv run playwright install chromium
uv run pytest -q --junitxml=dist/test-results.xml
uv run python tools/release.py build
uv run python tools/release.py test
```

Check every command's exit code and stop on failure; PowerShell does not normally
stop for a nonzero native exit code. The build downloads and verifies the
Microsoft WebView2 prerequisite and matching license notices. It does not install
or upgrade the application on the build host. The test step extracts the bundle
outside the checkout and exercises synthetic browser/native workflows. A clean
Windows 11 install/upgrade check remains separate from building on a development
machine with Python installed.

For supported Linux x64 browser bundles, run the equivalent commands in Ubuntu
22.04, installing Chromium's required OS libraries. Test the same resulting bytes
on Ubuntu 24.04 as well:

```sh
uv sync --locked --all-groups --python 3.12
uv run portfolio-check-private --tracked
uv run ruff check src tests tools
uv run playwright install --with-deps chromium
PORTFOLIO_REQUIRE_BROWSER=1 uv run pytest -q --junitxml=dist/test-results.xml
uv run python tools/release.py build
uv run python tools/release.py test
```

Use a dedicated Ubuntu VM/container or the isolated userspace procedure in
`docs/desktop-experiment.md`; do not change the main development host's libraries
to emulate a target. Build/test temporary files can live in a memory-backed
namespace while completed artifacts and logs go to a larger disk. Read-only
base images, independent scratch directories and synthetic workspaces keep
other sessions and private portfolio data outside the build.

Supported outputs are in `dist/candidate`: Windows Setup EXE, portable platform
archive, corresponding source/wheel, frozen docs, notices, dependency inventory,
manifest and SHA256SUMS. Keep each platform's directory intact. The Windows Setup
EXE can be transferred directly to the tester without wrapping it in another ZIP.
For an experimental Linux `.deb`, add the `window` extra and follow
`tools/desktop_build.py` plus native X11/Wayland and install/removal checks from
`docs/desktop-experiment.md`; those results remain separate from supported
release acceptance.

macOS installers are deferred: no Apple Silicon Mac is available for a fresh
native build and verification. Existing Mac artifacts must not be renamed or
represented as the current release. Paid signing/notarization remains out of
scope.

Local builds do not authorize publication. The current automatic promotion
script specifically requires a successful Actions candidate from the default
branch. When an official release is approved, either restore that workflow or
review an explicit local-artifact publication path that preserves exact source,
lock, docs and checksum verification and publishes the accepted bytes without
rebuilding. Do not weaken the existing hosted-promotion checks merely to obtain
a local installer.

#### Local build evidence, 2026-10-05

Built clean source `a39cf4f1de709ba14eacb4895d98eedba41db57b` with uv 0.12.10
and Python 3.12.14. Windows and Linux supported manifests both record `run_id:
local`, `source_clean: true`, and dependency lock SHA256
`cc8c50abfd04e79e1b04d13a37ac44ce318105ec1a24e4b0e9ffe7ebd13d8ac1`.
Their frozen documentation identities match. These are test candidates, not a
final v1 release; later source changes require new builds.
While these builds were running, another session advanced `release-v1` with
demo/ETF/geography fixes at `d31305769bc8fe3a643cbd92f8d99319133f860e`.
Those newer changes are not included in the `a39cf4f` artifacts. Rebuild the
chosen final source after remaining integrations instead of relabeling these
proof-of-process candidates as the latest release.

- Ubuntu 22.04 full suite: **1,075 passed**. Supported browser bundle passed
  extracted navigation/lifecycle checks on both Ubuntu 22.04 and 24.04.
- Windows full suite initially had **1,071 passed, three expected POSIX skips**
  and one privacy-hook failure because the local process PATH omitted the
  existing Git shell. After adding Git's `usr/bin`, all **1,023 unit tests passed**
  with the same three expected skips. Application source was unchanged.
- Windows Setup/portable builds, content/privacy and license checks passed.
  Extracted browser workflows and native welcome-screen close passed. The full
  native smoke did **not** pass: the F11 control failed to transition on one run;
  a diagnostic run on a fresh extraction could not acquire foreground focus.
  This does not establish whether F11 works reliably for the user. Manual
  Windows 11 installation, F11/monitor edges, upload/save dialogs, external links,
  repeated launch and shutdown remain pending. The local host has Python
  installed; the launched frozen process had Python/uv removed from PATH.
- Experimental Linux payload passed native X11 checks on Ubuntu 22.04. The same
  `.deb` was checksum-verified, extracted and tested on Ubuntu 24.04 under both
  X11 and actual Wayland: rendering/navigation, native upload/download bytes,
  focus/restore with preserved input, repeated launch and shutdown passed.
  All nine desktop/welcome/early-close reports passed. Package-manager
  installation/removal was not repeated for this build. Virtual desktops under
  the WSL kernel do not establish physical-desktop acceptance.
- Build workspaces and tests were synthetic and isolated. Ubuntu images were
  mounted read-only, temporary Linux work lived in memory, and completed files
  went to a separate disk. No other session's checkout, preview or portfolio was
  used. macOS remains deferred.

Delivered a `Portfolio-Breakdown-local-a39cf4f` folder with separate platform
directories, source/notices/checksums and `local-validation.json`. Every copied
artifact is checked against its supplied checksum list. Key SHA256 values:

| Artifact | SHA256 |
| --- | --- |
| Windows Setup EXE | `00c65936550a6430a02ac17c18152cf3b27acf910c7ac8dccd2dd94c5d61edac` |
| Linux browser archive | `359cd366a550df3d8d952b12a755af2128b4d8af5cbf09a666e2cde703cc8da5` |
| Experimental Linux `.deb` | `ee4d3cacdfcd9248fe06e8eb2fea95882abbcbfd93cf07d6ea5850f687921e62` |

Do not promote these files automatically or mark the remaining Windows native
checks complete. The setup executable is ready to transfer for manual testing,
without an additional ZIP wrapper.

## Optional guided tour integration (2026-10-06)

Integrated the complete `feature/quick-app-tour` branch through
`04821372b151b502628037c5c36aed4d84673a39`, based on release target
`cb7d348e9ab9270b9d9f523cc8e8146c76847e69`. The isolated integration branch
is `integrate/v1-quick-tour`. Newer demo/ETF corrections, gold valuation,
guided setup and native installer support are retained. Pending currency and
UI-polish branches are not included in this integration.

The optional 15-step tour uses a separate temporary synthetic portfolio, with
invented prices/history and automatic risk/contribution examples. It restores
the original workspace and view on Finish, Skip tour or Escape; dismissal is
remembered and Help offers replay. This is separate from the startup animation.
There are no dependency or lockfile changes. Getting started documents the tour
and the Overview landing after manual setup.

Integration also updates the existing demo regression and package/Windows smoke
scripts to dismiss the new invitation and explicitly open Positions after
manual setup. This prevents the welcome dialog from blocking their unrelated
navigation/import checks. Dedicated tour tests retain full walkthrough coverage.

Validation on the combined source: all **1,046 non-browser tests passed**.
With the locally installed Chromium path configured, the full browser run had
**54 passed and one failed**: the newer demo test had not dismissed the tour
invitation. After adding that explicit click, the affected test passed, giving
passing coverage of all **55 browser tests**, without skips. The application
source was unchanged by this test correction. All five dedicated tour browser
cases also passed independently. Ruff, staged privacy/diff review, documentation
build/link checks and wheel/source content checks passed. The adjusted package
smoke passed against the source-installed app, including import/save/restart,
ETF setup and lifecycle checks; this is not frozen-executable acceptance.

The integration preview uses checkout `/tmp/portfolio-v1-tour-integration`,
its own Python environment, synthetic workspaces under
`/tmp/portfolio-v1-tour-preview`, and `http://127.0.0.1:60917`. Browser verification
covered the highlighted allocation chart, Escape restoration and all main tabs
without application exceptions; a synthetic screenshot was visually reviewed.

Native Windows WebView2 tour acceptance remains pending on the final candidate,
including both themes, chart interaction, skipping/replay and restoration.
The release checklist now records this explicitly. No installer was rebuilt
for this merge; older local artifacts do not contain the tour. Hosted CI remains
unavailable, macOS remains deferred, and nothing is tagged or published.

## Reporting currency integration (2026-10-06)

Integrated the complete `feature/v1-portfolio-currency` history through
`8862e4fc9553180f22b215c86c39367a431a3439` into the tour-bearing release base
`b90a038d712a41a313b9958813d66d2515748c94`, using the isolated branch
`integrate/v1-currency`. This includes the original currency implementation,
fractional-quantity persistence correction, live-demo cost currency fix and final
guide correction; it is not a cherry-pick of the documentation-only tip.

EUR/USD/GBP selection, current reporting valuations, historical/supplied purchase
conversions and explicitly confirmed estimates are retained. Original costs stay
in their recorded currencies. Missing purchase conversions exclude gains while
available current values continue to count. Legacy workspaces default to EUR;
`portfolio.yaml` and cost-component metadata remain private workspace files.

Reconciled the shared overview, analytics, settings and rendering changes with
the existing tour, keeping its spotlights and synthetic EUR workspace. Combined
regressions cover USD/GBP portfolio and saved-plan restoration on leaving the
tour. Settings and tour share the same popover state key. Also corrected a stale
Settings selector after saving USD/GBP during setup; the regression failed before
the fix. The README now lists the supported reporting currencies, and the guide
retains the tour's Overview landing after setup.

Subsequent UI work must use the `*_reporting` calculation fields and currency-neutral
table keys. Keep portfolio currency explicit in calculations and format display
labels from the selected currency. Do not reintroduce EUR-specific field names
when reconciling the pending UI-polish branch.

The fresh preview uses `/tmp/portfolio-v1-currency-integration`, its own Python
environment and synthetic workspaces in `/tmp/portfolio-v1-currency-integration-preview`,
served at `http://127.0.0.1:60918`. Browser inspection verified GBP valuation/chart
labels and risk, all main tabs, EUR tour entry, Escape restoration and persisted
currency after reload/reselection. Synthetic screenshot inspection passed.

No dependency, lockfile or production packaging-script changes were needed.
Validation: the full combined suite passed **1,150 tests without skips**. Two
additional browser cases covering USD/GBP tour and saved-plan restoration were
added during that run; both passed alongside the existing currency browser test.
The focused currency/tour calculation and UI suite passed **61 tests**. Ruff,
staged privacy and manual diff review, documentation build/link checks, and
wheel/source archive content checks passed. The original currency and release
worktrees were clean before integration; private portfolio data was not used.
Source-installed lifecycle/import/ETF smoke checks passed; these do not establish
frozen-executable acceptance. Installer rebuilds are explicitly deferred until
all approved branches are integrated. Final Windows/native currency and tour
checks remain on the release checklist. Nothing is tagged or published.

## V1 position-entry and contextual-help polish (2026-10-05)

Prepared on `feature/v1-ui-polish`, based on release-v1 `dbaca01`.

- Position entry keeps quantity, linked average/total purchase cost, category and
  target visible. Compact decimal inputs preserve unchanged saved precision;
  the last edited cost remains authoritative when quantity changes. Account and
  manual pricing move into More details, except essential physical-asset valuation.
- Search uses explicit selection actions and verified-ISIN listing groups, removes
  interest-specific suggestion chips, and retains manual entry and existing
  instrument reuse. Reusing an existing identity retains the new position draft.
- Optional purchase rows calculate a new holding through existing purchase
  validation and atomic persistence. Manual drafts and table drafts survive mode
  changes and dismissal. Sales accounting remains excluded.
- Native control help and shared table-header help explain actions, units and
  percentage scopes. Portfolio settings is distinct from Streamlit appearance;
  launcher configuration hides developer menu controls while retaining themes.
- Updated the positions/getting-started/reference guide and candidate checklist.
  Holdings and purchase-history formats and dependencies are unchanged.

Automated validation used temporary invented workspaces and offline market inputs.
The complete 1,070-test suite ran with browser and documentation dependencies:
1,068 passed; two browser assertions still expected the previous manual-entry
visibility and absence of category help. Those assertions were updated to the
intended controls, including real tooltip visibility, and both passed on rerun.
The final focused 59-test run covered position entry, search, precision, purchase
rows, draft recovery and the updated welcome flow; the category keyboard/help
regression passed separately. All 1,070 cases are covered across these runs, with
no skipped tests. Ruff correctness checks and the changed/untracked-file privacy
scan passed; the staged index was not modified.

The source application's browser/navigation/lifecycle smoke check passed,
including import, save/restart and workspace recovery. The isolated preview was
restarted after Python changes and checked in Chromium across Overview, Exposure,
Positions and Rebalance, Light/Dark themes, linked cost entry and narrow layout,
without application exceptions. No personal portfolio was read or changed.

These source changes require a new candidate build and normal native-platform
acceptance before release. No candidate, tag or release was published here.


Follow-up UI review simplified identity to one row: an editable name and the
read-only price ticker selected by search. Optional ISIN metadata appears only
under More details; missing ISIN does not prevent ticker-based pricing. Explicit
manual entry still permits typing a ticker. Listing detail enrichment accepts
checksum-valid ISINs from exact-ticker metadata before optional lookup, and
selected listing fields survive reruns and draft resumption.

The buy-in currency selector is beside quantity and offers only EUR for new
positions until acquisition FX support is implemented. Saved foreign costs remain
in their original currency with a locked selector; unlabelled legacy costs stay
unlabelled unless explicitly assigned EUR. No conversion or relabelling occurs.
Full reporting-currency and historical acquisition FX support remains separate.

The identity/currency browser checks passed, covering a single editable name,
locked ticker, retained ISIN, EUR-only options, linked costs and narrow layouts.
The restarted online synthetic preview passed live Apple search and the same
form checks without saving a position or accessing personal data. Automated
regressions use synthetic workspaces and offline inputs. Test fixtures now use
the explicit manual-entry action when adding a holding without search.

Field-help spacing now keeps Streamlit’s native help button 6 px beside its
label instead of stretching to the input’s far edge. Three form browser tests
passed. The restarted online preview was verified at 1440, 620 and 390 px with
6 px gaps, mouse hover, keyboard focus and touch activation; column-heading
hover/focus help also passed. Public demo quotes required one refresh retry.

Pre-merge review (2026-10-05): corrected copied help text for sell protection,
immediate category assignment, temporary caps and minimum purchases, and removed
the getting-started guide’s incorrect claim that foreign purchase costs convert.
The 1,076-case regression run had 1,073 passes and three legacy manual-entry test
failures. Those tests now explicitly choose Enter manually; all three passed on
rerun, and the relevant 26-test UI/startup suite passed. No tests were skipped.
Documentation checks passed (11 tests), as did lint, diff whitespace checks and
the changed-file privacy scan. The installed source application’s browser,
navigation, import/save/restart and lifecycle smoke passed. The online preview
was restarted and checked across all main tabs and corrected category help.

The feature worktree is based on dbaca01. As of the 2026-10-06 merge handoff,
release-v1 has advanced to cb7d348 with desktop packaging and demo fixes; those
changes require integration checks when merging this branch. Currency support is
a separate feature. A new candidate build and native-platform acceptance remain
release gates, not prerequisites for this source merge.

### UI integration handoff (2026-10-06)

Integrate the complete `feature/v1-ui-polish` branch from the isolated worktree
`/tmp/portfolio-v1-ui-polish`, including the geography follow-up after `4a0f9af`.
That follow-up keeps the controls together, puts interpretation in tooltips and
moves coverage/missing-price notes below the chart and table. No calculations or
portfolio files change.

Reviewed against release-v1 `cb7d348e9ab9270b9d9f523cc8e8146c76847e69`.
A read-only three-way comparison identifies three conflicts for the release
session to resolve during integration:

- `docs/release-plan.md`: retain both sessions' appended handoffs.
- `src/portfolio_app/label_ui.py`: retain release's sector-default selection and
  explicit-selection callback, adding this branch's control help.
- `src/portfolio_app/geography_ui.py`: retain this branch's controls-first layout
  and move release's money-market explanation into the granularity tooltip.

Preserve release's newer demo allocations/fixtures, ETF availability status,
packaging changes and browser synchronization fixes in the files that merge
without text conflicts. This branch changes no dependencies, lockfile, package
configuration, file formats or core exposure/valuation calculations. Source
smoke selectors were updated for the new manual-entry action and text quantities.

Currency support remains an independent integration with substantial UI overlap.
Adapt its cost-conversion controls to `holding_amounts` and the purchase-row save
path; do not restore the old average/total switch. Replace EUR-only choices and
EUR-specific help/docs only with the validated conversion feature, preserve saved
foreign/unspecified costs and purchase components, and test both features
combined. Buy/sell accounting remains deferred; optional rows cover purchases
making up the current holding without intervening sales or splits.

Source browser/navigation/import/save/restart/lifecycle smoke passed in this
session. The online preview on port 8617 was restarted after the last Python
change and checked again for live search, identity/currency fields, linked costs,
geography controls/coverage/tooltips, country mode and narrow layouts. It uses
an isolated invented demo; no personal portfolio was accessed or saved. Native
Windows/macOS/Linux installer acceptance was not performed by this session.
After integration, rerun combined tests and source smoke, build a fresh candidate,
and perform the existing platform acceptance checklist for that exact build.
No merge, publication or release tag was created here.

Final branch validation: `uv run pytest -q` passed all 1,076 tests in 440.58 s
with Chromium configured and browser checks required; no skips. Inputs were
synthetic temporary workspaces with offline fixtures. Documentation checks passed
separately (11 tests), as did Ruff, diff whitespace and tracked-file privacy
checks. The complete branch is ready for integration with the above conflict
resolutions; the merged release and the combined currency feature have not been
validated by this run. The final handoff commit contains documentation only.

## UI polish integrated with currency and tour (2026-10-06)

The complete `feature/v1-ui-polish` history through
`a1b27312be6126f0e860170d5b2d71f6503f037e` is integrated on top of release
`c776a78bd538b2b15ddcae6b89902a45d360808d`, which already contains the currency
and optional-tour branches. Integration uses an isolated worktree,
`/tmp/portfolio-v1-polish-integration`, branch `integrate/v1-ui-polish`.
The earlier EUR-only UI handoff describes its branch before this reconciliation;
the combined app supports EUR/USD/GBP reporting and original purchase currencies.

The compact form, explicit investment selection, linked average/total costs,
optional purchase rows, contextual help, Portfolio settings menu and geography
notes below results are retained. Currency conversion controls sit below the
linked costs. Purchase rows accept per-row FX rates and use historical dates
when supplied rates are absent; unavailable conversions exclude gains without
removing available current valuations. Existing cost components and conversions
survive metadata-only edits. A regression exposed scientific notation in saved
costs being rejected by the editor; stored values now pass through decimal
formatting before comparison with the draft.

Tables and help use the reporting currency and neutral calculation fields.
Tour containers, invitation/restoration state and disabled settings during the
tour remain in place. The release's automatic sector/default-label selection
is preserved alongside its new help, and money market remains explained in the
geography tooltip. No dependency, lockfile or production packaging changes are
introduced; the `uv run` workflow is preserved.

Validation: the combined suite passed **1,185 tests** (1,124 non-browser and
61 required Chromium browser tests), with no failures or skips. Final focused
checks also passed: 12 rebalancing UI tests and the strengthened search test
that preserves a foreign buy-in currency. Browser tests now wait for the manual
position form to finish rerendering before opening its currency dropdown.
An earlier run had Chromium tab crashes while the host's disk was nearly full;
the successful full rerun used temporary browser profiles in RAM. This is a
local test-environment workaround, not an application or packaging dependency.

Source-installed startup, navigation, CSV/Excel import, save/restart, repeated
launch, shutdown and backup/restore smoke checks passed. Ruff, whitespace,
documentation generation (14 HTML pages, 30 help topics), wheel/source-archive
content checks and staged privacy review passed. The commit hook remains enabled.
All tests and previews use synthetic data.

The final source preview was restarted after Python changes and browser-checked
at `http://127.0.0.1:60919`, using its own temporary workspace under
`/tmp/portfolio-v1-polish-preview`. Checks covered the Overview chart, GBP settings
and valuation, linked foreign purchase costs, narrow forms, dark geography,
tour restoration and GBP-labelled category planning. No application exceptions
were observed and no positions were saved during inspection. This preview is
unrelated to other sessions' previews or personal portfolios.

Installer rebuilds remain deferred at the user's request until all intended v1
branches have been integrated. Existing installers do not include this merge.
Final combined native Windows/WebView2 acceptance, optional Linux installer
acceptance and clean-machine checks remain required for newly built candidate
bytes. macOS remains deferred. This integration does not authorize a release tag
or publication.

## Final hosted-build preflight (2026-10-06)

The chosen first release is **v0.1.0**, without a beta suffix or beta text in the
product/release name. The package already declares `0.1.0`. Paying for a final
hosted build is under consideration; no paid workflow dispatch or publication
has been authorized by this preflight work.

An isolated `fix/v1-release-preflight` worktree starts from combined release
`b3ffe2c9cc0f890fadb25d88c9977327db4ebe52`. Both candidate workflows now run
`uv run python tools/release.py preflight` before expensive packaging. This
checks the build interpreter/dependency notices, wheel/source contents and
documentation in disposable directories. Local documentation validation permits
an uncommitted fix; actual candidate builds retain their clean-source requirement.
Both workflows also rehearse the synthetic source application smoke before
freezing, while retaining the tests against the eventual frozen binaries.

The native source-window check now stops its build immediately on failure;
previously it continued through packaging and only failed at the end. Failure
artifacts retain synthetic source-smoke evidence. Unbuffered Python output and
named native unit tests with durations make stalled steps easier to identify.
No dependency upgrade, test retry, weakened assertion or increased job timeout
is introduced. Existing `uv run` commands and installer acceptance remain intact.

One complete dispatch of each existing build workflow retains configured job
limits of 45 Windows, 145 Linux and 85 macOS runner-minutes. These are execution
ceilings for one attempt, not a guarantee of a successful build or a complete
billing cap. Main CI, publication, storage, overhead and another attempt are
additional. Confirm the final source SHA and billing budget before dispatch.
The supported publisher verifies a common run attempt: use a fresh complete
candidate run after failure instead of mixing selectively rebuilt artifacts.
Native Linux/macOS artifacts still require separate review and attachment;
the supported publisher does not attach them automatically.

Local verification uses only synthetic data and an isolated Python environment.
Packaging preflight, source application lifecycle/import/save/restart smoke,
22 release-tool tests (including the newly added missing-license early-failure
regression), Ruff and actionlint passed. The full combined suite also passed:
1,185 tests, including 61 required browser tests, with no skips or failures.
Temporary test/browser data and this worktree's Python environment used RAM
because the local Linux disk was nearly full; no private data or other worktree
was deleted to make space. No installer was rebuilt, no GitHub workflow was dispatched and
no release was tagged or published. Native Windows and macOS final acceptance
cannot be inferred from this Linux source rehearsal.


## Windows 11 test installer before merging main (2026-10-06)

The user requested one fresh Windows installer for their Windows 11 machine,
with main integration after that acceptance. Built locally from clean source
`2797a0d7e1e19a029aeed77be8cce723448938ca`, version **0.1.0**, with the locked
runtime and existing release scripts. The manifest records `run_id: local` and
`source_clean: true`. No GitHub Actions minutes were used. This handoff is newer
than the binary's recorded source commit and does not change those bytes.

The initial native-Windows full suite had 1,182 passes, three expected POSIX-only
skips and one browser timing failure. A metric's new text appeared before
Streamlit finished replacing the header, so opening Portfolio settings could
lose the popover. The test now waits for that rerun to finish before opening
settings; no application behavior, assertions or timeouts changed. The corrected
flow passed in three fresh Windows sessions. All 61 required Windows browser
tests then passed without skips. The unchanged non-browser suite had 1,122 passes
and the three expected platform skips. The build source includes that committed
test fix, and its working tree was clean throughout packaging.

Windows privacy/lint, interpreter and dependency notices, wheel/source contents,
documentation and source application smoke passed. The new frozen application
passed extracted browser/import/save/restart/backup/lifecycle checks outside the
checkout. Native WebView2 checks passed welcome-screen close without transient
errors, startup and chart/tab navigation, real F11, borderless monitor edges,
repeated launch, bundled help and shutdown. The launched executable's PATH
excluded Python/uv directories. The build host is Windows 10 with tools installed;
this does not establish clean-machine or Windows 11 installation acceptance.
Native upload/save dialogs and external-link behavior remain on the manual list.

The delivered folder is `Downloads/Portfolio-Breakdown-0.1.0-test-2797a0d`:
`windows/` retains the complete candidate, corresponding source, frozen guides,
notices, manifest and checksum list. `TESTING.txt` gives the Win11 steps and
`local-validation.json` records the automated results and remaining acceptance.
Each copied candidate file was verified against SHA256SUMS. The Setup executable
can be transferred directly; no outer ZIP is required.

- Installer: `portfolio-breakdown-0.1.0-windows-x64-setup.exe`
- SHA256: `c782f608177f1a8b2ecd1f6f93a28d8569ffbf18605f364211207a3c5e1c271f`

Next: user Windows 11 installation/workflow acceptance, fix and retest any
blocker, then merge release-v1 into main. Main was checked as an ancestor of the
release branch; recheck before choosing a fast-forward. Final hosted builds must
use the accepted main commit and go through the existing candidate/promotion
process. This local test installer is not automatically publishable by that
process. No main merge, release tag, official publication, Linux rebuild or
macOS rebuild was performed. All tests used isolated synthetic workspaces;
existing installations, private data and other sessions' work were left intact.

## Complete portfolio archives and confirmed restore (2026-10-06)

Prepared on `feature/v1-backup-restore` from release-v1
`9f925f1ce4ca8678d37d2a8978819d1810fc334b`, in a separate linked worktree. The
shared working checkout and release branch remain unchanged.

Portfolio settings now creates portable `.portfolio-backup.zip` archives and
reviews uploaded archives separately from holdings CSV/Excel import. Format v1
records creation/app versions and an exact size/SHA-256 inventory. Restore rejects
unsafe/non-portable paths, links, duplicate/colliding entries, unsupported formats,
excessive expansion, damaged files and invalid portfolio schemas, including cost
metadata, currency, company mappings and fund-fee overrides. Arbitrary auxiliary
files, hidden document backups and portfolio caches retain their exact bytes.
Limits are 200 MiB compressed, 1 GiB expanded and 10,000 payload files.

A reentrant cross-process workspace barrier coordinates persisted writes and
snapshot capture. Holdings, settings, multi-file currency changes, ETF publication,
fees and background cache publication participate. Network requests and archive
compression stay outside the barrier. Third-party provider SQLite caches now use
application state; existing portfolio cache files are retained. External editors
must be closed because they do not participate in these advisory locks.

Restore stages and validates privately, shows a summary and editable absolute
new-folder path, then creates/verifies the destination exclusively after the user
chooses Restore and switch. The old workspace remains intact. Cancel before
confirmation creates no destination. Failed activation retains the verified new
folder for retry; failed selection persistence removes provisional launcher aliases.

The launcher owns active-workspace leases and authenticated activation. One atomic
selection record per original launch path persists the confirmed directory and
generation across restarts. The window/tab and URL remain unchanged; old forms,
dialog fragments and background jobs cannot save after a switch. Previous leases
remain reserved until shutdown, and shutdown coordinates with activation. Stop and
repeated-launch focus work through the original and active-workspace aliases.
`--ignore-workspace-selection` provides exact-folder recovery after stopping the
app, without removing the remembered selection; restore-and-switch is disabled in
that recovery mode. Existing stopped-folder copy/migration CLI commands remain.

User storage/recovery instructions and the release checklist now distinguish full
archives from holdings import and legacy directory copies. No dependencies, lockfile,
installer configuration, financial calculations or release version changed.

Validation uses only synthetic temporary workspaces and offline injected providers.
The complete suite passed **1,252 tests with no failures or skips**, including
required Chromium browser coverage. The final Windows reserved-name guard was
then checked in a **68-test** archive/activation/recovery run (including four new
path cases), also without failures or skips. A separate **139-test** focused run
covered ZIP normalization, concurrency, stale fragments, shutdown, cancellation,
restore/restart and the Windows presentation contract. All six existing UX browser
checks passed after making their settings-opening helper preserve an already-open
popover and assert visibility; no retry, timeout increase or feature assertion
was removed. An earlier full run mixed fresh UI code with stale imported modules
while review changes were still being made; the complete successful run used a
fresh process. A later isolated path-guard change adds no new import/API surface.

Source-installed navigation/import/save/restart/legacy recovery/lifecycle smoke
passed. The source preview was restarted after Python changes and checked in
Chromium for the Overview default/chart, all four tabs, GBP settings, archive
creation/download and narrow restore review/cancel, without application exceptions
or saved portfolio edits. Dedicated browser tests verified confirmed in-place
activation, edited destinations, stale dialogs, cancellation and restart selection.
Ruff, diff whitespace, documentation generation/link checks (14 HTML pages and
30 help topics), staged privacy checking and manual staged-diff review passed.
The pre-commit hook remains enabled. No private working portfolio was read or used.
Native Windows/WebView2 upload/Save-dialog cancellation, archive-byte fidelity,
in-place activation, same-shortcut restart, focus and shutdown still require
acceptance on newly built candidate bytes. This session does not rebuild installers,
merge release-v1, publish, or create a release tag.

### Integration review (2026-10-06)

The complete `feature/v1-backup-restore` branch is ready for source integration.
Both local and remote release-v1 still point to
`9f925f1ce4ca8678d37d2a8978819d1810fc334b`, the feature's direct ancestor;
there are no intervening release changes or conflicts to reconcile. Implementation
commit `dfbf6f712874b83340a15b157e6d6568c1febebb` and this handoff update belong
together. Integrate the complete branch: the persistence barriers, launcher leases,
durable selection, stale-writer protection and UI form one coordinated change.
If release-v1 advances, recheck overlapping launcher, workspace, persistence,
currency/ETF UI and release-document changes before integration.

A fresh **143-test** synthetic run passed with required Chromium coverage:
`test_backup`, `test_backup_browser`, `test_backup_ui`, `test_workspace`,
`test_workspace_activation`, `test_launcher`, `test_window`, `test_currency_ui`
and `test_analytics`. It includes the final Windows reserved-path cases as well
as round-trip fidelity, unsafe archives, concurrent writers, cancellation,
confirmed activation and restart persistence. Ruff and diff whitespace checks
passed. The source-installed browser/navigation/import/save/restart/legacy
recovery/lifecycle smoke also passed with the configured Chromium executable;
documentation build/link validation checked 14 pages, 30 help topics
and 41 files. The existing synthetic preview at `http://127.0.0.1:8629`, owned by
`/tmp/portfolio-v1-backup` with data in
`/tmp/portfolio-v1-backup-preview/portfolio`, was checked in Chromium for the
default Overview/chart, all four tabs, Create backup and restore review/cancel.
No source modules changed during this integration review.

There are no new dependencies, lockfile, version or packaging configuration
changes. Existing v1 imports, currencies, ETF features, native-window contracts
and the uv workflow are retained. This is source readiness, not release approval:
the existing Windows installer predates these changes. A later packaging session
must build a new candidate and perform the outstanding Windows/WebView2
upload/save/cancel, archive-byte fidelity, activation, same-shortcut restart,
focus and shutdown checks in the release checklist. No installer rebuild,
release-branch merge, publication or tag was performed here. Private data and
other sessions' worktrees remain untouched.
