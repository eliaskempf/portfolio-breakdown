# Release preparation and publication

## Release contract

Build Windows x64, Ubuntu 22.04/24.04 x64 and experimental Apple Silicon
macOS 15+ installers from one clean, frozen default-branch commit. Preserve the
valuation → exposure → taxonomy → aggregation pipeline and financial semantics.
Use synthetic workspaces for every automated and manual acceptance check.
Candidate builds create artifacts, never tags or releases. Publication verifies
and copies accepted bytes without rebuilding. Source, version, dependency lock
and frozen documentation must agree across every published platform.

## Prepare locally

Preserve existing contributor edits. Run `uv sync --locked --all-groups`,
`uv run ruff check src tests tools`, `uv run pytest` with required Chromium
browser coverage, `uv run python tools/package_smoke.py`, and
`uv run python tools/release.py preflight`. Build and inspect documentation using
[the documentation maintenance procedure](documentation-maintenance.md).
Audit locked runtime dependencies, including the native window extra, with
`uv export --locked --no-dev --extra window --no-emit-project --format requirements-txt`
and `uv run pip-audit --disable-pip --require-hashes --no-deps -r EXPORT_FILE`.
This audits pinned versions without attempting to install platform-specific Qt
build dependencies. For cross-platform advisory coverage, also audit every
locked runtime pin with platform markers removed from a disposable export;
never use that audit-only export to install dependencies.

Review wheel/source inventories, licenses, docs and privacy before packaging.
For smoke checks against an installed wheel, pass its console executable and
`--installation wheel` to `tools/package_smoke.py`. Use `--installation frozen`
for an extracted bundle; omitting the executable selects source mode. Windows
checks the appropriate console-free launcher in each package type. With
`--evidence DIRECTORY`, top-level failures and synthetic process logs are retained
alongside browser diagnostics, including failures outside a browser context.
Update the package version and its local lock entry together without implicitly
upgrading dependencies. Review [release and upgrade notes](user/releases.md)
against the implemented workflows and data compatibility. Link the matching
version's notes in the release description; generated publication text alone
contains provenance and platform caveats, not the product changes.
Working handoffs, session diaries, build reports and detailed privacy findings
belong outside the repository and must not ship in source archives or guides.
Before commits run `uv run portfolio-check-private --staged`, inspect the staged
diff and keep the privacy hook enabled. Before changing repository visibility,
review all remote branches/tags and history, commit metadata, PRs/issues and
attachments, Actions logs/artifacts and existing release assets. Removing a file
from the current tree does not remove historical copies. Report coverage gaps;
never rewrite history or delete remote evidence without explicit approval.

## Reproduce a local package

Use a clean Git checkout on Windows x64 or Ubuntu 22.04 x64 with Python 3.12.
Windows additionally needs Inno Setup 6.5.4 and WebView2. The compiler must have
its adjacent license file; `PORTFOLIO_ISCC` can select the installed `ISCC.exe`.
The build downloads and verifies the matching WebView2 SDK/bootstrapper notices.

```sh
uv sync --locked --all-groups
uv run playwright install chromium
uv run python tools/release.py preflight
uv run python tools/release.py build --directory dist/local-candidate
uv run python tools/release.py test --directory dist/local-candidate
```

The output directory must be empty. These commands build Windows Setup/portable
or the Linux browser archive and test the extracted package; they do not publish.
Local manifests use `run_id=local` and cannot satisfy hosted publication evidence.
For native Ubuntu .deb and Mac DMG builds, use the target-OS recipe and native
display prerequisites in [desktop development](desktop-experiment.md). Mac builds
require Python 3.13. Keep local packages private until their contents and builder
metadata have been reviewed; do not substitute them for accepted hosted bytes.

## Merge and freeze

