# AGENTS.md

## Private data and Git

Never commit personal portfolio data, account/broker details, acquisition costs,
targets, classifications, transaction exports, screenshots, reports, backups,
market-data caches, credentials, or local agent/editor configuration. Treat the
entire `data/` directory and the personal `handoff.md` as private. Do not copy
their contents into source, tests, documentation, commit messages, or PR bodies.
Do not use `git add -f` to bypass these protections.

Tests and demos must generate explicitly synthetic data in temporary or ignored
directories, without reading the user's working data. Only public instrument
metadata and deliberately invented examples belong in source code.

Before any commit, run `uv run portfolio-check-private --staged`, inspect the
staged diff for personal information, and keep the repository's pre-commit hook
enabled. The automated check is an additional safeguard, not a substitute for
reviewing the content of otherwise permitted source/documentation files.

Never rewrite Git history or remove local private data as a privacy cleanup
without checking what is affected. Untracking must preserve local data.

## Project context

This repository implements a local portfolio-analysis application.

Read the project specification / implementation handoff before making substantial changes. The specification is the source of truth for product behavior, including:

- portfolio and exposure semantics
- hierarchical asset taxonomies
- ETF look-through
- direct + indirect exposure
- filtering and visualization behavior

Do not duplicate those requirements here.

---

## Environment

Use `uv` for all Python environment and dependency management.

Expected commands:

```bash
uv sync
uv run pytest
uv run portfolio-app
```

Use `uv run ...` rather than manually activating a virtual environment.

Commit:

```text
pyproject.toml
uv.lock
```

Do not introduce Poetry, Pipenv, Conda, or manually maintained `requirements.txt` files unless explicitly requested.

---

## Packaging

The project should be a normal installable Python package using `pyproject.toml`.

Prefer a `src/` layout:

```text
src/
└── portfolio_app/
    ├── __init__.py
    └── ...

tests/
data/
```

Application/business logic belongs inside `src/portfolio_app/`, not in loose repository-root scripts.

Imports should work after installation and must not depend on the repository root being implicitly on `PYTHONPATH`.

Prefer a project entrypoint such as:

```toml
[project.scripts]
portfolio-app = "portfolio_app.app:main"
```

if this integrates cleanly with Streamlit.

---

## Preferred stack

Unless there is a good reason to change it, prefer:

- Python
- Streamlit
- pandas
- Plotly
- PyYAML
- yfinance or similarly lightweight market-data access
- pytest

Keep dependencies minimal.

Do not introduce a frontend/backend split, database, authentication, or cloud infrastructure unless explicitly required.

---

## Architecture

Keep calculation/business logic independent from Streamlit wherever practical.

Prefer modules with focused responsibilities, approximately:

```text
src/portfolio_app/
├── app.py
├── holdings.py
├── prices.py
├── exposures.py
├── taxonomy.py
├── aggregation.py
└── etf.py
```

This structure is illustrative rather than mandatory.

Prefer:

- small pure transformation functions
- explicit data flow
- type hints where useful
- testable calculations
- clear separation of data loading, transformations, and rendering

Avoid:

- giant Streamlit modules
- calculations embedded inside chart code
- hidden global state
- duplicated aggregation logic
- premature abstractions

---

## Important architectural constraint

Preserve the general pipeline described in the project spec:

```text
holdings
→ valuation
→ optional ETF expansion
→ normalized exposures
→ classifications
→ aggregation
→ visualization
```

Do not couple UI code or ETF-source-specific code directly into the core aggregation logic.

The hierarchical taxonomy model is a core abstraction. Do not replace it with ad-hoc flat sector columns.

---

## ETF implementation

Do not attempt to build universal ETF support unless explicitly requested.

ETF integrations should be narrowly implemented for ETFs actually needed by the project and normalize into the common internal representation described in the spec.

Keep provider-specific parsing isolated from portfolio calculations.

---

## Testing

Run tests with:

```bash
uv run pytest
```

Calculation logic should be tested independently from Streamlit.

When changing relevant behavior, add or update tests covering the affected transformation.

Particularly important areas include:

- valuation
- hierarchical aggregation
- filtering / percentage normalization
- ETF expansion
- ETF residual `Other` handling
- direct + indirect exposure merging
- missing / unclassified data

Do not make live network access necessary for unit tests. Use fixtures or injected/static price data.

---

## Working style

Before making a non-trivial change:

1. inspect the existing implementation and relevant tests
2. read the applicable section of the project specification
3. preserve good existing abstractions
4. make the smallest coherent change
5. add or update tests
6. run relevant tests
7. perform a lightweight app smoke test when UI behavior changes

### Running-preview verification

The Streamlit `/_stcore/health` endpoint checks process liveness only. It does
not prove that the current code, imported modules, or affected views work.

Before reporting an app change as ready:

- Identify the actual preview URL/port, checkout and data directory being used.
- If Python modules changed while that preview was running, restart that preview
  with the same data directory and launch options. A browser refresh or successful
  test in a separate process does not clear stale server-side imports. Do not
  restart another session's app.
- Open the actual preview in a browser and visit the affected tabs and modes.
  Verify the requested default view, chart rendering, control placement and
  absence of application exceptions, including imports triggered by navigation.
- Keep runtime inspection read-only; do not record private portfolio values or
  screenshots. Automated tests and demos must continue to use synthetic data.
- If browser verification is unavailable, explicitly report the preview as
  unverified rather than treating a healthy endpoint as a successful UI check.

Document assumptions when behavior is not obvious.

Do not silently invent financial semantics when the specification is ambiguous.

---

## Priorities

When tradeoffs arise, prioritize:

```text
correct calculations
> flexible data model
> maintainable package architecture
> useful visualization
> UI polish
```

Avoid expanding project scope without a concrete requirement.

## Autonomy

When the specification is sufficiently clear, work autonomously through
implementation, tests, and smoke checks without asking for confirmation.

Resolve minor implementation choices yourself. Only ask the user when
blocked by genuinely missing information or a materially ambiguous
financial/domain requirement.

Do not stop merely to report intermediate progress if the next required
implementation step is clear.
