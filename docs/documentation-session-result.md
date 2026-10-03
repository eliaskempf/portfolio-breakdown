# Documentation session result

## Branch and scope

Worktree: `/tmp/portfolio-docs-v1`, branch `docs-v1`.
Chosen base: `9f1877d4e2a3222529f979782e0e90c6ee2134dc`.
The integration checkout was inspected read-only; its files were not edited.
No personal workspace, personal handoff, exports, credentials or market caches
were read. Tests used generated synthetic data. No push, merge, tag, GitHub Release,
Pages setting change or deployment occurred.

Commits:

- Dependencies only: `5f693a4f8cd66a2649544c3dcb355871bf700c0e`.
- Site, guides and validation: `bd0d28c88a01626421de381e83db9cd71cbe4ba9`.
- This completion record is a subsequent documentation-only commit; obtain its
  full ID with `git log -1 --format=%H docs/documentation-session-result.md`.

Dependencies, separately: optional `docs = ["mkdocs>=1.6,<2"]`, locked to MkDocs
1.6.1. New transitive lock entries are ghp-import 2.1.0, Markdown 3.11, mergedeep
1.3.4, mkdocs-get-deps 0.2.2, pathspec 1.1.1 and pyyaml-env-tag 1.1. Watchdog gains
macOS wheel entries at its existing version. No existing runtime versions changed.
Reconcile these focused pyproject/lock changes with the window session's branch.

## Delivered

Twelve task-based Markdown guides under `docs/user/`, responsive MkDocs navigation,
local search, 30 stable help topics, strict source/build checks, synthetic CLI
workflow tests, development/candidate/release archive tooling and separate manual
Pages publication. No application-side links or prose were changed.

`docs/documentation-maintenance.md` contains the feature/guide mapping, PR
review process, commands and app-help contract. `docs/documentation-migration.md`
contains the source-function-to-guide inventory, text that must remain contextual,
and the integration checklist. README, docs/install.md and release tooling were
left to their owning integration session.

Necessary supporting changes outside the new docs files: narrow .gitignore and
privacy-checker allowlists for public Markdown and named docs infrastructure.
Private paths, recognizable-secret scans and the enabled pre-commit hook remain
in force. Generated output is ignored under dist/.

## Verification evidence

All commands ran in this worktree's own uv environment. Initial dependency setup:

```sh
uv sync --group docs --group browser --group release
uv --no-cache lock --check
```

The lock check resolved 96 packages without changes. Subsequent local checks used
`UV_CACHE_DIR=/tmp/portfolio-docs-uv-cache` and `uv run --no-sync` to use the already
installed environment without writing the shared cache. Equivalent reproducible
setup after checkout is `uv sync --locked --group docs --group browser --group release`.

```sh
export UV_CACHE_DIR=/tmp/portfolio-docs-uv-cache
uv run --no-sync mkdocs build --strict
uv run --no-sync python tools/docs_site.py check dist/docs-site
uv run --no-sync pytest -q --ignore-glob='tests/*browser.py'
uv run --no-sync pytest -q tests/test_documentation.py
uv run --no-sync ruff check tools/docs_site.py tools/docs_preview.py tests/test_documentation.py src/portfolio_app/privacy.py
/tmp/portfolio-actionlint/actionlint .github/workflows/docs.yml .github/workflows/docs-pages.yml
uv run --no-sync portfolio-check-private --staged
uv run --no-sync portfolio-check-private --tracked
```

Observed: strict build and links passed; **838 non-browser tests passed** in
95 seconds. The final focused documentation rerun passed **11 tests**. The earlier
combined docs/privacy/workspace run passed 89 tests. Ruff, actionlint, staged and
tracked privacy checks passed. Every staged diff was manually inspected before
committing, and the actual commit hook passed. Application browser suites were
not duplicated; app UI behavior was not changed.

The documentation browser check used an existing local Chromium headless shell:

```sh
PORTFOLIO_TEST_CHROMIUM=/tmp/portfolio-playwright/chromium_headless_shell-1243/chrome-headless-shell-linux64/chrome-headless-shell uv run --no-sync python tools/docs_preview.py
```

It passed desktop navigation, search and result navigation, all 30 topic anchors,
mobile navigation/search, and all guide widths at 390px. It rejected external
requests and recorded no JavaScript exceptions or HTTP errors. Passing temporary
URLs were `http://127.0.0.1:44457/portfolio-breakdown/dev/` and
`http://127.0.0.1:39239/portfolio-breakdown/candidates/bd0d28c88a01626421de381e83db9cd71cbe4ba9/`;
those temporary servers were stopped by the check. Initial smoke checks exposed
a bundled dark-mode/highlighting interaction; the site uses the bundled light
theme with CDN highlighting disabled, which passed the final browser run.

