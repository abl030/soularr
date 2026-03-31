#!/usr/bin/env python
from __future__ import annotations

import argparse
import re
import unicodedata
import os
import sys
import time
import subprocess as sp
import shutil
import difflib
import configparser
import logging
import json
import urllib.request
import urllib.error
from datetime import datetime
from typing import Any, TYPE_CHECKING
import copy
import music_tag
import slskd_api
from slskd_api.apis import users

if TYPE_CHECKING:
    from album_source import DatabaseSource
    from lib.config import SoularrConfig


class EnvInterpolation(configparser.ExtendedInterpolation):
    """
    Interpolation which expands environment variables in values.
    Borrowed from https://stackoverflow.com/a/68068943
    """

    def before_read(self, parser, section, option, value):
        value = super().before_read(parser, section, option, value)
        return os.path.expandvars(value)


# Allows backwards compatibility for users updating an older version of Soularr
# without using the new [Logging] section in the config.ini file.
DEFAULT_LOGGING_CONF = {
    "level": "INFO",
    "format": "[%(levelname)s|%(module)s|L%(lineno)d] %(asctime)s: %(message)s",
    "datefmt": "%Y-%m-%dT%H:%M:%S%z",
}

# === Typed Config (populated in main() via SoularrConfig.from_ini()) ===
cfg: SoularrConfig = None  # type: ignore[assignment]  # Set in main()

# === API Clients & Logging ===
slskd: slskd_api.SlskdClient = None  # type: ignore[assignment]  # Set in main()
config = None
logger = logging.getLogger("soularr")

# === API client instances (set in main()) ===
pipeline_db_source: "DatabaseSource" = None  # type: ignore[assignment]  # Set in main()

# === Runtime State & Caches ===
search_cache = {}
folder_cache = {}
user_upload_speed = {}  # username → upload speed in bytes/sec (from search results)
broken_user = []


def album_match(expected_tracks, slskd_tracks, username, filetype):
    counted = []
    total_match = 0.0

    album_info = get_album_by_id(expected_tracks[0]["albumId"])
    album_name = album_info["title"]
    artist_name = album_info["artist"]["artistName"]

    for expected_track in expected_tracks:
        expected_filename = expected_track["title"] + "." + filetype.split(" ")[0]
        best_match = 0.0

        for slskd_track in slskd_tracks:
            slskd_filename = slskd_track["filename"]

            # Try to match the ratio with the exact filenames
            ratio = difflib.SequenceMatcher(None, expected_filename, slskd_filename).ratio()

            # If ratio is a bad match try and split off (with " " as the separator) the garbage at the start of the slskd_filename and try again
            ratio = check_ratio(" ", ratio, expected_filename, slskd_filename)
            # Same but with "_" as the separator
            ratio = check_ratio("_", ratio, expected_filename, slskd_filename)

            # Same checks but preappend album name.
            ratio = check_ratio("", ratio, album_name + " " + expected_filename, slskd_filename)
            ratio = check_ratio(" ", ratio, album_name + " " + expected_filename, slskd_filename)
            ratio = check_ratio("_", ratio, album_name + " " + expected_filename, slskd_filename)

            if ratio > best_match:
                best_match = ratio

        if best_match > cfg.minimum_match_ratio:
            counted.append(expected_filename)
            total_match += best_match

    if len(counted) == len(expected_tracks) and username not in cfg.ignored_users:
        logger.info(f"Found match from user: {username} for {len(counted)} tracks! Track attributes: {filetype}")
        logger.info(f"Average sequence match ratio: {total_match / len(counted)}")
        logger.info("SUCCESSFUL MATCH")
        logger.info("-------------------")
        return True

    return False


def check_ratio(separator, ratio, expected_filename, slskd_filename):
    if ratio < cfg.minimum_match_ratio:
        if separator != "":
            expected_filename_word_count = len(expected_filename.split()) * -1
            truncated_slskd_filename = " ".join(slskd_filename.split(separator)[expected_filename_word_count:])
            ratio = difflib.SequenceMatcher(None, expected_filename, truncated_slskd_filename).ratio()
        else:
            ratio = difflib.SequenceMatcher(None, expected_filename, slskd_filename).ratio()

        return ratio
    return ratio


def album_track_num(directory):
    files = directory["files"]
    allowed_filetypes_no_attributes = [item.split(" ")[0] for item in cfg.allowed_filetypes]
    count = 0
    index = -1
    filetype = ""
    for file in files:
        if file["filename"].split(".")[-1] in allowed_filetypes_no_attributes:
            new_index = allowed_filetypes_no_attributes.index(file["filename"].split(".")[-1])

            if index == -1:
                index = new_index
                filetype = allowed_filetypes_no_attributes[index]
            elif new_index != index:
                filetype = ""
                break

            count += 1

    return_data = {"count": count, "filetype": filetype}
    return return_data


def sanitize_folder_name(folder_name):
    valid_characters = re.sub(r'[<>:."/\\|?*]', "", folder_name)
    return valid_characters.strip()


def release_trackcount_mode(releases):
    track_count = {}

    for release in releases:
        trackcount = release["trackCount"]
        if trackcount in track_count:
            track_count[trackcount] += 1
        else:
            track_count[trackcount] = 1

    most_common_trackcount = None
    max_count = 0

    for trackcount, count in track_count.items():
        if count > max_count:
            max_count = count
            most_common_trackcount = trackcount

    return most_common_trackcount


