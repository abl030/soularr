# Pre-commit Quality Check

Run pyright + full test suite. Use this before committing.

## Steps

1. Run pyright on all key files:
```bash
pyright lib/quality.py lib/beets.py lib/beets_db.py lib/pipeline_db.py harness/import_one.py harness/beets_harness.py album_source.py soularr.py tests/test_validation_result.py tests/test_import_result.py tests/test_quality_decisions.py tests/test_beets_db.py
```

Must be **0 errors**. Do not proceed if there are new errors (pre-existing ones in soularr.py from psycopg2 typing are OK).

2. Run full test suite:
```bash
nix-shell --run "bash scripts/run_tests.sh"
```

3. Check results:
```bash
grep -E "^Ran |^OK|^FAILED" /tmp/soularr-test-output.txt
grep "^FAIL:\|^ERROR:" /tmp/soularr-test-output.txt
```

Must show `OK`. slskd live test skips (Docker not running) are acceptable.

4. If both pass, safe to commit.
