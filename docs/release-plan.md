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
The application and frozen executable code are unchanged by this follow-up.
