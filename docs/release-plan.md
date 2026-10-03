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