After local readiness review and merge approval, integrate the release branch
into the default branch, preferring a fast-forward where possible. Test any
resulting merge commit. Record its full SHA and freeze it; do not tag yet.
Routine CI/docs checks run on main pushes and pull requests; dependency audits
also run weekly. Candidate/desktop builds, publication and Pages deployment
remain manual-only. Changing
visibility, enabling/dispatching workflows, Pages deployment and release
publication each require the appropriate explicit approval. Billing setup alone
is not build approval. Recheck branch protections after visibility changes.
When adopting the reusable CI workflow, review any required checks pinned to old
job names and select the Linux/Windows source checks from its first run.

## Build once

First obtain a completed **CI** or **Source validation** run at the frozen SHA.
CI calls the same required Linux/Windows source validation used by candidates:
installed-wheel and source smoke, complete unit/AppTest/browser execution, and
raw collection/execution/JUnit evidence. Supply its run ID as `source_run` to
both installer workflows after build approval. Keep the branch frozen until
both workflows have selected that SHA. They can then package concurrently.

| Workflow | Inputs | Output |
| --- | --- | --- |
| Build candidate | source_run; empty runs source validation in this workflow | Windows Setup/portable ZIP, Linux browser archive, frozen docs and platform acceptance |
| Experimental desktop candidates | source_run, diagnostics_only=false | Ubuntu native .deb and experimental Mac DMG with native/compatibility checks |

Do not duplicate their gates by dispatching CI or Documentation as routine extra
builds. Run Dependency audit only if equivalent local evidence is missing/stale.
Reusing source evidence verifies repository, workflow, commit, dependency lock,
Python/OS, successful jobs, run attempts and complete disjoint test execution;
a diagnostic subset cannot satisfy this gate. Missing or expired evidence fails
closed. Native Linux reuses the complete Linux source run and additionally checks
native lifecycle behavior with the window extra. Mac retains its full unit suite
on Python 3.13 as well as native checks. Source checks retain their 45-minute ceiling. Packaging and acceptance
have separate bounded jobs; these ceilings are not expected durations or a
billing cap. Separate acceptance runners add setup/download overhead on a fully
successful run, but a failed check no longer repeats source validation or packaging.
Review the selected jobs and platform fan-out before approving paid runs.

For transient failures at unchanged source, investigate and rerun only failed
jobs. Packaging uploads `unaccepted-<platform>` before any acceptance job starts.
Acceptance downloads those bytes and records their manifest digest; publication
requires all applicable receipts and successful jobs, including compatibility.
Retained successful platform artifacts may identify earlier attempts of the same
run. Native build evidence must identify the exact artifact attempt; compatibility
evidence must be from that attempt or later and cover the same manifest digest.
When GitHub labels a retained build with the new rerun attempt, the publisher
requires a successful build record from the artifact's original attempt with
identical start/end timestamps and source identity; replacement builds do not
qualify. A source change needs new matching candidates for all published platforms.
Download accepted candidates before their 30-day artifact retention expires.

## Diagnose before another candidate

Inspect the first failure and its evidence. Use **Focused release diagnostics**
for a test file or exact collected node ID, a source smoke, or one Windows/Linux
packaged stage. Select the platform that failed. Newline-separated node IDs are
validated against collection; inputs are never evaluated as shell commands.
For packaged checks supply `artifact_run` and `artifact_name` from the same SHA,
including an `unaccepted-*` artifact from a failed candidate. Only enable
`build_artifact` when no saved package exists. This builds once, without the
full source suite, and stores a diagnostic package that cannot be published.

The local equivalent is:

```sh
uv run python tools/run_diagnostics.py --nodeid tests/test_etf_discovery_browser.py --repeat 3 --cpu-rate 4 --output /tmp/etf-diagnostic
uv run python tools/run_diagnostics.py --suite native-startup --directory dist/local-candidate --output /tmp/native-diagnostic
```

Use a new output directory per run. Repetitions are explicit (1–10) and stop on
the first failure; they never turn a failure into passing acceptance. CPU rate 4
is Chromium CPU throttling, not a simulation of Windows or disk/network load.
Inspect `diagnostic.json`, per-attempt logs/JUnit, traces, screenshots and lifecycle
events before changing synchronization. Native evidence includes process logs and
readiness/attachment timing even if CDP attachment never succeeds. Diagnostic
success still requires a complete source gate and exact-artifact acceptance for
publication. Native Linux/Mac diagnostics remain in the desktop workflow.

