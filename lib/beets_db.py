"""Beets library database queries.

Read-only access to the beets SQLite DB. Centralizes all scattered
sqlite3.connect() calls from soularr.py and import_one.py.

Usage:
    with BeetsDB() as db:
        info = db.get_album_info("mbid-here")
        if info:
            print(info.min_bitrate_kbps, info.is_cbr)
"""

import os
import sqlite3
from dataclasses import dataclass
from typing import Optional

DEFAULT_BEETS_DB = os.environ.get("BEETS_DB", "/mnt/virtio/Music/beets-library.db")


@dataclass
class AlbumInfo:
    """Query result from beets DB for a single album."""
    album_id: int
    track_count: int
    min_bitrate_kbps: int
    is_cbr: bool
    album_path: str  # directory containing the tracks


class BeetsDB:
    """Read-only connection to the beets SQLite library database."""

    def __init__(self, db_path: str = DEFAULT_BEETS_DB) -> None:
        if not os.path.exists(db_path):
            raise FileNotFoundError(f"Beets DB not found: {db_path}")
        self._conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "BeetsDB":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    @staticmethod
    def _decode_path(raw: object) -> str:
        """Decode a beets path (stored as bytes or str) to a string."""
        if isinstance(raw, bytes):
            return raw.decode("utf-8", errors="replace")
        return str(raw)

    def album_exists(self, mb_release_id: str) -> bool:
        """Check if an MBID is already in the beets library."""
        row = self._conn.execute(
            "SELECT 1 FROM albums WHERE mb_albumid = ?", (mb_release_id,)
        ).fetchone()
        return row is not None

    def get_album_info(self, mb_release_id: str) -> Optional[AlbumInfo]:
        """Get full album info for quality gate / postflight verification.

        Returns None if the MBID isn't in beets or has no tracks.
        """
        album_row = self._conn.execute(
            "SELECT id FROM albums WHERE mb_albumid = ?", (mb_release_id,)
        ).fetchone()
        if not album_row:
            return None
        album_id: int = album_row[0]

        # Get bitrate stats (exclude 0-bitrate tracks)
        rows = self._conn.execute(
            "SELECT bitrate, path FROM items WHERE album_id = ? AND bitrate > 0",
            (album_id,)
        ).fetchall()
        if not rows:
            return None

        bitrates = [r[0] for r in rows]
        min_br = min(bitrates)
        is_cbr = len(set(bitrates)) == 1
        track_count = len(rows)

        # Album path = directory of first track
        first_path = self._decode_path(rows[0][1])
        album_path = os.path.dirname(first_path)

        return AlbumInfo(
            album_id=album_id,
            track_count=track_count,
            min_bitrate_kbps=int(min_br / 1000),
            is_cbr=is_cbr,
            album_path=album_path,
        )

    def get_min_bitrate(self, mb_release_id: str) -> Optional[int]:
        """Get min track bitrate (kbps) for an MBID. Returns None if not found."""
        album_row = self._conn.execute(
            "SELECT id FROM albums WHERE mb_albumid = ?", (mb_release_id,)
        ).fetchone()
        if not album_row:
            return None
        br_row = self._conn.execute(
            "SELECT MIN(bitrate) FROM items WHERE album_id = ? AND bitrate > 0",
            (album_row[0],)
        ).fetchone()
        if not br_row or not br_row[0]:
            return None
        return int(br_row[0] / 1000)

    def get_item_paths(self, mb_release_id: str) -> list[tuple[int, str]]:
        """Get all (item_id, path) pairs for an album. Returns empty list if not found."""
        album_row = self._conn.execute(
            "SELECT id FROM albums WHERE mb_albumid = ?", (mb_release_id,)
        ).fetchone()
        if not album_row:
            return []
        rows = self._conn.execute(
            "SELECT id, path FROM items WHERE album_id = ?", (album_row[0],)
        ).fetchall()
        return [(r[0], self._decode_path(r[1])) for r in rows]

    def get_album_path(self, mb_release_id: str) -> Optional[str]:
        """Get the directory path for an album's tracks. Returns None if not found."""
        row = self._conn.execute(
            "SELECT (SELECT path FROM items WHERE album_id = a.id LIMIT 1) "
            "FROM albums a WHERE a.mb_albumid = ?", (mb_release_id,)
        ).fetchone()
        if not row or not row[0]:
            return None
        return os.path.dirname(self._decode_path(row[0]))

    # ── Web UI query methods ────────────────────────────────────────

    def check_mbids(self, mbids: list[str]) -> set[str]:
        """Return the subset of MBIDs that exist in the beets library."""
        if not mbids:
            return set()
        ph = ",".join("?" for _ in mbids)
        rows = self._conn.execute(
            f"SELECT mb_albumid FROM albums WHERE mb_albumid IN ({ph})", mbids
        ).fetchall()
        return {r[0] for r in rows}

    def check_mbids_detail(self, mbids: list[str]) -> dict[str, dict[str, object]]:
        """Batch lookup: MBID → {beets_tracks, beets_format, beets_bitrate, beets_samplerate, beets_bitdepth}."""
        if not mbids:
            return {}
        ph = ",".join("?" for _ in mbids)
        rows = self._conn.execute(
            f"SELECT a.mb_albumid, "
            f"  (SELECT COUNT(*) FROM items WHERE album_id = a.id) AS track_count, "
            f"  (SELECT GROUP_CONCAT(DISTINCT i.format) FROM items i WHERE i.album_id = a.id) AS formats, "
            f"  (SELECT MIN(i.bitrate) FROM items i WHERE i.album_id = a.id) AS min_bitrate, "
            f"  (SELECT MIN(i.samplerate) FROM items i WHERE i.album_id = a.id) AS samplerate, "
            f"  (SELECT MAX(i.bitdepth) FROM items i WHERE i.album_id = a.id) AS bitdepth "
            f"FROM albums a WHERE a.mb_albumid IN ({ph})", mbids
        ).fetchall()
        result: dict[str, dict[str, object]] = {}
        for r in rows:
            if r[0] is None:
                continue
            result[r[0]] = {
                "beets_tracks": r[1],
                "beets_format": r[2],
                "beets_bitrate": int(r[3] / 1000) if r[3] else None,
                "beets_samplerate": r[4],
                "beets_bitdepth": r[5],
            }
        return result

    def search_albums(self, query: str, limit: int = 100) -> list[dict[str, object]]:
        """Search albums by artist or album name (LIKE, case-insensitive)."""
        rows = self._conn.execute(
            "SELECT a.id, a.album, a.albumartist, a.year, a.mb_albumid, "
            "       a.albumtype, a.label, a.country, "
            "       (SELECT COUNT(*) FROM items WHERE items.album_id = a.id) as track_count, "
            "       (SELECT GROUP_CONCAT(DISTINCT i.format) FROM items i WHERE i.album_id = a.id) as formats, "
            "       a.added, a.mb_releasegroupid, a.release_group_title, "
            "       (SELECT MIN(i.bitrate) FROM items i WHERE i.album_id = a.id) as min_bitrate "
            "FROM albums a "
            "WHERE a.albumartist LIKE ? COLLATE NOCASE OR a.album LIKE ? COLLATE NOCASE "
            "ORDER BY a.albumartist, a.year, a.album LIMIT ?",
            (f"%{query}%", f"%{query}%", limit),
        ).fetchall()
        return [self._album_row_to_dict(r) for r in rows]

    def get_recent(self, limit: int = 50) -> list[dict[str, object]]:
        """Get most recently added albums."""
        rows = self._conn.execute(
            "SELECT a.id, a.album, a.albumartist, a.year, a.mb_albumid, "
            "       a.albumtype, a.label, a.country, "
            "       (SELECT COUNT(*) FROM items WHERE items.album_id = a.id) as track_count, "
            "       (SELECT GROUP_CONCAT(DISTINCT i.format) FROM items i WHERE i.album_id = a.id) as formats, "
            "       a.added, a.mb_releasegroupid, a.release_group_title, "
            "       (SELECT MIN(i.bitrate) FROM items i WHERE i.album_id = a.id) as min_bitrate "
            "FROM albums a ORDER BY a.added DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [self._album_row_to_dict(r) for r in rows]

    def get_album_detail(self, album_id: int) -> Optional[dict[str, object]]:
        """Get full album metadata + track list. Returns None if not found."""
        album = self._conn.execute(
            "SELECT id, album, albumartist, year, mb_albumid, albumtype, "
            "       label, country, artpath, added "
            "FROM albums WHERE id = ?", (album_id,)
        ).fetchone()
        if not album:
            return None
        items = self._conn.execute(
            "SELECT id, title, artist, track, disc, length, format, "
            "       bitrate, samplerate, bitdepth, path "
            "FROM items WHERE album_id = ? ORDER BY disc, track", (album_id,)
        ).fetchall()
        tracks = [{
            "id": i[0], "title": i[1], "artist": i[2], "track": i[3],
            "disc": i[4], "length": i[5], "format": i[6],
            "bitrate": i[7], "samplerate": i[8], "bitdepth": i[9],
            "path": self._decode_path(i[10]) if i[10] else None,
        } for i in items]
        album_path = os.path.dirname(tracks[0]["path"]) if tracks and tracks[0]["path"] else None
        return {
            "id": album[0], "album": album[1], "albumartist": album[2],  # type: ignore[literal-required]
            "year": album[3], "mb_albumid": album[4], "type": album[5],
            "label": album[6], "country": album[7],
            "artpath": self._decode_path(album[8]) if album[8] else None,
            "added": album[9], "tracks": tracks, "path": album_path,
        }

    _ALBUM_SELECT = (
        "SELECT a.id, a.album, a.albumartist, a.year, a.mb_albumid, "
        "       a.albumtype, a.label, a.country, "
        "       (SELECT COUNT(*) FROM items WHERE items.album_id = a.id) as track_count, "
        "       (SELECT GROUP_CONCAT(DISTINCT i.format) FROM items i WHERE i.album_id = a.id) as formats, "
        "       a.added, a.mb_releasegroupid, a.release_group_title, "
        "       (SELECT MIN(i.bitrate) FROM items i WHERE i.album_id = a.id) as min_bitrate "
        "FROM albums a "
    )

    def get_albums_by_artist(self, name: str, mbid: str = "") -> list[dict[str, object]]:
        """Get all albums by an artist. Matches by MB artist ID (if given) or name.

        When mbid is provided, matches on mb_albumartistid exact or mb_albumartistids LIKE,
        plus a name fallback for Discogs-only albums (no MB UUID in mb_albumartistid).
        """
        if mbid:
            rows = self._conn.execute(
                self._ALBUM_SELECT +
                "WHERE a.mb_albumartistid = ? OR a.mb_albumartistids LIKE ? "
                "  OR (a.albumartist LIKE ? COLLATE NOCASE "
                "      AND (a.mb_albumartistid IS NULL OR a.mb_albumartistid = '' "
                "           OR a.mb_albumartistid NOT LIKE '%-%')) "
                "ORDER BY a.year, a.album",
                (mbid, f"%{mbid}%", f"%{name}%"),
            ).fetchall()
        else:
            rows = self._conn.execute(
                self._ALBUM_SELECT +
                "WHERE a.albumartist LIKE ? COLLATE NOCASE "
                "ORDER BY a.year, a.album",
                (f"%{name}%",),
            ).fetchall()
        return [self._album_row_to_dict(r) for r in rows]

    def find_by_artist_album(self, artist: str, album: str) -> Optional[int]:
        """Find track count by artist+album name. Returns None if not found."""
        row = self._conn.execute(
            "SELECT a.id FROM albums a "
            "WHERE a.albumartist LIKE ? COLLATE NOCASE AND a.album LIKE ? COLLATE NOCASE "
            "LIMIT 1",
            (f"%{artist}%", f"%{album}%"),
        ).fetchone()
        if not row:
            return None
        count = self._conn.execute(
            "SELECT COUNT(*) FROM items WHERE album_id = ?", (row[0],)
        ).fetchone()
        return count[0] if count else None

    def get_avg_bitrate_kbps(self, mb_release_id: str) -> Optional[int]:
        """Get average track bitrate (kbps) for an MBID. Returns None if not found."""
        album_row = self._conn.execute(
            "SELECT id FROM albums WHERE mb_albumid = ?", (mb_release_id,)
        ).fetchone()
        if not album_row:
            return None
        avg_row = self._conn.execute(
            "SELECT CAST(AVG(bitrate) AS INTEGER) FROM items "
            "WHERE album_id = ? AND bitrate > 0",
            (album_row[0],),
        ).fetchone()
        if not avg_row or not avg_row[0]:
            return None
        return int(avg_row[0] / 1000)

    @staticmethod
    def _album_row_to_dict(r: tuple[object, ...]) -> dict[str, object]:
        """Convert a standard album query row to dict."""
        return {
            "id": r[0], "album": r[1], "artist": r[2], "year": r[3],
            "mb_albumid": r[4], "type": r[5], "label": r[6],
            "country": r[7], "track_count": r[8], "formats": r[9],
            "added": r[10], "mb_releasegroupid": r[11],
            "release_group_title": r[12], "min_bitrate": r[13],
        }