def choose_release(artist_name, releases):
    most_common_trackcount = release_trackcount_mode(releases)

    # Prefer the release marked as monitored — this is the one the
    # user explicitly selected in the UI and represents the edition they want.
    for release in releases:
        if not release.get("monitored", False):
            continue
        country = release["country"][0] if release["country"] else None
        if release["format"][1] == "x" and cfg.allow_multi_disc:
            format_accepted = release["format"].split("x", 1)[1] in cfg.accepted_formats
        else:
            format_accepted = release["format"] in cfg.accepted_formats
        if format_accepted:
            logger.info(
                f"Selected monitored release for {artist_name}: {release['status']}, "
                f"{country}, {release['format']}, Mediums: {release['mediumCount']}, "
                f"Tracks: {release['trackCount']}, ID: {release['id']}"
            )
            return release

    for release in releases:
        country = release["country"][0] if release["country"] else None

        if release["format"][1] == "x" and cfg.allow_multi_disc:
            format_accepted = release["format"].split("x", 1)[1] in cfg.accepted_formats
        else:
            format_accepted = release["format"] in cfg.accepted_formats

        if cfg.use_most_common_tracknum:
            if release["trackCount"] == most_common_trackcount:
                track_count_bool = True
            else:
                track_count_bool = False
        else:
            track_count_bool = True

        if (cfg.skip_region_check or country in cfg.accepted_countries) and format_accepted and release["status"] == "Official" and track_count_bool:
            logger.info(
                ", ".join(
                    [
                        f"Selected release for {artist_name}: {release['status']}",
                        str(country),
                        release["format"],
                        f"Mediums: {release['mediumCount']}",
                        f"Tracks: {release['trackCount']}",
                        f"ID: {release['id']}",
                    ]
                )
            )

            return release

    if cfg.use_most_common_tracknum:
        for release in releases:
            if release["trackCount"] == most_common_trackcount:
                return release
        else:
            default_release = releases[0]

    else:
        default_release = releases[0]

    return default_release


def verify_filetype(file, allowed_filetype):
    current_filetype = file["filename"].split(".")[-1]
    bitdepth = None
    samplerate = None
    bitrate = None

    if "bitRate" in file:
        bitrate = file["bitRate"]
    if "sampleRate" in file:
        samplerate = file["sampleRate"]
    if "bitDepth" in file:
        bitdepth = file["bitDepth"]

    # Check if the types match up for the current files type and the current type from the config
    if current_filetype == allowed_filetype.split(" ")[0]:
        # Check if the current type from the config specifies other attributes than the filetype (bitrate etc)
        if " " in allowed_filetype:
            selected_attributes = allowed_filetype.split(" ")[1]
            # If it is a bitdepth/samplerate pair instead of a simple bitrate
            if "/" in selected_attributes:
                selected_bitdepth = selected_attributes.split("/")[0]
                try:
                    selected_samplerate = str(int(float(selected_attributes.split("/")[1]) * 1000))
                except (ValueError, IndexError):
                    logger.warning("Invalid samplerate in selected_attributes")
                    return False

                if bitdepth and samplerate:
                    if str(bitdepth) == str(selected_bitdepth) and str(samplerate) == str(selected_samplerate):
                        return True
                else:
                    return False
            # If it is a VBR quality preset (e.g. "mp3 v0", "mp3 v2")
            elif selected_attributes.lower() in ("v0", "v2"):
                if bitrate:
                    cbr_values = {128, 160, 192, 224, 256, 320}
                    is_vbr = bitrate not in cbr_values
                    # Prefer isVariableBitRate flag from slskd if available
                    if "isVariableBitRate" in file:
                        is_vbr = file["isVariableBitRate"]
                    if not is_vbr:
                        return False
                    if selected_attributes.lower() == "v0":
                        return 220 <= bitrate <= 280
                    else:  # v2
                        return 170 <= bitrate <= 220
                return False
            # If it is a minimum bitrate (e.g. "aac 256+", "ogg 256+", "opus 192+")
            elif selected_attributes.endswith("+"):
                try:
                    min_bitrate = int(selected_attributes[:-1])
                except ValueError:
                    logger.warning(f"Invalid minimum bitrate in allowed_filetype: {allowed_filetype}")
                    return False
                if bitrate:
                    return bitrate >= min_bitrate
                return False
            # If it is an exact bitrate
            else:
                selected_bitrate = selected_attributes
                if bitrate:
                    if str(bitrate) == str(selected_bitrate):
                        return True
                return False
        # If no bitrate or other info then it is a match so return true
        else:
            return True
    else:
        return False


def download_filter(allowed_filetype, directory: Any):
    """
    Filters the directory listing from SLSKD using the filetype whitelist.
    If not using the whitelist it will only return the audio files of the allowed filetype.
    This is to prevent downloading m3u,cue,txt,jpg,etc. files that are sometimes stored in
    the same folders as the music files.
    """
    logging.debug("download_filtering")
    if cfg.download_filtering:
        whitelist = []  # Init an empty list to take just the allowed_filetype
        if cfg.use_extension_whitelist:
            whitelist = list(cfg.extensions_whitelist)
        whitelist.append(allowed_filetype.split(" ")[0])
        unwanted = []
        logger.debug(f"Accepted extensions: {whitelist}")
        for file in directory["files"]:
            for extension in whitelist:
                if file["filename"].split(".")[-1].lower() == extension.lower():
                    break  # Jump out and don't add wanted files to the unwanted list
            else:
                unwanted.append(file["filename"])  # Add to list of files to remove from the wanted list
                logger.debug(f"Unwanted file: {file['filename']}")
        if len(unwanted) > 0:
            temp = []
            logger.debug(f"Unwanted Files: {unwanted}")
            for file in directory["files"]:
                if file["filename"] not in unwanted:
                    logger.debug(f"Added file to queue: {file['filename']}")
                    temp.append(file)  # Build the new list of files
            directory["files"] = temp
            for files in temp:
                logger.debug(f"File in final list: {files['filename']}")
            return directory  # Return the modified list
    return directory  # If we didn't find unwanted files or we aren't filtering just return the original list


