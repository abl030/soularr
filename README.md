# Soularr (abl030 fork)

A Soulseek download engine for music libraries, driven by a SQLite pipeline database. Searches Soulseek via [slskd](https://github.com/slskd/slskd), validates downloads against [MusicBrainz](https://musicbrainz.org/) via [beets](https://beets.io/), and auto-imports to a beets library or stages for manual review.

Originally forked from [mrusse/soularr](https://github.com/mrusse/soularr) by [Michael Russell](https://github.com/mrusse) — a Python script that bridges Lidarr with Soulseek. This fork has diverged significantly: Lidarr is now optional, replaced by a SQLite database as the source of truth for what to download.

## How it works

```
Pipeline DB (SQLite)          slskd (Soulseek)           beets
      │                            │                       │
      │  get_wanted()              │                       │
      ├──────────────► search ────►│                       │
      │                            │  download             │
      │                            │◄─────────────         │
      │                            │                       │
      │              validate against target MBID ────────►│
      │                            │                       │
      │  ┌─── source=request ──────┤                       │
      │  │    dist ≤ 0.15          │  auto-import ────────►│ → /Beets
      │  │                         │                       │
      │  └─── source=redownload ───┤                       │
      │       stage to /Incoming   │  (manual review)      │
      │                            │                       │
      │  mark_done() / mark_failed()                       │
      ◄────────────────────────────┘                       │
```

1. **Wanted albums** come from the pipeline database — added via CLI, Lidarr sync, or LLM review pipeline
2. **Soularr searches** Soulseek for each album, matching by track count and filename similarity
3. **Downloads** are validated against the target MusicBrainz release ID using a beets dry-run import
4. **Two-track pipeline**:
   - **Requests** (`source='request'`): auto-convert FLAC to MP3 VBR V0, auto-import to beets if distance ≤ 0.15
   - **Redownloads** (`source='redownload'`): always stage to `/Incoming` for manual review — these are replacing known-bad source material
5. **Status tracking** in the pipeline DB throughout the lifecycle: wanted → searching → downloading → validating → staged/imported/rejected

## What's different from upstream

This fork is a significant rewrite. The core search-and-download engine from upstream is preserved, but the orchestration layer is new:

### Pipeline database mode
- `album_source.py` — abstraction layer (`DatabaseSource` / `LidarrSource`) so the same search/download code works with either source
- Albums come from a SQLite DB instead of Lidarr's API — no more LMD dependency, edition pinning issues, or RefreshArtist wiping data
- Lidarr is optional — albums can be synced from Lidarr into the DB via a bridge script, or added directly via CLI

### Beets validation + auto-import
- Every download is validated against the target MusicBrainz release ID via a beets harness (dry-run)
- Match classification: `strong_match`, `good_match`, `artist_collab_match`, `high_distance`, `track_count_mismatch`, etc.
- Request albums auto-import to beets (convert FLAC→V0, import via harness, post-flight MBID verification)
- Redownload albums stage to `/Incoming` only — never auto-imported

### Search improvements (from earlier fork work)
- VBR V0/V2 support in `allowed_filetypes`
- Strip special characters and short tokens from search queries
- Cap search tokens at 4 most distinctive words
- Track-name fallback for short artist/album names
- Monitored release preference in `choose_release()`

### Quality control
- Cutoff upgrade loop prevention — only tries strictly better filetypes
- Persistent per-album cutoff denylist (`cutoff_denylist.json`)
- ALAC quality mapping for correct tier matching

## Configuration

Two new config sections beyond upstream:

```ini
[Beets Validation]
enabled = True
harness_path = /path/to/run_beets_harness.sh
distance_threshold = 0.15
staging_dir = /path/to/Incoming
tracking_file = /path/to/beets-validated.jsonl

[Pipeline DB]
enabled = True
db_path = /path/to/pipeline.db
```

All upstream config sections (`[Lidarr]`, `[Slskd]`, `[Release Settings]`, `[Search Settings]`, `[Download Settings]`, `[Logging]`) are preserved and work the same way. See the upstream [README](https://github.com/mrusse/soularr) for those options.

When `[Pipeline DB] enabled = True`, Soularr reads wanted albums from the SQLite database instead of Lidarr. Lidarr is still used for slskd coordination but not as the source of truth.

## Deployment

This fork is deployed via NixOS (not Docker). The NixOS module builds a Python environment with all dependencies and runs Soularr as a systemd oneshot on a 5-minute timer.

For the upstream Docker-based deployment, see the [original README](https://github.com/mrusse/soularr).

## Credits

- **Original Soularr**: [Michael Russell](https://github.com/mrusse) — [mrusse/soularr](https://github.com/mrusse/soularr)
- **Libraries**: [pyarr](https://github.com/totaldebug/pyarr), [slskd-api](https://github.com/bigoulours/slskd-python-api), [music-tag](https://github.com/KristoforMaynworWormo/music-tag)

## License

[MIT](LICENSE) (same as upstream)
