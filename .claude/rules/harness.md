---
paths:
  - "harness/**"
  - "lib/beets.py"
  - "lib/quality.py"
---

# Beets Harness Rules

- The harness runs in the beets Python environment (Nix Home Manager on doc1), NOT in the dev shell
- `_serialize_album_candidate()` must capture EVERY field from AlbumMatch — never discard data
- All harness output types must be typed dataclasses: HarnessItem, HarnessTrackInfo, TrackMapping
- import_one.py emits ImportResult as a `__IMPORT_RESULT__` sentinel JSON line on stdout, human logging on stderr
- Use `beets-docs` skill (`.claude/commands/beets-docs.md`) to look up beets internals