def check_for_match(tracks, allowed_filetype, file_dirs, username):
    """
    Does the actual match checking on a single disk/album.
    """
    logger.debug(f"Current broken users {broken_user}")
    if username in broken_user:
        return False, {}, ""
    for file_dir in file_dirs:
        if username not in folder_cache:
            logger.debug(f"Add user to cache: {username}")
            folder_cache[username] = {}

        if file_dir not in folder_cache[username]:
            logger.info(f"User: {username} Folder: {file_dir} not in cache. Fetching from SLSKD")
            version = slskd.application.version()
            version_check = slskd_version_check(version)

            if not version_check:
                logger.info(f"Error checking slskd version number: {version}. Version check > 0.22.2: {version_check}. This would most likely be fixed by updating your slskd.")

            try:
                directory: Any
                if version_check:
                    directory = slskd.users.directory(username=username, directory=file_dir)[0]
                else:
                    directory = slskd.users.directory(username=username, directory=file_dir)
            except Exception:
                logger.exception(f'Error getting directory from user: "{username}"')
                broken_user.append(username)
                logger.debug(f"Updated broken users {broken_user}")
                return False, {}, ""
            folder_cache[username][file_dir] = copy.deepcopy(directory)
        else:
            logger.info(f"User: {username} Folder: {file_dir} in cache. Using cached value")
            directory = copy.deepcopy(folder_cache[username][file_dir])

        track_num = len(tracks)
        tracks_info = album_track_num(directory)

        if tracks_info["count"] == track_num and tracks_info["filetype"] != "":
            if album_match(tracks, directory["files"], username, allowed_filetype):
                if _track_titles_cross_check(tracks, directory["files"]):
                    return True, directory, file_dir
                else:
                    logger.warning(
                        f"Track title cross-check FAILED for user {username}, "
                        f"dir {file_dir} — skipping (wrong pressing?)"
                    )
                    continue
            else:
                continue
    return False, {}, ""


def is_blacklisted(title: str) -> bool:
    for word in cfg.title_blacklist:
        if word and word.lower() in title.lower():
            logger.info(f"Skipping {title} due to blacklisted word: {word}")
            return True
    return False


def filter_list(albums):
    """
    Helper to do all the various filtering in one go and in one place. Same net effect as the previous multi-stage approach
    Just neater and easier to work on.
    """
    list_to_download = []
    for album in albums:
        if is_blacklisted(album["title"]):
            logger.info(f"Skipping blacklisted album: {album['artist']['artistName']} - {album['title']} (ID: {album['id']}")
            continue
        else:
            list_to_download.append(album)

    if len(list_to_download) > 0:
        return list_to_download
    else:
        return None


def search_for_album(album):
    from lib.search import build_query

    album_title = album["title"]
    artist_name = album["artist"]["artistName"]
    album_id = album["id"]
    query = build_query(artist_name, album_title, prepend_artist=cfg.album_prepend_artist)

    if not query:
        logger.warning(f"Cannot build search query for '{artist_name} - {album_title}'")
        return False

    logger.info(f"Searching for album: {query} "
                f"(from '{artist_name} - {album_title}')")
    try:
        search = slskd.searches.search_text(
            searchText=query,
            searchTimeout=cfg.search_timeout,
            filterResponses=True,
            maximumPeerQueueLength=cfg.maximum_peer_queue,
            minimumPeerUploadSpeed=cfg.minimum_peer_upload_speed,
        )
    except Exception:
        logger.exception(f"Failed to perform search via SLSKD: {query}")
        return False

    # Add timeout here to increase reliability with Slskd. Sometimes it doesn't update search status fast enough. More of an issue with lots of historical searches in slskd
    time.sleep(5)
    start_time = time.time()
    while True:
        if slskd.searches.state(search["id"], False)["state"] != "InProgress":  # Added False here as we don't want the search results here. Just the state.
            break
        time.sleep(1)
        if (time.time() - start_time) > cfg.search_timeout:
            logger.error("Failed to perform search via SLSKD due to timeout on search results.")
            return False

    search_results = slskd.searches.search_responses(search["id"])  # We use this API call twice. Let's just cache it locally.
    logger.info(f"Search returned {len(search_results)} results")
    if cfg.delete_searches:
        slskd.searches.delete(search["id"])

    if not len(search_results) > 0:
        return False

    if album_id not in search_cache:
        search_cache[album_id] = {}  # This is so we can check for matches we missed or if a user goes offline during our download

    for result in search_results:  # Switching to cached version. One less API call
        username = result["username"]
        if username not in search_cache[album_id]:
            # If we don't currently have a cache for a user set one up
            search_cache[album_id][username] = {}
        # Cache upload speed for sorting — prefer faster peers
        speed = result.get("uploadSpeed", 0)
        if speed and (username not in user_upload_speed or speed > user_upload_speed[username]):
            user_upload_speed[username] = speed
        logger.info(f"Caching and truncating results for user: {username}")
        init_files = result["files"]  # init_files short for initial files. Before truncating
        # Search the returned files and only cache files that are of the allowed_filetypes
        for file in init_files:
            file_dir = file["filename"].rsplit("\\", 1)[0]  # split dir/filenames on \
            for allowed_filetype in cfg.allowed_filetypes:
                if verify_filetype(file, allowed_filetype):  # Check the filename for an allowed type
                    if allowed_filetype not in search_cache[album_id][username]:
                        search_cache[album_id][username][allowed_filetype] = []  # Init the cache for this allowed filetype
                    if file_dir not in search_cache[album_id][username][allowed_filetype]:
                        search_cache[album_id][username][allowed_filetype].append(file_dir)
    return True


def _get_denied_users(album_id):
    """Get denied users from pipeline DB source_denylist."""
    denied = set()
    request_id = abs(album_id)
    try:
        db = pipeline_db_source._get_db()
        denied.update(e["username"] for e in db.get_denylisted_users(request_id))
    except Exception:
        pass
    return denied


