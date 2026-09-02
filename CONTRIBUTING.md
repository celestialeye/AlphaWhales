# Contributing

## Development setup

```powershell
python -m pip install -r requirements.txt
python -m pip install pytest
python run.py
```

The application runs at `http://127.0.0.1:8000`.

## Repository boundaries

- Commit source, templates, static assets, documentation, and reference data.
- Do not commit generated files under `cache/`.
- Do not commit `.env`, browser traces, virtual environments, or credentials.
- Keep the application independent from sibling repositories. Copy and
  document reusable primitives rather than importing from external workspaces.

## Code organization

- Put SEC access, calculations, joins, and response shaping in
  `DataService`.
- Keep routes in `main.py` thin.
- Keep pair calculations in `pair_service.py`.
- Keep universe-wide SEC ingestion, quality checks, and screening snapshot
  generation in `investor_screening/`; the FastAPI route should call
  `ScreeningService` rather than query the large foundation directly.
- Keep AWFI research, universe selection, score versioning, and publication in
  `predictive_sentiment/`. Runtime score retrieval belongs in
  `awfi_service.py`; routes should not query research tables directly.
- Keep Jinja templates structural; presentation behavior belongs in
  `static/js/app.js` and `static/css/styles.css`.
- Preserve upstream SEC DataFrame field names until API response shaping.
- Join SEC current/comparison rows by CUSIP, not ticker.

## Financial-model requirements

- Clearly distinguish reported SEC values from estimates.
- Any estimated cost, flow, valuation, sizing, or pair result needs a visible
  methodology and caveat.
- Missing inputs must produce unavailable or zero-risk output, not a
  success-shaped fallback.
- Pair execution text must only appear for a `READY` signal.
- Use median portfolio weight when describing a typical holder.

## Validation

Run focused tests for the changed behavior, then the complete offline merge
gate:

```powershell
python -m compileall -q config.py roster_store.py data_service.py awfi_service.py main.py pair_service.py prefetch.py run.py predictive_sentiment investor_screening
python -c "import main; print(type(main.app).__name__, len(main.data_service.cache))"
node --check static/js/app.js
python -m pytest -q
```

For frontend changes:

1. Start the application on port 8010.
2. Use the workspace Playwright MCP tools.
3. Check desktop and 390-pixel mobile widths.
4. Check browser console errors.
5. Confirm there is no page-level horizontal overflow.
6. Confirm static-asset cache busting and cache-warm behavior.

See [Engineering Workflow](docs/ENGINEERING_WORKFLOW.md) for the definition of
done, focused test groups, documentation and memory hygiene, pull-request
requirements, and worktree cleanup procedure. Install the repository plugin
once with `copilot plugin marketplace add celestialeye/AlphaWhales` followed
by `copilot plugin install alphawhales-workflow@alphawhales`, then run
`/shipit` in a new Copilot CLI session to execute that closeout workflow.

## Commit style

Use Conventional Commits:

```text
feat(ticker): add valuation and pair intelligence
fix(qoq): reconcile owner-count changes
docs: document cache and valuation methodology
```
