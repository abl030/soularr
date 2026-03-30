---
paths:
  - "lib/pipeline_db.py"
  - "scripts/pipeline_cli.py"
---

# Pipeline DB Rules (PostgreSQL)

- Connection: `postgresql://soularr@192.168.100.11:5432/soularr`
- **MUST use `autocommit=True`** — prevents idle-in-transaction deadlocks
- DDL migrations: use separate short-lived connections with `lock_timeout`
- New columns: always use idempotent `DO $$ BEGIN ALTER TABLE ADD COLUMN ... EXCEPTION WHEN duplicate_column THEN NULL; END $$`
- JSONB columns: use for structured audit data (import_result, validation_result)
- Only 3 statuses: wanted, imported, manual
- Backup before migrations: `ssh doc2 'pg_dump -h 192.168.100.11 -U soularr soularr' > /tmp/soularr_backup_$(date +%Y%m%d_%H%M%S).sql`
