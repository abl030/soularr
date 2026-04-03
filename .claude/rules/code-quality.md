# Code Quality Standards

## Type Safety
- All new dataclasses, functions, and module-level code must pass pyright with 0 errors
- Use typed dataclasses (not dicts) for structured data crossing module boundaries
- **No dual-interface types.** Never add `__getitem__`, `.get()`, or `isinstance(x, dict)` dispatch to a dataclass. If a function receives both dicts and dataclasses, that is a type error — fix the callers, not the receiver. Temporary bridges become permanent bugs.
- If a function parameter is untyped and accepts multiple representations (dict or dataclass), type it and fix all callers to pass the correct type
- Inner data structures must also be typed — no `list[dict]` when a dataclass exists
- Verify with: `pyright <files>` on every touched file before committing

## Testing — Red/Green TDD
- Write tests FIRST (RED), then implement (GREEN)
- Every new function, dataclass, and decision branch needs test coverage
- Use `nix-shell --run "bash scripts/run_tests.sh"` for full suite
- Read `/tmp/soularr-test-output.txt` instead of re-running the 2-minute suite
- For single modules during dev: `nix-shell --run "python3 -m unittest tests.<module> -v"`

## API Contract Tests
- Every API endpoint consumed by the frontend must have a contract test in `test_web_server.py`
- Contract tests use a real in-memory SQLite beets DB (not mocks) to verify actual query results
- Define a `REQUIRED_FIELDS` set per endpoint — the fields the frontend JS relies on
- Assert every returned dict includes all required fields with non-empty values
- When adding a field the frontend needs, add it to `REQUIRED_FIELDS` first (RED), then fix the backend (GREEN)
- See `TestLibraryArtistContract` as the reference pattern

## Logging & Auditability
- Every download outcome (success, rejection, timeout, crash) MUST create a download_log row
- Use typed JSON dataclasses (`ImportResult`, `ValidationResult`) — never raw dicts
- Store the full JSON in JSONB columns for SQL queryability
- Never throw away data the harness or subprocess provides — log everything

## Decision Logic
- All quality/import decisions must be pure functions in `lib/quality.py`
- No decision logic inline in soularr.py — call the pure function, branch on result
- Every pure function must have direct unit tests (not just tested through integration)

## Frontend (JavaScript)
- ES6 modules in `web/js/` — no inline `<script>` in HTML
- `// @ts-check` + JSDoc types on all exported functions
- Pure functions in `web/js/util.js` — testable via Node without DOM
- Shared state in `web/js/state.js` — no bare globals across modules
- Cross-module onclick handlers go through `window.*` bindings in `main.js`
- `node --check web/js/*.js` must pass (runs in pre-commit + CI)
- JS unit tests in `tests/test_js_util.mjs` — run with `node`, no npm
- Static JS served at `/js/*.js` by server.py

## Backend (Server Routes)
- Route handlers in `web/routes/*.py` — server.py is routing/cache/main only
- Route functions take `(handler, params)` or `(handler, body)`, not `self`
- All beets queries go through `lib/beets_db.py` `BeetsDB` class — no raw `sqlite3.connect()` in handlers
- Route modules access server globals via `_server()` deferred import

## Finish What You Start
- Don't build infrastructure without wiring it up. Every new function, dataclass, or mode must be called from production code. If it's only reachable via manual config nobody sets, it's dead code.
- Before marking any feature complete, trace the full path from trigger to effect. Ask: "Does this actually run in production without manual intervention?" If not, it's not done.
- A new dataclass that nothing constructs, a config option nobody sets, a fallback that never triggers — these are all incomplete work, not shipped features.

## Pre-Commit Review Gate
- For non-trivial changes (new dataclasses, refactored function signatures, new pipeline paths), spawn an Opus agent to review the diff before committing.
- The agent should check: correctness bugs, test gaps, callers you missed, type errors, unfinished wiring.
- Fix everything it finds before committing. This is not optional.

## Commits
- One logical change per commit
- Run full test suite + pyright before committing
- Deploy and verify live after pushing
