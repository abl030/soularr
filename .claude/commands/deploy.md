# Deploy to doc2

Push code, update flake input on doc1, rebuild doc2, verify.

## Steps

1. Run tests and pyright first:
```bash
nix-shell --run "bash scripts/run_tests.sh"
```
Check `/tmp/soularr-test-output.txt` for failures. Do NOT proceed if tests fail.

2. Check pyright on touched files:
```bash
pyright <files that changed>
```

3. Commit and push:
```bash
git add <files> && git commit -m "<message>" && git push
```

4. Update flake input on doc1 and push:
```bash
ssh doc1 'cd ~/nixosconfig && nix flake update soularr-src && git add flake.lock && git commit -m "soularr: <description>" && git push'
```

5. Rebuild doc2:
```bash
ssh doc2 'sudo nixos-rebuild switch --flake github:abl030/nixosconfig#doc2 --refresh'
```

6. Verify deployed code has the change:
```bash
ssh doc2 'grep "<something unique>" /nix/store/*/lib/quality.py 2>/dev/null | head -1'
```

## Database migrations

If schema changed (new columns), backup and migrate BEFORE deploying:
```bash
ssh doc2 'pg_dump -h 192.168.100.11 -U soularr soularr' > /tmp/soularr_backup_$(date +%Y%m%d_%H%M%S).sql
ssh doc2 "psql -h 192.168.100.11 -U soularr soularr -c \"DO \\\$\\\$ BEGIN ALTER TABLE <table> ADD COLUMN <col> <type>; EXCEPTION WHEN duplicate_column THEN NULL; END \\\$\\\$;\""
```

## IMPORTANT
- `restartIfChanged = false` — deploys don't restart soularr. The 5-min timer picks up new code on next cycle.
- To force a run: `ssh doc2 'sudo systemctl start soularr &'` (don't block — it's a oneshot)
- Flake updates MUST happen on doc1 (has git push credentials). NEVER from doc2 or Windows.
