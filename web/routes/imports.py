"""Manual import route handlers — scan, import, wrong matches."""

import json
import os
import shutil
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from lib.import_service import run_import, log_and_update_import  # type: ignore[import-not-found]
from lib.manual_import import (  # type: ignore[import-not-found]
    scan_complete_folder,
    match_folders_to_requests,
    ImportRequest,
)


def _server():
    from web import server  # type: ignore[import-not-found]
    return server


def get_manual_import_scan(h, params: dict[str, list[str]]) -> None:

    complete_dir = params.get("dir", ["/mnt/data/Media/Temp/Music/Complete"])[0]
    folders = scan_complete_folder(complete_dir)

    # Get wanted requests for matching
    pdb = _server()._db()
    wanted = pdb.get_by_status("wanted")
    requests = [
        ImportRequest(
            id=r["id"],
            artist_name=r["artist_name"],
            album_title=r["album_title"],
            mb_release_id=r.get("mb_release_id", ""),
        )
        for r in wanted
    ]

    matches = match_folders_to_requests(folders, requests)

    h._json({
        "folders": [
            {
                "name": f.name,
                "path": f.path,
                "artist": f.artist,
                "album": f.album,
                "file_count": f.file_count,
                "match": next(
                    ({"request_id": m.request.id,
                      "artist": m.request.artist_name,
                      "album": m.request.album_title,
                      "mb_release_id": m.request.mb_release_id,
                      "score": round(m.score, 2)}
                     for m in matches if m.folder.name == f.name),
                    None,
                ),
            }
            for f in folders
        ],
        "wanted_count": len(requests),
    })


def post_manual_import(h, body: dict) -> None:
    srv = _server()
    request_id = body.get("request_id")
    path = body.get("path")
    if not request_id or not path:
        h._error("Missing request_id or path")
        return

    req = srv._db().get_request(int(request_id))
    if not req:
        h._error(f"Request {request_id} not found", 404)
        return
    mbid = req["mb_release_id"]
    if not mbid:
        h._error("Request has no MusicBrainz release ID")
        return

    import_one_path = os.path.join(
        os.path.dirname(__file__), "..", "..", "harness", "import_one.py")

    outcome = run_import(
        path, mbid,
        request_id=int(request_id),
        import_one_path=import_one_path,
        override_min_bitrate=req.get("min_bitrate"),
    )
    log_and_update_import(srv._db(), int(request_id), outcome,
                          outcome_label="manual_import",
                          staged_path=path)

    h._json({
        "status": "ok" if outcome.success else "error",
        "message": outcome.message,
        "exit_code": outcome.exit_code,
        "request_id": request_id,
        "artist": req["artist_name"],
        "album": req["album_title"],
    })


def get_wrong_matches(h, params: dict[str, list[str]]) -> None:
    """List wrong-match rejections for albums not yet in beets."""
    pdb = _server()._db()
    rows = pdb.get_wrong_matches()

    entries = []
    for row in rows:
        vr_raw = row.get("validation_result")
        vr: dict = (
            vr_raw if isinstance(vr_raw, dict)
            else json.loads(str(vr_raw)) if vr_raw else {}
        )
        failed_path: str = vr.get("failed_path", "")
        # Extract the target candidate (is_target=True or first candidate)
        candidates = vr.get("candidates", [])
        target = next((c for c in candidates if c.get("is_target")), None)
        if not target and candidates:
            target = candidates[0]

        entries.append({
            "download_log_id": row["download_log_id"],
            "request_id": row["request_id"],
            "artist": row["artist_name"],
            "album": row["album_title"],
            "mb_release_id": row.get("mb_release_id"),
            "failed_path": failed_path,
            "files_exist": bool(failed_path and os.path.isdir(failed_path)),
            "distance": vr.get("distance"),
            "scenario": vr.get("scenario"),
            "detail": vr.get("detail"),
            "soulseek_username": row.get("soulseek_username")
                or vr.get("soulseek_username"),
            "candidate": target,
            "local_items": vr.get("items", []),
        })

    h._json({"entries": entries})


def post_wrong_match_delete(h, body: dict) -> None:
    """Delete files for a wrong match and clear its failed_path."""
    log_id = body.get("download_log_id")
    if not log_id:
        h._error("Missing download_log_id")
        return

    pdb = _server()._db()
    entry = pdb.get_download_log_entry(int(log_id))
    if not entry:
        h._error(f"Download log entry {log_id} not found", 404)
        return

    vr_raw = entry.get("validation_result")
    vr: dict = (
        vr_raw if isinstance(vr_raw, dict)
        else json.loads(str(vr_raw)) if vr_raw else {}
    )
    failed_path: str = vr.get("failed_path", "")

    # Delete files from disk if they exist
    if failed_path and os.path.isdir(failed_path):
        shutil.rmtree(failed_path)

    # Clear failed_path so it stops appearing in wrong matches list
    pdb.clear_wrong_match_path(int(log_id))

    h._json({"status": "ok", "download_log_id": log_id})


GET_ROUTES: dict[str, object] = {
    "/api/manual-import/scan": get_manual_import_scan,
    "/api/wrong-matches": get_wrong_matches,
}
POST_ROUTES: dict[str, object] = {
    "/api/manual-import/import": post_manual_import,
    "/api/wrong-matches/delete": post_wrong_match_delete,
}