## Accept exact artifacts

Use [the candidate checklist](release-checklist.md). Record run URLs, source SHA,
version, lock and every installer hash. Inspect final archives/binaries, bundled
docs, corresponding source, notices and builder paths. Source-only checks do not
establish binary privacy. Manual installer acceptance applies to Windows Setup
on Windows 11. Linux and macOS use automated acceptance only; no manual Linux
or Mac verification is required or planned. Hosted checks must exercise the
exact installers, including Windows upgrade from the checksum-pinned previous
release, Linux upgrade/removal and workspace preservation. Windows checks retain
old runtime libraries and unrelated synthetic files while verifying the new
runtime, then check removal of both installer-owned payloads.
Record whether Python/uv are absent or merely excluded from PATH in the
applicable test environment.

The desktop run provides `candidate-linux-native-x64` and `candidate-macos-arm64`.
Both native platforms require the source gate and their `accept-<platform>` job.
Linux requires successful `build-linux-x64`, `linux-compatibility-x11` and
`linux-compatibility-wayland` jobs. Mac requires `build-macos-arm64` and both
`macos-compatibility-macos-15` and `macos-compatibility-macos-26`. Diagnostic runs
or stale/failed checks cannot accept an installer. Keep the Linux browser archive.
The .deb retains the `portfolio-breakdown-experimental` package/menu identity.

The Mac download is experimental: ad-hoc integrity signing only, no Developer ID
signing/notarization, and unverified personal-Mac installation approval. If its
gates fail, repair them or obtain an explicit deferral decision; do not silently
omit the planned download or claim manual acceptance.

## Documentation and release publication

After public-documentation approval, configure Pages with GitHub Actions as its
source, `DOCS_BASE_URL=https://eliaskempf.github.io/portfolio-breakdown/` and
`DOCS_PUBLISH_APPROVED=true`. Guide source stays with the app; `gh-pages` stores
only the generated version archive. In **Publish reviewed documentation**, select
the frozen `source_sha`, `channel=candidate`, successful `candidate_run`, empty
`release_version` and `acceptance=true`. This uses candidate docs without a fresh
build. Inspect hosted themes, mobile layout, search, links/assets and identity.
Bundled-doc changes require new candidates; deployment configuration may not.

Present source/run IDs, inventory/hashes, acceptance evidence and limitations for
release approval. Then **Publish tested candidate** takes:

- `candidate_run`: successful Build candidate run;
- `linux_native_candidate_run`: accepted desktop run at the same SHA;
- `acceptance=true`: Windows manual acceptance and Linux automated acceptance
  of those exact bytes;
- `macos_candidate_run`: accepted desktop run (normally the same desktop run);
- `experimental_macos=true`: acknowledgment of its disclosed limitations.

The publisher creates the version tag and publishes the release; it is not a
draft-preview operation. Do not create the tag separately. It attaches Setup,
portable ZIP, Linux browser archive, .deb, selected DMG, documentation, matching
source, platform notices/manifests and aggregate `SHA256SUMS`. Native source and
notices use `linux-native-x64` and `macos-arm64` prefixes to avoid collisions.
Inspect interrupted drafts before retrying: existing assets are never overwritten.

After publication, rerun **Publish reviewed documentation** with the same SHA,
`channel=candidate`, `release_version=<candidate-package-version>` (without `v`)
and acceptance. This copies the archived candidate to
`releases/<candidate-package-version>/` without rebuilding. Verify
downloads, checksums and docs signed out. Pages root opens the latest stable
release automatically (Main before the first release). The hosted guide offers
Latest release/Main navigation. Publication
refreshes Main and applies hosted navigation/favicon styling to a separate copy;
the archived and bundled candidate bytes remain unchanged. Keep routine
CI/docs/audit enabled and installer builds and release publication manual.
