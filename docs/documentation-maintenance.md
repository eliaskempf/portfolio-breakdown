# Documentation maintenance and app-help contract

Only `docs/user/` is published. Internal release records and this document remain
outside the site root. MkDocs uses its bundled responsive theme and search;
syntax-highlighting CDN requests are disabled. No analytics or runtime docs
library is added to the app. Builds require installed locked dependencies but no
external service. Links to external sites are not fetched by validation.

## Build, check and preview

From this checkout:

```sh
uv sync --locked --group docs --group browser
uv run mkdocs build --strict
uv run python tools/docs_site.py check dist/docs-site
uv run pytest -q tests/test_documentation.py tests/test_privacy.py tests/test_workspace.py tests/test_demo.py tests/test_imports.py
uv run playwright install chromium
uv run python tools/docs_preview.py
```

The browser script serves only a temporary copy of generated public output on a
free loopback port, under `/portfolio-breakdown/dev/`. It checks desktop navigation,
search, every app topic, mobile navigation/search and horizontal overflow. It
blocks external requests and fails on browser exceptions or HTTP errors. Use
`PORTFOLIO_TEST_CHROMIUM` to select an existing compatible Chromium executable.
It prints the exact URL and stops its own server afterwards. Never serve the
repository root, source app data or a private workspace as documentation.

For an interactive preview, build into an ignored public-only archive:

```sh
uv run python tools/docs_site.py build
uv run python tools/docs_site.py assemble dist/docs-site dist/preview/portfolio-breakdown
uv run python -m http.server 8768 --bind 127.0.0.1 --directory dist/preview
```

Visit `http://127.0.0.1:8768/portfolio-breakdown/dev/`. Stop this server with Ctrl+C.
The version banner shows dirty local changes. Inspect `dist/docs-site/build-info.json`
for the exact source SHA, app version, route, topic map and file checksums.

## Review process

Every pull request declares two lines in its body:

```text
Docs impact: updated
Docs review: Reviewed import/review and live-listings against the changed flow; synthetic import tests pass.
```

Use `Docs impact: none` with a concrete reason when a change has no user-visible
effect. The documentation workflow requires a declaration on all PRs, including
changes only to application source. Human review must assess the declaration:
check affected controls, defaults, scope/denominators, persistence, data sources,
unknown/stale states and limitations against the implementation and tests.
A green build, matching timestamp, presence of a topic or a written declaration
**does not prove semantic accuracy**. Before a candidate, a reviewer walks through
the affected tasks with invented data and records source SHA plus evidence.
Revisit this mapping when features move; do not treat it as exhaustive coverage.

| Feature / source | User guide and stable anchor | Useful existing tests |
| --- | --- | --- |
| app.py, launcher.py, workspace.py | install/#launch-stop; storage/#backup | test_launcher.py, test_workspace.py, test_documentation.py |
| demo.py, onboarding_ui.py | getting-started/#demo | test_demo.py, test_onboarding_ui.py |
| allocation.py, target_ui.py, strategic_ui.py | allocation/#targets; allocation/#overview | test_allocation_expansion.py, test_targets.py, test_strategic.py |
| taxonomy.py, filtering.py | allocation/#classifications; exposure/#filters | test_aggregation_filtering.py |
| position_ui.py, purchases.py, balances.py | positions/#edit; #buy-ins; #purchases; #balances | test_positions.py, test_purchases.py, test_total_buy_in.py |
| import_ui.py, imports.py, import_readers.py | import/#review; import/#live-listings | test_imports.py, test_import_ui.py, existing import browser suite |
| exposure_analysis.py, etf_setup.py, etf_refresh.py | exposure/#look-through; #other; #sources; #bonds | test_etf.py, test_etf_discovery.py, test_etf_sources.py |
| company_merges.py, grouping.py, geography.py | exposure/#merges; #filters | test_company_merges.py, test_geography.py |
| valuation.py, history.py, performance.py | performance/#valuation; #performance; #history | test_prices_valuation.py, test_history.py, test_performance.py |
| analytics.py, risk.py, fundamentals.py | analytics/#risk; #metrics; #sources | test_analytics.py, test_risk.py |
| scoped_rebalancing.py, rebalancing.py | rebalance/#modes; #constraints; #results | test_rebalancing.py, test_capped_contributions.py |

During the pre-release billing pause, all repository workflows are manual-only,
including documentation validation. Local review is still required before merges.
The manual Documentation workflow checks strict MkDocs, generated internal asset/link/
anchor checks, synthetic documented CLI recovery workflows, existing demo/import/
privacy tests and the focused documentation browser smoke. It does not duplicate
the application's browser suite. External provider availability cannot break
ordinary doc tests. Build outputs remain ignored artifacts, not Git source.

The GitHub workflow is also temporarily disabled at repository level to prevent
the older copy on `main` from running. After the manual-only workflow changes reach
`main`, it can be enabled for deliberate dispatches. Restoring automatic PR/push
checks is a separate, explicit decision after the billing pause.

## Stable app-help contract