def try_enqueue(all_tracks, results, allowed_filetype):
    """
    Single album match and enqueue.
    Iterates over all users and enqueues a found match
    """
    album_id = all_tracks[0]["albumId"]
    denied_users = _get_denied_users(album_id)
    # Sort users by upload speed (fastest first) for quicker downloads
    sorted_users = sorted(results.keys(), key=lambda u: user_upload_speed.get(u, 0), reverse=True)
    for username in sorted_users:
        if username in denied_users:
            logger.info(f"Skipping user '{username}' for album ID {album_id}: denylisted (previously provided mislabeled quality)")
            continue
        if allowed_filetype not in results[username]:
            continue
        logger.debug(f"Parsing result from user: {username}")
        file_dirs = results[username][allowed_filetype]
        found, directory, file_dir = check_for_match(all_tracks, allowed_filetype, file_dirs, username)
        if found:
            directory = download_filter(allowed_filetype, directory)
            for i in range(0, len(directory["files"])):
                directory["files"][i]["filename"] = file_dir + "\\" + directory["files"][i]["filename"]
            try:
                downloads = slskd_do_enqueue(username=username, files=directory["files"], file_dir=file_dir)
                if downloads is not None:
                    return True, downloads
                else:
                    album = get_album_by_id(all_tracks[0]["albumId"])
                    album_name = album["title"]
                    artist_name = album["artist"]["artistName"]
                    logger.info(f"Failed to enqueue download to slskd for {artist_name} - {album_name} from {username}")
            except Exception as e:
                album = get_album_by_id(all_tracks[0]["albumId"])
                album_name = album["title"]
                artist_name = album["artist"]["artistName"]

                logger.warning(f"Exception enqueueing tracks: {e}")
                logger.info(f"Exception enqueueing download to slskd for {artist_name} - {album_name} from {username}")
    album = get_album_by_id(all_tracks[0]["albumId"])
    album_name = album["title"]
    artist_name = album["artist"]["artistName"]
    logger.info(f"Failed to enqueue {artist_name} - {album_name}")
    return False, None


def try_multi_enqueue(release, all_tracks, results, allowed_filetype):
    """
    This is the multi-disk/media path for locating and enqueueing an album
    It does a flat search first. Then it does a split search.
    Otherwise it's basically the same as the single album search.
    """
    split_release = []
    tmp_results = copy.deepcopy(results)
    for media in release["media"]:
        disk = {}
        disk["source"] = None
        disk["tracks"] = []
        disk["disk_no"] = media["mediumNumber"]
        disk["disk_count"] = len(release["media"])
        for track in all_tracks:
            if track["mediumNumber"] == media["mediumNumber"]:
                disk["tracks"].append(track)
        split_release.append(disk)
    total = len(split_release)
    count_found = 0
    album_id = all_tracks[0]["albumId"]
    denied_users = _get_denied_users(album_id)
    for disk in split_release:
        for username in tmp_results:
            if username in denied_users:
                logger.info(f"Skipping user '{username}' for album ID {album_id} (multi-disc): denylisted (previously provided mislabeled quality)")
                continue
            if allowed_filetype not in tmp_results[username]:
                continue
            file_dirs = results[username][allowed_filetype]
            found, directory, file_dir = check_for_match(disk["tracks"], allowed_filetype, file_dirs, username)
            if found:
                directory = download_filter(allowed_filetype, directory)
                disk["source"] = (username, directory, file_dir)
                count_found += 1
                break
        else:
            return (
                False,
                None,
            )  # Only runs if we complete the loop without finding a source for the current disk regardless of how many other disks we located. All or nothing.
    if count_found == total:
        all_downloads = []
        enqueued = 0
        for disk in split_release:
            username, directory, file_dir = disk["source"]
            for i in range(0, len(directory["files"])):
                directory["files"][i]["filename"] = file_dir + "\\" + directory["files"][i]["filename"]
            try:
                downloads = slskd_do_enqueue(username=username, files=directory["files"], file_dir=file_dir)
                if downloads is not None:
                    for file in downloads:
                        file.disk_no = disk["disk_no"]
                        file.disk_count = disk["disk_count"]
                    all_downloads.extend(downloads)
                    enqueued += 1
                else:
                    album = get_album_by_id(all_tracks[0]["albumId"])
                    album_name = album["title"]
                    artist_name = album["artist"]["artistName"]
                    logger.info(f"Failed to enqueue download to slskd for {artist_name} - {album_name} from {username}")
                    # Delete ALL other downloads in all_downloads list
                    if len(all_downloads) > 0:
                        cancel_and_delete(all_downloads)
                        return False, None
            except Exception:
                album = get_album_by_id(all_tracks[0]["albumId"])
                album_name = album["title"]
                artist_name = album["artist"]["artistName"]

                logger.exception("Exception enqueueing tracks")
                logger.info(f"Exception enqueueing download to slskd for {artist_name} - {album_name} from {username}")
                # Delete all other downloads in all_downloads list
                if len(all_downloads) > 0:
                    cancel_and_delete(all_downloads)
                    return False, None
        if enqueued == total:
            return True, all_downloads
        else:
            # Delete all other downloads
            if len(all_downloads) > 0:
                cancel_and_delete(all_downloads)
            return False, None

    else:
        return False, None


def get_album_tracks(album, release_id=None):
    """Get tracks for an album from pipeline DB."""
    return pipeline_db_source.get_tracks(album)


# Cache for current album being processed
_current_album_cache = {}


def get_album_by_id(album_id):
    """Get album data by ID from cache."""
    if album_id in _current_album_cache:
        return _current_album_cache[album_id]
    raise KeyError(f"Album {album_id} not found in cache")


