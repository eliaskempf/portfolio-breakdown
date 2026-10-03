# Documentation session handoff

## Mode and objective

Use **Default mode (Plan mode off)**. The scope below is concrete enough to
implement. Start with a short outline, then carry the work through local builds,
checks and a reviewable branch. Do not stop after proposing a plan.

Build the v1 user documentation and its maintenance checks. Documentation should
live outside README, be suitable for GitHub Pages, and correspond to the app
version being used. Prepare deployment, but do not publish the site in this task.

## Checkout and privacy

The shared integration worktree is `/tmp/portfolio-v1-worktree`, branch
`release-v1`. Its confirmed starting commit is:

```text
9f1877d4e2a3222529f979782e0e90c6ee2134dc
```

First verify that worktree's branch and status read-only. Create your own branch
and worktree from this exact commit; do not change the integration checkout:

```sh
git -C /tmp/portfolio-v1-worktree branch --show-current
git -C /tmp/portfolio-v1-worktree status --short
git -C /tmp/portfolio-v1-worktree worktree add -b docs-v1 /tmp/portfolio-docs-v1 9f1877d4e2a3222529f979782e0e90c6ee2134dc
```

If the destination or branch already exists, inspect it rather than deleting or
resetting it. Verify your new branch/status before edits. Work only in your own
worktree. The original checkout contains separate unfinished futures work; do
not modify it or base this task on it. Shared Git metadata changes for normal
worktree/commit operations are expected; do not alter another checkout's files.

Read your checkout's `AGENTS.md`, `docs/release-plan.md`, `docs/install.md`, README
and relevant implementation/tests. Do not read personal `handoff.md`, `data/`,
private portfolios, account details, exports, caches or credentials. Examples
and tests must be invented and isolated. No private screenshots or logs in docs.
Use `uv` for dependencies and all Python commands. Keep the pre-commit hook
enabled; before commits run `uv run portfolio-check-private --staged` and inspect
the staged diff manually. Commit only your task files, never use `git add -f`.

## Ownership and parallel work

Own these areas:

- `docs/user/`: public user guide Markdown, navigation and necessary public assets.
- `mkdocs.yml`: use `docs/user` as the documentation root so internal handoffs and
  release records are not automatically copied into the published site.
- New documentation-specific workflow files, validation tools and tests.
- A docs dependency group in `pyproject.toml` and its `uv.lock` changes.
- A task completion record at `docs/documentation-session-result.md`.

The integration session owns onboarding, app UI, header/sidebar changes, help
buttons, application documentation links, animation integration, console-free
startup and the main release handoff/checklist. Do not edit those areas.
Do not rewrite README or `docs/install.md` in this parallel task. Report their
needed corrections/migration as a concrete integration checklist. In particular,
`docs/install.md` currently incorrectly describes `--demo` as offline.

The window session may also change dependencies on its branch. Keep dependency
changes focused and list them in your result; the integration session will
reconcile the lockfile. Do not merge other branches or push to main/release-v1.

## Product facts and pending UI changes

V1 includes holdings import and extended ETF support; it excludes the futures
sandbox. Preserve the valuation/exposure/taxonomy semantics in the source and
tests. Do not invent financial explanations when behavior is unclear.

At the starting commit, demo positions, costs and targets are invented, but the
normal demo uses public quotes/history and issuer ETF downloads. Quantities are
sized once and do not reset on price refresh. `--demo --offline-demo` selects the
explicit synthetic offline example. All demo files are temporary and separate
from the persistent portfolio. Market-price history is not personal return
history. Unsupported/missing breakdowns stay whole; residual Other is not
automatically an error. Read the implementation for the precise rules.

The integration session is preparing these UI changes:

- Welcome dialog with Explore demo and Start my portfolio; no import welcome card.
- Import remains available within Positions.
- Compact header with a portfolio dropdown, replacing the sidebar.
- Question-mark help control at the upper right.
- Animation/startup changes, with details awaiting another session's handoff.

These are planned behavior, not present in the base commit. Draft stable task-based
guides now; record navigation wording that needs reconciliation. Do not present
unimplemented controls or a dedicated desktop window as shipped features.

## Deliverables

1. A small MkDocs site, built through `uv`, with a clean responsive theme and
   search. Keep dependencies minimal; no application runtime dependency on the
   documentation generator. No analytics or external services needed to build it.
2. Task-based guides covering installation/start/stop/update/recovery; starting
   with demo or an empty portfolio; categories and within-category targets;
   positions and buy-ins; FinanzManager-compatible holdings import and listing
   linking; ETF look-through/Other/source dates; prices/performance/risk meaning
   and limitations; rebalancing; troubleshooting and data storage/privacy.
3. A prose migration inventory. For each substantial in-app explanation, identify
   the source location and destination guide/anchor. Identify the short contextual
   text that must remain: validation errors, unknown/stale/partial-data labels,
   units, destructive-action confirmations and qualifications needed to interpret
   a displayed result. Do not remove app text in this task.
4. Stable topic URLs/anchors and a proposed app-help topic mapping. Keep the mapping
   independent of current sidebar placement. Supply the integration contract;
   application-side link code is owned by the integration session.
5. Documentation validation on pull requests and pushes to main. Run a strict
   build and internal-link/anchor checks, plus meaningful checks of selected
   documented CLI commands or workflows using synthetic data. External-site
   availability must not make ordinary tests flaky. Use existing workflows and
   tests where possible rather than duplicating the browser suite.
6. A modest feature-to-guide mapping and an explicit documentation-impact review
   process for user-visible changes. A change may have no documentation impact,
   but that conclusion needs review. A passing build or matching file timestamp
   is not proof of semantic accuracy; document the remaining human review step.
7. A proposed and locally validated distinction between development docs and
   immutable release/candidate docs. Record the source SHA/version in output.
   Released app help must resolve to matching docs rather than silently describing
   newer main behavior. Coordinate candidate/release hooks through the integration
   checklist; do not change release promotion tooling independently.
8. A GitHub Pages workflow ready for review. Build/test automatically, but keep
   publication separate and manual until explicitly approved. Do not change
   repository Pages settings, enable deployment, create a release/tag, or publish.

## Verification and return handoff

Run the strict site build, link checks, added tests and repository privacy checks.
Preview the generated site locally and check navigation/search, narrow layout,
the GitHub project subpath and representative guide links. Report any browser
verification that was unavailable. Do not make source app data available to the
documentation server. Review the generated file inventory for unintended content.

Return a concise result with:

- Branch/worktree and full commit hashes, with dependencies listed separately.
- Exact build/preview/check commands and observed results.
- Topic routes and app-link/versioning contract.
- Prose migration and pending navigation reconciliation checklist.
- Required integration changes to README/install/release tooling, without making
  those shared edits yourself.
- Remaining factual uncertainties and publication prerequisites.

Leave a reviewable committed branch if checks pass. No deployment or merge is
part of this task. Documentation migration should not block v1 on cosmetic
completeness; prioritize accurate core guides and reliable maintenance checks.
