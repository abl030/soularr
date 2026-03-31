"""Tests for lib/util.py — pure utility functions extracted from soularr.py."""

import json
import os
import shutil
import tempfile
import unittest
from unittest.mock import patch, MagicMock


class TestSanitizeFolderName(unittest.TestCase):

    def test_strips_invalid_chars(self):
        from lib.util import sanitize_folder_name
        self.assertEqual(sanitize_folder_name('AC/DC - Back:In "Black"'),
                         'ACDC - BackIn Black')

    def test_preserves_valid_name(self):
        from lib.util import sanitize_folder_name
        self.assertEqual(sanitize_folder_name("Radiohead - OK Computer (1997)"),
                         "Radiohead - OK Computer (1997)")

    def test_strips_trailing_whitespace(self):
        from lib.util import sanitize_folder_name
        self.assertEqual(sanitize_folder_name("Album Name   "), "Album Name")


class TestMoveFailedImport(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.orig_cwd = os.getcwd()
        os.chdir(self.tmpdir)

    def tearDown(self):
        os.chdir(self.orig_cwd)
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_moves_to_failed_imports(self):
        from lib.util import move_failed_import
        src = os.path.join(self.tmpdir, "Artist - Album (2020)")
        os.makedirs(src)
        open(os.path.join(src, "track.mp3"), "w").close()
        result = move_failed_import(src)
        self.assertIsNotNone(result)
        assert result is not None
        self.assertIn("failed_imports", result)
        self.assertTrue(os.path.isdir(result))
        self.assertFalse(os.path.exists(src))

    def test_dedup_suffix(self):
        from lib.util import move_failed_import
        folder_name = "Artist - Album (2020)"
        # Create existing failed_imports entry
        failed_dir = os.path.join(self.tmpdir, "failed_imports")
        os.makedirs(os.path.join(failed_dir, folder_name))
        # Create source
        src = os.path.join(self.tmpdir, folder_name)
        os.makedirs(src)
        open(os.path.join(src, "track.mp3"), "w").close()
        result = move_failed_import(src)
        self.assertIsNotNone(result)
        assert result is not None
        self.assertTrue(result.endswith("_1"))

    def test_missing_source_returns_none(self):
        from lib.util import move_failed_import
        result = move_failed_import("/nonexistent/path/album")
        self.assertIsNone(result)


class TestStageToAi(unittest.TestCase):

    def test_moves_files_to_staging(self):
        from lib.util import stage_to_ai
        tmpdir = tempfile.mkdtemp()
        try:
            source = os.path.join(tmpdir, "source")
            staging = os.path.join(tmpdir, "staging")
            os.makedirs(source)
            os.makedirs(staging)
            open(os.path.join(source, "track1.mp3"), "w").close()
            open(os.path.join(source, "track2.mp3"), "w").close()

            album_data = MagicMock()
            album_data.artist = "Artist"
            album_data.title = "Album"

            dest = stage_to_ai(album_data, source, staging)
            self.assertTrue(os.path.isdir(dest))
            self.assertTrue(os.path.exists(os.path.join(dest, "track1.mp3")))
            self.assertTrue(os.path.exists(os.path.join(dest, "track2.mp3")))
            self.assertFalse(os.path.exists(source))
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)


class TestRepairMp3Headers(unittest.TestCase):

    def test_calls_mp3val_on_mp3_files(self):
        from lib.util import repair_mp3_headers
        tmpdir = tempfile.mkdtemp()
        try:
            open(os.path.join(tmpdir, "track.mp3"), "w").close()
            open(os.path.join(tmpdir, "cover.jpg"), "w").close()
            with patch("lib.util.sp.run") as mock_run:
                mock_run.return_value = MagicMock(stdout="OK", returncode=0)
                repair_mp3_headers(tmpdir)
                # Should only be called for .mp3 files
                self.assertEqual(mock_run.call_count, 1)
                call_args = mock_run.call_args[0][0]
                self.assertEqual(call_args[0], "mp3val")
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_no_mp3val_graceful(self):
        from lib.util import repair_mp3_headers
        tmpdir = tempfile.mkdtemp()
        try:
            open(os.path.join(tmpdir, "track.mp3"), "w").close()
            with patch("lib.util.sp.run", side_effect=FileNotFoundError):
                # Should not raise
                repair_mp3_headers(tmpdir)
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)


class TestDenylist(unittest.TestCase):

    def test_round_trip(self):
        from lib.util import (load_search_denylist, save_search_denylist,
                              update_search_denylist, is_search_denylisted)
        tmpfile = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        tmpfile.close()
        try:
            dl = load_search_denylist(tmpfile.name)
            self.assertEqual(dl, {})
            update_search_denylist(dl, 42, success=False)
            self.assertEqual(dl["42"]["failures"], 1)
            save_search_denylist(tmpfile.name, dl)
            dl2 = load_search_denylist(tmpfile.name)
            self.assertEqual(dl2["42"]["failures"], 1)
        finally:
            os.unlink(tmpfile.name)

    def test_threshold(self):
        from lib.util import is_search_denylisted, update_search_denylist
        dl = {}
        update_search_denylist(dl, 1, success=False)
        self.assertFalse(is_search_denylisted(dl, 1, max_failures=3))
        update_search_denylist(dl, 1, success=False)
        update_search_denylist(dl, 1, success=False)
        self.assertTrue(is_search_denylisted(dl, 1, max_failures=3))


if __name__ == "__main__":
    unittest.main()
