# Engineering Workflow

This is currently a single-person or family-use system in active development.
The workflow should catch regressions without adding team-scale ceremony. A
lightweight pull request with passing CI is the normal delivery boundary;
production deployment and release automation are deferred until a deployment
target exists.

## Definition of done

A feature, fix, refactor, research task, or documentation change is complete
when:

1. The implementation and directly related cleanup are finished.
2. Relevant documentation, methodology, operations guidance, and durable
   Copilot memory are reconciled.
3. Focused validation and the full offline merge gate pass.
4. The diff contains no secrets, generated application data, temporary
   artifacts, or unrelated user changes.
5. Logical Conventional Commits are pushed on a focused branch.
6. A lightweight pull request against `main` records the change and validation.
7. The task-owned working tree is clean. Passing CI is the automated merge
   gate.

The repository provides `/shipit` through the `alphawhales-workflow` Copilot
CLI plugin. Register the repository marketplace and install the plugin once per
workstation:

```powershell
copilot plugin marketplace add celestialeye/AlphaWhales
copilot plugin install alphawhales-workflow@alphawhales
```

Use `/shipit` in a new Copilot CLI session to execute the closeout workflow.
Optional text after the command is treated as additional scope, for example:

```text
/shipit close the valuation work and reference issue 42
```

## Validation layers

Run commands from the repository root. Live SEC and market-data refreshes are
data operations, not substitutes for tests.

### 1. Change-focused validation

Run the smallest relevant tests first. Typical focused suites include:

```powershell
python -m pytest tests/test_market_insights.py -q
python -m pytest tests/test_investor_history.py -q
python -m pytest tests/test_sentiment_conviction.py -q
python -m pytest tests/test_investor_screening.py -q
python -m pytest tests/test_investor_performance.py -q
python -m pytest tests/test_awfi.py tests/test_awfi_service.py tests/test_awfi_period_view.py tests/test_predictive_sentiment.py tests/test_predictive_sentiment_cli.py -q
```

Add or update tests with behavior changes. Favor deterministic tests using
fixtures, temporary directories, and mocked data providers. Network access,
local caches, execution order, and wall-clock timing must not determine CI
success.

### 2. Offline merge gate

Every pull request must pass:

```powershell
python -m compileall -q config.py roster_store.py data_service.py awfi_service.py main.py pair_service.py prefetch.py run.py predictive_sentiment investor_screening
python -c "import main; print(type(main.app).__name__, len(main.data_service.cache))"
node --check static/js/app.js
python -m pytest -q
```

GitHub Actions runs this gate on pull requests and pushes to `main` using
Python 3.11 and Node.js 22. For the current single-developer workflow, checking
that this job is green before merge is sufficient; required approvals and
elaborate branch rules are unnecessary.

### 3. Browser validation

For frontend changes, start the application on port 8010:

```powershell
python -m uvicorn main:app --port 8010
```

Use the configured Playwright browser tools to verify:

- the affected workflow at desktop width;
- the same workflow at 390px width;
- no browser console errors;
- no page-level horizontal overflow;
- automatic static-asset cache busting and correct cache-warm behavior;
- loading, empty, unavailable, error, and populated states affected by the
  change.

Run these browser checks when UI behavior changed. Add automated end-to-end
tests only when stable fixtures make them reliable; do not make live SEC or
OpenBB availability a merge dependency.

### 4. Data and research validation

Changes to ingestion, screening, performance, valuation, sentiment, or AWFI
must include deterministic contract and regression tests for the changed
formula, boundary, or publication behavior. Run live refresh or rebuild
commands only when the task intentionally changes generated data, and never
commit generated `cache/` or `data/investor_screening/` content.

## Lightweight review practice

Every change gets a practical self-review of the complete diff, not only the
last edit. Check:

- correctness, failure behavior, and backward compatibility;
- API, DataFrame, JavaScript, template, SSE, and storage contracts;
- financial labels, methodology, units, and estimate caveats;
- security-sensitive input, file, URL, credential, and subprocess handling;
- deterministic test coverage for regressions;
- accidental generated files, debug code, screenshots, logs, and secrets.

Use an independent code-review agent only when the change is nontrivial or
risky, such as:

- financial formulas, methodology, valuation, sentiment, performance, or AWFI;
- SEC ingestion, cache or database publication, concurrency, or recovery;
- security-sensitive input, file, URL, credential, or subprocess handling;
- broad API, template, JavaScript, SSE, or storage contract changes.

Straightforward documentation, copy, configuration, and tiny fixes do not need
an extra review pass. AI review is advisory unless it identifies a
high-confidence correctness, security, or data-loss problem. Fix those problems
before shipping; avoid style-only review churn.

The current offline CI gate is enough for routine work. Do not add required
human approvals, CODEOWNERS, coverage quotas, CodeQL, dependency-enforcement
jobs, or other team-scale governance until the repository becomes public,
multi-user, or production-hosted.

## Documentation and memory hygiene

Update documentation only where behavior or procedures changed. This can
include `README.md`, `CHANGELOG.md`, architecture, methodology, operations,
contribution guidance, and subsystem specifications.

Delete a planning or task document only when it is clearly superseded and has
no remaining historical or methodological value. Never hand-edit generated
cache snapshots as documentation.

Copilot memory should contain durable, non-sensitive conventions that are not
obvious from a small code sample. Reuse or upvote an existing memory instead of
creating a duplicate, and downvote outdated memories. Do not store credentials,
personal data, transient task details, or generated-data observations.

## Git and pull requests

- Branch from `main` using `feat/`, `fix/`, `docs/`, `refactor/`, `test/`,
  `ci/`, or `chore/`.
- Preserve unrelated and pre-existing worktree changes.
- Stage explicit paths and inspect the staged diff before committing.
- Use logical Conventional Commits with the required Copilot co-author trailer.
- Never amend, force-push, bypass hooks, or rewrite history without an explicit
  request.
- Push with upstream tracking and create or update a pull request against
  `main`.
- Treat the pull request as a lightweight change record and CI checkpoint, not
  an approval ceremony.
- Do not merge automatically. Confirm CI is green before merging.

## Worktree cleanup

Remove only exact, inspected, task-owned temporary files. Never use broad
recursive deletion or discard unrelated user work to manufacture a clean
status. Before handoff, confirm:

```powershell
git status --short
git log -1 --oneline
git branch --show-current
```

The final handoff records the branch, commits, pull-request link, validation
result, and any explicit blocker.

## Deferred deployment work

There is no production deployment target. When one is selected, add a separate
delivery design covering environments, secrets, infrastructure ownership,
artifact promotion, database and cache migration, observability, rollback,
recovery objectives, approvals, and post-deployment verification. Do not embed
placeholder production credentials or speculative deploy steps in the current
CI workflow.
