---
paths:
  - "web/**"
---

# Web UI Rules

- Single-page app: stdlib `http.server`, vanilla JS, single HTML file (`web/index.html`)
- No build step, no npm, no bundler
- JSON API endpoints in `web/server.py`
- MusicBrainz queries go through local mirror at `http://192.168.1.35:5200` via `web/mb.py`
- The web UI reads download_log JSONB columns (import_result, validation_result) — use the typed field names from the dataclasses, not arbitrary strings
- After changes: `ssh doc2 'sudo systemctl restart soularr-web'`
