# Release preparation and publication

## Release contract

Build Windows x64, Ubuntu 22.04/24.04 x64 and experimental Apple Silicon
macOS 14+ installers from one clean, frozen default-branch commit. Preserve the
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
All repository workflows currently use manual dispatch only. Keep disabled
workflows disabled until those definitions reach the default branch. Changing
visibility, enabling/dispatching workflows, Pages deployment and release
publication each require the appropriate explicit approval. Billing setup alone
is not build approval. Recheck branch protections after visibility changes.

## Build once

Dispatch these two workflows from the same frozen default-branch SHA after build
approval. They can run concurrently; keep the branch frozen until both runs have
selected that SHA.

| Workflow | Inputs | Output |
| --- | --- | --- |
| Build candidate | manual dispatch | Windows Setup/portable ZIP, Linux browser archive, tests, frozen docs and Ubuntu browser compatibility |
| Experimental desktop candidates | diagnostics_only=false | Ubuntu native .deb and experimental Mac DMG with native/compatibility checks |

Do not duplicate their gates by dispatching CI or Documentation as routine extra
builds. Run Dependency audit only if equivalent local evidence is missing/stale.
Current installer-job ceilings are 45 Windows, 145 Linux and 85 macOS runner
minutes across both workflows (275 total, not elapsed time or a billing cap).
Publication permits 20 Linux minutes and each Pages dispatch 25. Reruns, storage
and unrelated jobs are additional.

For transient failures at unchanged source, investigate and rerun only failed
jobs. Retained successful platform artifacts may identify earlier attempts of
the same run. Native build evidence must identify the exact artifact attempt;
compatibility evidence must be from that attempt or later.
When GitHub labels a retained build with the new rerun attempt, the publisher
requires a successful build record from the artifact's original attempt with
identical start/end timestamps and source identity; replacement builds do not
qualify. A source change needs new matching candidates for all published platforms.
Download accepted candidates before their 30-day artifact retention expires.

## Accept exact artifacts

Use [the candidate checklist](release-checklist.md). Record run URLs, source SHA,
version, lock and every installer hash. Inspect final archives/binaries, bundled
docs, corresponding source, notices and builder paths. Source-only checks do not
establish binary privacy. Manual installer acceptance applies to Windows Setup
on Windows 11. Linux and macOS use automated acceptance only; no manual Linux
or Mac verification is required or planned. Hosted checks must exercise the
exact installers, including Linux upgrade/removal and workspace preservation.
Record whether Python/uv are absent or merely excluded from PATH in the
applicable test environment.

The desktop run provides `candidate-linux-native-x64` and `candidate-macos-arm64`.
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
`channel=candidate`, `release_version=0.1.0` (without `v`) and acceptance. This
copies the archived candidate to `releases/0.1.0/` without rebuilding. Verify
downloads, checksums and docs signed out. Pages root lists versions; it is not an
automatic latest redirect. Restore automatic CI/docs/audit only as a separate
reviewed change; keep installer builds and release publication manual.
