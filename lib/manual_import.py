"""Manual import — scan folders, match to pipeline requests, run import.

Pure functions for folder name parsing and request matching.
Subprocess wrapper for running import_one.py.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Audio file extensions we expect in a download folder
from lib.quality import AUDIO_EXTENSIONS_DOTTED as _AUDIO_EXTS

# Patterns for stripping noise from folder names
_YEAR_PAREN_RE = re.compile(r"\s*[\(\[]\s*\d{4}\s*[\)\]]")  # (2022) or [2012]
_BRACED_RE = re.compile(r"\s*\{[^}]*\}")  # {Hostess Entertainment...}
_SCENE_SUFFIX_RE = re.compile(r"[-_](WEB|CD|FLAC|MP3|VINYL)[-_](?:\w+[-_])*\d{4}[-_]\w+$", re.IGNORECASE)
_LEADING_YEAR_RE = re.compile(r"^\d{4}\s+")
_BRACKETED_YEAR_RE = re.compile(r"\s*\[\d{4}\]\s*")


@dataclass(frozen=True)
class FolderInfo:
    """A folder in the Complete directory with parsed metadata."""

    name: str
    path: str
    artist: str
    album: str
    file_count: int


@dataclass(frozen=True)
class ImportRequest:
    """A pipeline request that could match a folder."""

    id: int
    artist_name: str
    album_title: str
    mb_release_id: str


@dataclass(frozen=True)
class FolderMatch:
    """A matched folder-to-request pair with confidence score."""

    folder: FolderInfo
    request: ImportRequest
    score: float  # 0.0–1.0


def parse_folder_name(name: str) -> FolderInfo:
    """Parse an unstructured folder name into artist + album.

    Handles patterns:
    - "Artist - Album"
    - "Artist - Year - Album"
    - "Album (2022)"
    - "Artist_Name-Album-WEB-2026-SCENE"
    - "1987 Sister"
    - "[2012] Album"
    """
    if not name:
        return FolderInfo(name=name, path="", artist="", album="", file_count=0)

    working = name

    # Handle scene releases: "Artist_Name-Album-WEB-2026-SCENE"
    scene_match = _SCENE_SUFFIX_RE.search(working)
    if scene_match:
        working = working[:scene_match.start()]
        # Scene releases use underscores for spaces, hyphens for field separators
        parts = working.split("-")
        if len(parts) >= 2:
            artist = parts[0].strip().replace("_", " ")
            album = parts[1].strip().replace("_", " ")
            return FolderInfo(name=name, path="", artist=artist, album=album, file_count=0)

    # Strip braced metadata: {Hostess Entertainment...}
    working = _BRACED_RE.sub("", working)
    # Strip year in parens/brackets: (2022) or [2012]
    working = _YEAR_PAREN_RE.sub("", working)
    # Strip bracketed year prefix: [2012]
    working = _BRACKETED_YEAR_RE.sub(" ", working)

    # Try "Artist - Year - Album" or "Artist - Album"
    if " - " in working:
        parts = [p.strip() for p in working.split(" - ")]
        if len(parts) >= 3:
            # "Artist - Year - Album" — middle part is year if numeric
            if parts[1].isdigit():
                artist = parts[0]
                album = " - ".join(parts[2:])
            else:
                artist = parts[0]
                album = " - ".join(parts[1:])
        else:
            artist = parts[0]
            album = parts[1]
        return FolderInfo(name=name, path="", artist=artist.strip(), album=album.strip(), file_count=0)

    # Strip leading year: "1987 Sister"
    stripped = _LEADING_YEAR_RE.sub("", working).strip()
    if stripped != working.strip():
        return FolderInfo(name=name, path="", artist="", album=stripped, file_count=0)

    return FolderInfo(name=name, path="", artist="", album=working.strip(), file_count=0)


def _tokenize(s: str) -> set[str]:
    """Lowercase and split into word tokens for matching."""
    return set(re.findall(r"[a-z0-9]+", s.lower()))


def match_folders_to_requests(
    folders: list[FolderInfo],
    requests: list[ImportRequest],
    min_score: float = 0.3,
) -> list[FolderMatch]:
    """Match folders to pipeline requests by fuzzy artist+album token overlap.

    Returns the best match per folder (if score >= min_score), sorted by score desc.
    """
    results: list[FolderMatch] = []

    for folder in folders:
        folder_tokens = _tokenize(folder.artist + " " + folder.album)
        if not folder_tokens:
            continue

        best_match: FolderMatch | None = None
        folder_album_tokens = _tokenize(folder.album)
        for req in requests:
            req_tokens = _tokenize(req.artist_name + " " + req.album_title)
            req_album_tokens = _tokenize(req.album_title)
            if not req_tokens:
                continue
            # Full Jaccard (artist + album)
            overlap = folder_tokens & req_tokens
            union = folder_tokens | req_tokens
            full_score = len(overlap) / len(union) if union else 0.0
            # Album-only Jaccard (catches folders with no artist in name)
            album_overlap = folder_album_tokens & req_album_tokens
            album_union = folder_album_tokens | req_album_tokens
            album_score = len(album_overlap) / len(album_union) if album_union else 0.0
            score = max(full_score, album_score)
            if score >= min_score and (best_match is None or score > best_match.score):
                best_match = FolderMatch(folder=folder, request=req, score=score)

        if best_match is not None:
            results.append(best_match)

    return sorted(results, key=lambda m: m.score, reverse=True)


def scan_complete_folder(base_path: str) -> list[FolderInfo]:
    """Scan a directory for album folders. Returns FolderInfo with file counts."""
    if not os.path.isdir(base_path):
        return []

    results: list[FolderInfo] = []
    for entry in sorted(os.listdir(base_path)):
        full_path = os.path.join(base_path, entry)
        if not os.path.isdir(full_path):
            continue
        # Count audio files
        audio_count = 0
        for f in os.listdir(full_path):
            ext = os.path.splitext(f)[1].lower()
            if ext in _AUDIO_EXTS:
                audio_count += 1
        if audio_count == 0:
            continue

        parsed = parse_folder_name(entry)
        results.append(FolderInfo(
            name=entry,
            path=full_path,
            artist=parsed.artist,
            album=parsed.album,
            file_count=audio_count,
        ))
    return results
