# Deployment Rules

- All code deploys via Nix flake: push → flake update on doc1 → rebuild doc2
- Flake updates MUST happen on doc1 (has git push credentials). NEVER from doc2.
- `restartIfChanged = false` on the soularr service — deploys don't restart it. The 5-min timer picks up new code on the next cycle.
- Always verify deployed code: `ssh doc2 'grep "<unique string>" /nix/store/*/lib/quality.py 2>/dev/null'`
- Database migrations: backup first, run migration manually via psql, THEN deploy code
- Use the `/deploy` command for the full sequence