def find_download(album, grab_list):
    """
    This does the main loop over search results and user directories
    It has two paths it can take. One is the "single album" path
    The other is the multi-media path.
    """
    album_id = album["id"]
    artist_name = album["artist"]["artistName"]
    artist_id = album["artistId"]
    results = search_cache[album_id]

    # Cache album so get_album_by_id() works during matching
    _current_album_cache[album_id] = album

    filetypes_to_try = cfg.allowed_filetypes

    # Per-album quality override from pipeline DB (e.g. upgrade requests)
    quality_override = album.get("_db_quality_override")
    if quality_override:
        filetypes_to_try = [ft.strip() for ft in quality_override.split(",")]
        logger.info(
            f"Quality override for {artist_name} - {album['title']}: "
            f"searching only {filetypes_to_try}"
        )

    for allowed_filetype in filetypes_to_try:
        logger.info(f"Checking for Quality: {allowed_filetype}")
        releases = list(album.get("releases", []))

        # Check if any release is explicitly monitored by the user.
        has_monitored = any(r.get("monitored", False) for r in releases)

        num_releases = len(releases)
        for _ in range(0, num_releases):
            if len(releases) == 0:
                break
            release = choose_release(artist_name, releases)
            releases.remove(release)
            release_id = release["id"]
            all_tracks = get_album_tracks(album, release_id=release_id)
            if not all_tracks:
                logger.warning(f"No tracks for {artist_name} - {album['title']} (release {release_id}) — skipping")
                continue
            found, downloads = try_enqueue(all_tracks, results, allowed_filetype)

            if found:
                assert downloads is not None
                grab_list[album_id] = GrabListEntry(
                    album_id=album_id,
                    files=downloads,
                    filetype=allowed_filetype,
                    title=album["title"],
                    artist=artist_name,
                    year=album["releaseDate"][0:4],
                    mb_release_id=release.get("foreignReleaseId", ""),

                    db_request_id=album.get("_db_request_id"),
                    db_source=album.get("_db_source"),
                    db_quality_override=album.get("_db_quality_override"),
                )
                return True
            elif len(release["media"]) > 1:
                found, downloads = try_multi_enqueue(release, all_tracks, results, allowed_filetype)
                if found:
                    assert downloads is not None
                    grab_list[album_id] = GrabListEntry(
                        album_id=album_id,
                        files=downloads,
                        filetype=allowed_filetype,
                        title=album["title"],
                        artist=artist_name,
                        year=album["releaseDate"][0:4],
                        mb_release_id=release.get("foreignReleaseId", ""),
                        db_request_id=album.get("_db_request_id"),
                        db_source=album.get("_db_source"),
                        db_quality_override=album.get("_db_quality_override"),
                    )
                    return True

            # If a monitored release was tried and didn't match, stop here.
            if has_monitored and not release.get("monitored", False):
                # We already tried the monitored release (choose_release
                # returns it first) and we're now on a non-monitored one.
                # This shouldn't happen because we break below, but guard
                # against it defensively.
                break
            if has_monitored and release.get("monitored", False):
                # The monitored release was tried and didn't match.
                # Skip remaining releases for this quality tier.
                logger.info(
                    f"Monitored release ({release['trackCount']} tracks) not found on "
                    f"Soulseek for {artist_name} - {album['title']} at quality "
                    f"{allowed_filetype}, skipping non-monitored releases"
                )
                break
    return False


def search_and_queue(albums):
    grab_list = {}
    failed_grab = []
    failed_search = []
    total = len(albums)
    for i, album in enumerate(albums, 1):
        logger.info(f"Album {i}/{total}: {album['artist']['artistName']} - {album['title']}")
        if search_for_album(album):
            if not find_download(album, grab_list):
                failed_grab.append(album)

        else:
            failed_search.append(album)
    return grab_list, failed_search, failed_grab


from lib.grab_list import GrabListEntry
from lib.quality import DownloadInfo, SpectralContext

from lib.import_dispatch import (_populate_dl_info_from_import_result,
                                 _build_download_info as _build_download_info_impl,
                                 _check_quality_gate as _check_quality_gate_impl)

from lib.download import (cancel_and_delete as _cancel_and_delete_impl,
                          slskd_download_status as _slskd_download_status_impl,
                          slskd_do_enqueue as _slskd_do_enqueue_impl,
                          downloads_all_done,
                          _gather_spectral_context as _gather_spectral_context_impl,
                          process_completed_album as _process_completed_album_impl,
                          monitor_downloads as _monitor_downloads_impl,
                          grab_most_wanted as _grab_most_wanted_impl)


def _make_ctx():
    """Build a SoularrContext from module globals (bridge during migration)."""
    from lib.context import SoularrContext
    return SoularrContext(cfg=cfg, slskd=slskd, pipeline_db_source=pipeline_db_source)


def _gather_spectral_context(album_data, import_folder: str) -> SpectralContext:
    return _gather_spectral_context_impl(album_data, import_folder, _make_ctx())


def _check_quality_gate(album_data, request_id):
    _check_quality_gate_impl(album_data, request_id, _make_ctx())


def _build_download_info(album_data) -> DownloadInfo:
    return _build_download_info_impl(album_data)


def cancel_and_delete(files):
    _cancel_and_delete_impl(files, _make_ctx())


def slskd_download_status(downloads):
    return _slskd_download_status_impl(downloads, _make_ctx())


def slskd_do_enqueue(username, files, file_dir):
    return _slskd_do_enqueue_impl(username, files, file_dir, _make_ctx())


def process_completed_album(album_data: GrabListEntry, failed_grab):
    _process_completed_album_impl(album_data, failed_grab, _make_ctx())


def monitor_downloads(grab_list, failed_grab):
    _monitor_downloads_impl(grab_list, failed_grab, _make_ctx())


def grab_most_wanted(albums):
    ctx = _make_ctx()
    return _grab_most_wanted_impl(albums, search_and_queue, ctx)