The inventory contains 12 guide HTML pages plus 404, bundled CSS/JS/fonts/icons,
search assets/index and sitemaps: **40 hashed files plus build-info.json**. Inspection
found no internal handoffs, release records, private data or screenshots. The unused
bundled darkmode.js is present as a theme asset but is not loaded. Each guide shows
source/version/channel, and build-info records the full SHA and file hashes.

## Immutable archive and preview

At the clean site commit, these commands passed:

```sh
uv run --no-sync python tools/docs_site.py build --channel candidate
uv run --no-sync python tools/docs_site.py assemble dist/docs-site dist/preview/portfolio-breakdown
uv run --no-sync python tools/docs_site.py promote dist/preview/portfolio-breakdown --source-sha bd0d28c88a01626421de381e83db9cd71cbe4ba9 --version 0.1.0
uv run --no-sync python tools/docs_site.py build --channel candidate
uv run --no-sync python tools/docs_site.py assemble dist/docs-site dist/preview/portfolio-breakdown
uv run --no-sync python tools/docs_site.py check dist/preview/portfolio-breakdown/releases/0.1.0
```

Repeated candidate builds were byte-identical, using the source commit timestamp.
Local release promotion preserved candidate bytes. Tests also proved dirty
candidate rejection, immutable overwrite refusal, preservation of older candidates,
replaceable dev docs, and broken-anchor/subpath detection. The local release alias
is a validation artifact, not an official release or publication.

Then dev was rebuilt/assembled and this **public-output-only preview** was started:

```sh
uv run --no-sync python tools/docs_site.py build
uv run --no-sync python tools/docs_site.py assemble dist/docs-site dist/preview/portfolio-breakdown
uv run --no-sync python -m http.server 8768 --bind 127.0.0.1 --directory dist/preview
```

Preview: `http://127.0.0.1:8768/portfolio-breakdown/dev/`.
Version selector: `http://127.0.0.1:8768/portfolio-breakdown/`.
Served directory: `/tmp/portfolio-docs-v1/dist/preview`.
Preview content is from clean source `bd0d28c88a01626421de381e83db9cd71cbe4ba9`,
application package version 0.1.0; this result-record commit changes no site input.
A browser visited the actual port 8768 version selector, dev import guide, local
release rebalance navigation/search and narrow recovery guide successfully.
No application data directory is mounted or served. This session owns that server;
stop its foreground process with Ctrl+C when finished.

## Integration contract and remaining work

Help routes are relative to the real repository Pages base URL:
`dev/`, `candidates/<full-source-sha>/`, or `releases/<package-version>/`, followed
by a topic from `tools/docs_site.py:TOPICS`. Examples: `positions/#buy-ins`,
`import/#live-listings`, `exposure/#other`, `analytics/#risk`.
Released app metadata must pin a matching route; never silently fall back to dev.
Release copies retain candidate provenance/canonical URLs to the same fixed source.

Before integration/publication:

1. Reconcile the planned welcome dialog, portfolio header dropdown, upper-right
   help and final startup/window behavior against the actual integrated UI. Import
   stays in Positions. Do not advertise unimplemented controls or a dedicated window.
2. Correct README's unconditional offline-demo history/analytics claims and
   docs/install.md's offline normal-demo/welcome descriptions. Move duplicated
   prose only after destination review; preserve the contextual qualifications in
   the migration inventory.
3. Integrate app-help links and candidate manifest checks; archive matching docs
   beside binaries and promote their exact bytes together. Add root mkdocs.yml to
   source-release packaging if docs builds from extracted sdists are required.
4. Set the real Pages base URL (example.invalid is a deliberate placeholder),
   approve publication explicitly, review protected environment/Pages settings and
   establish durable archive retention. Only then enable the manual workflow gate.
   The workflow restores the prior successful complete archive and fails when its
   artifact has expired; 90-day Actions retention is not permanent preservation.
5. Run hosted workflow acceptance and a human task walkthrough on the integrated
   candidate. A green docs build/declaration is not proof of semantic accuracy.

FinanzManager export/version recognition remains provisional; provider coverage
is bounded and may change. Final UI/native startup acceptance belongs to the
integrated candidate. These uncertainties are recorded, not invented away.
No final publication approval or integration change is requested by this branch.