The authoritative topic map is `TOPICS` in `tools/docs_site.py`, exported
in every `build-info.json` and checked against generated anchors. Topic IDs are
independent of sidebar/header layout. Example: `buy-ins` maps to
`positions/#buy-ins`; `etf-other` maps to `exposure/#other`; `risk` maps to
`analytics/#risk`. There are 30 topics. Application links are rendered by `workspace_ui.py` using
`documentation.guide_url`. Bundled-help behavior is tested in
`tests/test_bundled_documentation.py`.

Packaged app help opens the exact bundled public guide on a separate loopback
server, independently of Pages publication. A source checkout also serves its
`dist/docs-site` output locally when it contains `build-info.json`; build it with
the commands above before starting the app. Without that output the current
source app links to `https://eliaskempf.github.io/portfolio-breakdown/dev/`.
Source developers must rebuild docs and restart the owning app after guide edits.
If the packaged guide or its index is missing, the app's help menu reports it
as unavailable and recommends reinstalling the complete app. It does not substitute
development documentation; the in-app tour remains available.

`DOCS_BASE_URL` configures the published site root, including the project subpath
and trailing slash. The real root is `https://eliaskempf.github.io/portfolio-breakdown/`;
`example.invalid` below is a local validation placeholder. Keep relative page
links and explicit anchors. Do not rename an exposed topic/anchor without
retaining compatibility. A frozen version is never retroactively edited.

| App build | Documentation route |
| --- | --- |
| Development checkout | `dev/` (visibly marked development) |
| Candidate with source SHA | `candidates/<full-source-sha>/` |
| Official release | `releases/<package-version>/`, an exact copy of approved candidate bytes |

Candidates can share package version 0.1.0; the full source SHA distinguishes them.
Candidate packaging records the documentation source SHA, route and build-info
checksum in its manifest and bundles that exact site with the executable. The
installed app serves those bytes locally. Verify their identity before release;
missing matching docs must not be accepted as a valid release package. Do not
transmit portfolio data in URLs.

## Versioning and promotion

Build committed candidates with:

```sh
uv run python tools/docs_site.py build --channel candidate --base-url https://example.invalid/portfolio-breakdown/
uv run python tools/docs_site.py assemble dist/docs-site dist/pages
```

Uncommitted candidates fail. Dev may be replaced; existing candidate routes reject
different manifests/bytes. `build-info.json` records SHA/version, dirty status,
channel, URL, routes and hashes. `check` verifies the complete file inventory and
anchors without network access. For a previously archived approved candidate:

```sh
uv run python tools/docs_site.py promote dist/pages --source-sha FULL_SOURCE_SHA --version 0.1.0
```

Promotion copies the candidate bytes without rebuilding and refuses a different
existing release. The version must match the candidate's package version. The
release alias retains candidate provenance/canonical URLs, intentionally pointing
to the same immutable documentation. Relative navigation and search remain valid
under either route. An erratum needs a newly reviewed candidate/version, not a
silent overwrite. Candidate schema 2 connects the frozen documentation ZIP and
build-info checksum
to the app source SHA. `tools/docs_candidate.py` validates the selected successful
Build candidate run; publication consumes those archived bytes. An older schema 1
candidate does not establish this app/help identity.

Guide source stays with the application in `docs/user/`. Only generated static
output belongs on `gh-pages`. The manual `docs-pages.yml` workflow is dormant
unless `DOCS_PUBLISH_APPROVED` is explicitly `true`, acceptance is checked, and
dispatch is from the default branch. It fetches the existing `gh-pages` branch,
preserves older versions, assembles dev/candidate or promotes archived candidate
bytes, validates every retained version, and commits the static archive back to
`gh-pages`. It then removes Git metadata from the deployment directory and sends
only static files to the Pages Actions deployment. The 90-day Actions archive is
an additional copy, not the source of version retention.

Before enabling publication, verify access to the existing `gh-pages` archive and
back it up independently. The workflow initializes a new branch only when Git
reports no matching `gh-pages` ref (exit status 2). Access/network errors stop
publication, as do fetch failures after finding an existing ref. Existing Pages content from another
workflow requires explicit migration. Do not delete versions, branches or run
history to force an empty archive.

Publication prerequisites: human approval; correct `DOCS_BASE_URL`; Pages Actions
source and protected `github-pages` environment reviewed by the repository owner;
source-to-app candidate identity verified; existing archive and retention reviewed;
hosted workflow accepted. A local candidate with `run_id: local` is not accepted
by the hosted candidate-publication gate. Build/test/upload in `docs.yml` grants
only read permissions and never deploys. No publication is authorized by running
local documentation checks.

For a pre-release visual review, use the local preview above first: it costs no
Actions minutes and requires no installer rebuild. Layout, typography and colors
can be changed through `mkdocs.yml`, theme overrides or local CSS while preserving
the documented topic IDs. If a public preview is wanted, merge the tooling into
the default branch, approve/configure Pages, and manually publish channel `dev`
at the reviewed source SHA. This neither tags nor publishes an application release.
After visuals are approved, build the binary candidate once. Publish its frozen
guide from that successful candidate run, then add the release alias by copying
the same guide bytes. Do not rebuild the docs independently for the final release.

Implementation references: [MkDocs configuration](https://www.mkdocs.org/user-guide/configuration/)
for strict link diagnostics and [GitHub custom Pages workflows](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages)
for artifact deployment permissions and environments.