def move_failed_import(src_path) -> "str | None":
    """Move a failed import to the failed_imports directory. Returns the absolute destination path."""
    failed_imports_dir = os.path.join(os.getcwd(), "failed_imports")

    if not os.path.exists(failed_imports_dir):
        os.makedirs(failed_imports_dir)

    folder_name = os.path.basename(src_path)
    target_path = os.path.join(failed_imports_dir, folder_name)

    counter = 1
    while os.path.exists(target_path):
        target_path = os.path.join(failed_imports_dir, f"{folder_name}_{counter}")
        counter += 1

    if os.path.exists(folder_name):
        shutil.move(folder_name, target_path)
        logger.info(f"Failed import moved to: {target_path}")
        return target_path
    return None


def _normalize_title(s):
    """Normalize a title for comparison: lowercase, strip punctuation, collapse whitespace."""
    s = unicodedata.normalize("NFKD", s)
    s = s.lower().strip()
    s = re.sub(r"[''`]", "'", s)
    s = re.sub(r"[^\w\s'&]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _extract_title_from_filename(filename):
    """Extract a track title from a Soulseek filename.

    Strips: extension, leading track numbers, artist prefixes.
    Returns normalized title via _normalize_title().

    Examples:
        "01 - Enter Sandman.mp3" → "enter sandman"
        "01_Enter_Sandman.flac" → "enter sandman"
        "Metallica - 01 - Enter Sandman.mp3" → "enter sandman"
        "Enter Sandman.mp3" → "enter sandman"
    """
    # Strip extension
    name = re.sub(r'\.[a-zA-Z0-9]{2,4}$', '', filename)
    # Replace underscores with spaces
    name = name.replace('_', ' ')
    # Strip leading "Artist - " prefix (before track number)
    # Pattern: "Something - 01 - Title" → "01 - Title"
    name = re.sub(r'^.+?\s*-\s*(?=\d{1,2}\s*[-.\s])', '', name)
    # Strip leading track number patterns: "01 - ", "01. ", "01 ", "1 - ", etc.
    name = re.sub(r'^\d{1,3}\s*[-._)\s]+\s*', '', name)
    # Strip leading "Artist - " if still present (e.g., "Artist - Title")
    # Only strip if there's content after the dash
    if ' - ' in name:
        parts = name.split(' - ', 1)
        # Heuristic: if the part before dash is short-ish, it might be artist
        # But if after dash is empty, keep the whole thing
        if len(parts) == 2 and parts[1].strip():
            name = parts[1]
    return _normalize_title(name)


def _track_titles_cross_check(expected_tracks, slskd_files):
    """Cross-check that Soulseek filenames match expected track titles.

    This catches wrong pressings with different tracklists (e.g., Weezer Blue vs Green).
    Runs AFTER album_match() passes (track count + fuzzy filename already verified).

    Returns True if enough titles match, False if too many are missing.
    Tolerance: up to 1/5 tracks can mismatch (same as _tracks_are_trivial_match).
    """
    if not expected_tracks or not slskd_files:
        return True  # Nothing to check

    # Extract and normalize expected titles
    expected = [_normalize_title(t.get("title", "")) for t in expected_tracks]

    # Extract and normalize titles from Soulseek filenames
    slskd_titles = [_extract_title_from_filename(f.get("filename", "")) for f in slskd_files]

    # For each expected title, find the best fuzzy match among Soulseek titles
    mismatches = 0
    for exp_title in expected:
        if not exp_title:
            continue
        best_ratio = 0.0
        for slskd_title in slskd_titles:
            if not slskd_title:
                continue
            # Check substring containment first (fast path)
            if exp_title in slskd_title or slskd_title in exp_title:
                best_ratio = 1.0
                break
            ratio = difflib.SequenceMatcher(None, exp_title, slskd_title).ratio()
            if ratio > best_ratio:
                best_ratio = ratio
        if best_ratio < 0.5:  # Lenient threshold — just catching obviously wrong tracks
            mismatches += 1

    max_allowed = max(1, len(expected) // 5)
    if mismatches > max_allowed:
        logger.info(f"CROSS-CHECK: {mismatches}/{len(expected)} tracks failed title match "
                    f"(max allowed: {max_allowed})")
        return False
    return True



def repair_mp3_headers(folder_path):
    """Run mp3val -f on all MP3 files to fix header issues before audio validation."""
    for f in os.listdir(folder_path):
        if not f.lower().endswith(".mp3"):
            continue
        filepath = os.path.join(folder_path, f)
        try:
            result = sp.run(["mp3val", "-f", filepath],
                            capture_output=True, text=True, timeout=60)
            if "FIXED" in result.stdout:
                logger.info(f"MP3VAL: fixed {f}")
        except FileNotFoundError:
            logger.warning("MP3VAL: mp3val not found on PATH — skipping header repair")
            return
        except sp.TimeoutExpired:
            logger.warning(f"MP3VAL: timeout on {f}")
        except Exception:
            logger.exception(f"MP3VAL: error on {f}")


def validate_audio(folder_path, mode="normal"):
    """Check audio integrity of downloaded files via ffmpeg full decode.

    mode: "strict" = any error rejects, "normal" = reject if >10% fail, "off" = skip.
    Returns: {"valid": bool, "error": str|None, "failed_files": list}
    """
    if mode == "off":
        return {"valid": True, "error": None, "failed_files": []}

    audio_exts = {"mp3", "flac", "m4a", "ogg", "opus", "wma", "aac", "alac", "wav"}
    files = []
    for f in os.listdir(folder_path):
        ext = f.rsplit(".", 1)[-1].lower() if "." in f else ""
        if ext in audio_exts:
            files.append(os.path.join(folder_path, f))

    if not files:
        return {"valid": True, "error": None, "failed_files": []}

    failed = []
    for filepath in files:
        try:
            result = sp.run(
                ["ffmpeg", "-v", "error", "-i", filepath, "-f", "null", "-"],
                capture_output=True, text=True, timeout=300
            )
            if result.returncode != 0 or result.stderr.strip():
                stderr = result.stderr.strip()
                # FLAC missing MD5: re-encode in place to fix, then re-test
                if filepath.lower().endswith(".flac") and "cannot check MD5 signature" in stderr:
                    logger.info(f"AUDIO_CHECK: fixing unset MD5: {os.path.basename(filepath)}")
                    fix = sp.run(
                        ["flac", "-f", "--verify", filepath],
                        capture_output=True, text=True, timeout=300,
                    )
                    if fix.returncode == 0:
                        retest = sp.run(
                            ["ffmpeg", "-v", "error", "-i", filepath, "-f", "null", "-"],
                            capture_output=True, text=True, timeout=300,
                        )
                        if retest.returncode == 0 and not retest.stderr.strip():
                            continue  # fixed and clean
                        stderr = retest.stderr.strip()
                    else:
                        stderr = f"MD5 fix failed: {fix.stderr.strip()[:150]}"
                err = stderr[:200]
                failed.append((os.path.basename(filepath), err))
        except sp.TimeoutExpired:
            failed.append((os.path.basename(filepath), "ffmpeg timeout"))
        except FileNotFoundError:
            logger.error("AUDIO_CHECK: ffmpeg not found on PATH — skipping audio validation")
            return {"valid": True, "error": None, "failed_files": []}

    if not failed:
        logger.info(f"AUDIO_CHECK: all {len(files)} files passed ({mode} mode)")
        return {"valid": True, "error": None, "failed_files": []}

    fail_pct = len(failed) / len(files)
    detail = "; ".join(f"{name}: {err}" for name, err in failed[:5])
    error_msg = f"{len(failed)}/{len(files)} files failed: {detail}"
    logger.warning(f"AUDIO_CHECK: {error_msg}")

    if mode == "strict":
        reject = True
    else:  # normal
        reject = fail_pct > 0.10 or any(len(err) > 500 for _, err in failed)

    if reject:
        logger.warning(f"AUDIO_CHECK: → REJECT ({mode} mode, {fail_pct:.0%} failed)")
        return {"valid": False, "error": error_msg, "failed_files": failed}
    else:
        logger.info(f"AUDIO_CHECK: → PASS ({mode} mode, {fail_pct:.0%} failed, below threshold)")
        return {"valid": True, "error": None, "failed_files": failed}


def beets_validate(album_path, mb_release_id, distance_threshold=0.15):
    """Thin wrapper — delegates to lib.beets.beets_validate()."""
    from lib.beets import beets_validate as _bv
    return _bv(cfg.beets_harness_path, album_path, mb_release_id, distance_threshold)


def stage_to_ai(album_data, source_path, staging_dir):
    """Move validated files from slskd download area to /AI/{Artist}/{Album}/."""
    artist_dir = sanitize_folder_name(album_data.artist)
    album_dir = sanitize_folder_name(album_data.title)
    dest = os.path.join(staging_dir, artist_dir, album_dir)
    os.makedirs(dest, exist_ok=True)

    for f in os.listdir(source_path):
        src = os.path.join(source_path, f)
        dst = os.path.join(dest, f)
        shutil.move(src, dst)

    shutil.rmtree(source_path, ignore_errors=True)
    return dest


def trigger_meelo_scan():
    """Trigger a Meelo library scan after import. Best-effort — failures don't block."""
    if not cfg.meelo_url:
        return
    try:
        # Get JWT
        login_data = json.dumps({"username": cfg.meelo_username, "password": cfg.meelo_password}).encode()
        login_req = urllib.request.Request(
            f"{cfg.meelo_url}/api/auth/login",
            data=login_data,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(login_req, timeout=10) as resp:
            jwt = json.loads(resp.read())["access_token"]

        # Trigger scan of beets library only
        scan_req = urllib.request.Request(
            f"{cfg.meelo_url}/scanner/scan?library=beets",
            method="POST",
            headers={"Authorization": f"Bearer {jwt}"},
        )
        with urllib.request.urlopen(scan_req, timeout=10) as resp:
            resp.read()
        logger.info("MEELO: triggered beets library scan")
    except Exception as e:
        logger.warning(f"MEELO: scan trigger failed: {e}")


def log_validation_result(album_data, result, dest_path=None):
    """Append beets validation result to tracking JSONL."""
    entry = {
        "timestamp": datetime.now().isoformat(),
        "artist": album_data.artist,
        "album": album_data.title,
        "mb_release_id": album_data.mb_release_id,
        "album_id": album_data.album_id,
        "status": "staged" if result["valid"] else "rejected",
        "scenario": result.get("scenario", ""),
        "distance": result.get("distance"),
        "detail": result.get("detail", ""),
        "dest_path": dest_path,
        "error": result.get("error"),
    }
    try:
        with open(cfg.beets_tracking_file, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        logger.exception(f"Failed to write beets tracking entry")


def is_docker():
    return os.getenv("IN_DOCKER") is not None


def slskd_version_check(version, target="0.22.2"):
    version_tuple = tuple(map(int, version.split(".")[:3]))
    target_tuple = tuple(map(int, target.split(".")[:3]))
    return version_tuple > target_tuple


def setup_logging(config):
    if "Logging" in config:
        log_config = config["Logging"]
    else:
        log_config = DEFAULT_LOGGING_CONF
    logging.basicConfig(**log_config)  # type: ignore


def load_search_denylist(file_path):
    if not os.path.exists(file_path):
        return {}

    try:
        with open(file_path, "r") as file:
            return json.load(file)
    except (json.JSONDecodeError, IOError) as ex:
        logger.warning(f"Error loading search denylist: {ex}. Starting with empty denylist.")
        return {}


def save_search_denylist(file_path, denylist):
    try:
        with open(file_path, "w") as file:
            json.dump(denylist, file, indent=2)
    except IOError as ex:
        logger.error(f"Error saving search denylist: {ex}")


def is_search_denylisted(denylist, album_id, max_failures):
    album_key = str(album_id)
    if album_key in denylist:
        return denylist[album_key]["failures"] >= max_failures
    return False


def update_search_denylist(denylist, album_id, success):
    album_key = str(album_id)
    current_datetime = datetime.now()
    current_datetime_str = current_datetime.strftime("%Y-%m-%dT%H:%M:%S")

    if success:
        if album_key in denylist:
            logger.info("Removing album from denylist: %s", denylist[album_key]["album_id"])
            del denylist[album_key]
    else:
        logger.info("Adding album to denylist: " + album_key)
        if album_key in denylist:
            denylist[album_key]["failures"] += 1
            denylist[album_key]["last_attempt"] = current_datetime_str
        else:
            denylist[album_key] = {
                "failures": 1,
                "last_attempt": current_datetime_str,
                "album_id": album_id,
            }


def main():
    global \
        cfg, \
        slskd, \
        config, \
        pipeline_db_source, \
        search_cache, \
        folder_cache, \
        user_upload_speed, \
        broken_user

    # Let's allow some overrides to be passed to the script
    parser = argparse.ArgumentParser(description="""Soularr downloads wanted albums from Soulseek via slskd""")

    default_data_directory = os.getcwd()

    if is_docker():
        default_data_directory = "/data"

    parser.add_argument(
        "-c",
        "--config-dir",
        default=default_data_directory,
        const=default_data_directory,
        nargs="?",
        type=str,
        help="Config directory (default: %(default)s)",
    )

    parser.add_argument(
        "-v",
        "--var-dir",
        default=default_data_directory,
        const=default_data_directory,
        nargs="?",
        type=str,
        help="Var directory (default: %(default)s)",
    )

    parser.add_argument(
        "--no-lock-file",
        action="store_false",
        dest="lock_file",
        default=True,
        help="Disable lock file creation",
    )

    args = parser.parse_args()

    lock_file_path = os.path.join(args.var_dir, ".soularr.lock")
    config_file_path = os.path.join(args.config_dir, "config.ini")

    if not is_docker() and os.path.exists(lock_file_path) and args.lock_file:
        logger.info(f"Soularr instance is already running.")
        sys.exit(1)

    try:
        if not is_docker() and args.lock_file:
            with open(lock_file_path, "w") as lock_file:
                lock_file.write("locked")

        # Disable interpolation to make storing logging formats in the config file much easier
        config = configparser.ConfigParser(interpolation=EnvInterpolation())

        if os.path.exists(config_file_path):
            config.read(config_file_path)
        else:
            if is_docker():
                logger.error(
                    'Config file does not exist! Please mount "/data" and place your "config.ini" file there. Alternatively, pass `--config-dir /directory/of/your/liking` as post arguments to store the config somewhere else.'
                )
                logger.error("See: https://github.com/mrusse/soularr/blob/main/config.ini for an example config file.")
            else:
                logger.error(
                    "Config file does not exist! Please place it in the working directory. Alternatively, pass `--config-dir /directory/of/your/liking` as post arguments to store the config somewhere else."
                )
                logger.error("See: https://github.com/mrusse/soularr/blob/main/config.ini for an example config file.")
            if os.path.exists(lock_file_path) and not is_docker():
                os.remove(lock_file_path)
            sys.exit(0)

        # --- Parse config into typed dataclass ---
        from lib.config import SoularrConfig
        cfg = SoularrConfig.from_ini(config, config_dir=args.config_dir, var_dir=args.var_dir)

        setup_logging(config)

        if cfg.beets_validation_enabled:
            logger.info(f"Beets validation ENABLED: harness={cfg.beets_harness_path}, "
                        f"threshold={cfg.beets_distance_threshold}, staging={cfg.beets_staging_dir}")

        from album_source import DatabaseSource
        pipeline_db_source = DatabaseSource(cfg.pipeline_db_dsn)
        logger.info(f"Pipeline DB: {cfg.pipeline_db_dsn}")

        if cfg.meelo_url:
            logger.info(f"Meelo post-import scan ENABLED: {cfg.meelo_url}")

        # Init directory cache. The wide search returns all the data we need. This prevents us from hammering the users on the Soulseek network
        search_cache = {}
        folder_cache = {}
        user_upload_speed = {}
        broken_user = []

        slskd = slskd_api.SlskdClient(host=cfg.slskd_host_url, api_key=cfg.slskd_api_key, url_base=cfg.slskd_url_base)
        wanted_records = []

        logger.info("Getting wanted records from pipeline DB...")
        wanted_records = pipeline_db_source.get_wanted(limit=cfg.page_size)
        logger.info(f"Pipeline DB: {len(wanted_records)} wanted record(s)")

        if len(wanted_records) > 0:
            try:
                filtered = filter_list(wanted_records)
                if filtered is not None:
                    failed = grab_most_wanted(filtered)
                else:
                    failed = 0
                    logger.info("No releases wanted that aren't on the deny list and/or blacklisted")
            except Exception:
                logger.exception("Fatal error! Exiting...")

                if os.path.exists(lock_file_path) and not is_docker():
                    os.remove(lock_file_path)
                sys.exit(0)
            if failed == 0:
                logger.info("Soularr finished. Exiting...")
                slskd.transfers.remove_completed_downloads()
            else:
                logger.info(f"{failed}: releases failed to find a match in the search results and are still wanted.")
                slskd.transfers.remove_completed_downloads()
        else:
            logger.info("No releases wanted. Exiting...")

    finally:
        # Clean up pipeline DB connection
        if pipeline_db_source is not None:
            try:
                pipeline_db_source.close()
            except Exception:
                pass
        # Remove the lock file after activity is done
        if os.path.exists(lock_file_path) and not is_docker():
            os.remove(lock_file_path)


if __name__ == "__main__":
    main()
