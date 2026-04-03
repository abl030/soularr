"""Tests for lib.manual_import — folder scanning, matching, and import execution."""

import json
import tempfile
import unittest
from unittest.mock import patch, MagicMock, ANY
from lib.manual_import import (
    FolderInfo,
    ImportRequest,
    ManualImportResult,
    import_result_failure_message,
    import_result_log_fields,
    parse_folder_name,
    match_folders_to_requests,
    parse_import_result_stdout,
    run_manual_import,
)


class TestParseFolderName(unittest.TestCase):
    """Tests for extracting artist/album from unstructured folder names."""

    def test_artist_dash_album(self) -> None:
        result = parse_folder_name("The Mountain Goats - Deserters")
        self.assertEqual(result.artist, "The Mountain Goats")
        self.assertEqual(result.album, "Deserters")

    def test_album_with_year_in_parens(self) -> None:
        result = parse_folder_name("Deserters (2022)")
        self.assertEqual(result.album, "Deserters")
        self.assertEqual(result.artist, "")

    def test_artist_dash_year_dash_album(self) -> None:
        result = parse_folder_name("Doves - 2002 - The Last Broadcast")
        self.assertEqual(result.artist, "Doves")
        self.assertEqual(result.album, "The Last Broadcast")

    def test_artist_dash_bracketed_year_album(self) -> None:
        result = parse_folder_name("Four Tet - [2012] Pink {Hostess Entertainment}")
        self.assertEqual(result.artist, "Four Tet")
        self.assertIn("Pink", result.album)

    def test_scene_release(self) -> None:
        result = parse_folder_name("Courtney_Marie_Andrews-Valentine-WEB-2026-QUAVER")
        self.assertEqual(result.artist, "Courtney Marie Andrews")
        self.assertEqual(result.album, "Valentine")

    def test_plain_album_name(self) -> None:
        result = parse_folder_name("My Beautiful Dark Twisted Fantasy")
        self.assertEqual(result.album, "My Beautiful Dark Twisted Fantasy")
        self.assertEqual(result.artist, "")

    def test_empty_string(self) -> None:
        result = parse_folder_name("")
        self.assertEqual(result.artist, "")
        self.assertEqual(result.album, "")

    def test_year_prefix(self) -> None:
        result = parse_folder_name("1987 Sister")
        self.assertEqual(result.album, "Sister")
        self.assertEqual(result.artist, "")


class TestMatchFoldersToRequests(unittest.TestCase):
    """Tests for fuzzy matching folders against pipeline requests."""

    def _req(self, id: int, artist: str, album: str) -> ImportRequest:
        return ImportRequest(
            id=id,
            artist_name=artist,
            album_title=album,
            mb_release_id="mbid-" + str(id),
        )

    def test_exact_match(self) -> None:
        folders = [FolderInfo(name="Deserters (2022)", path="/tmp/Deserters (2022)",
                              artist="The Mountain Goats", album="Deserters",
                              file_count=107)]
        requests = [self._req(1, "The Mountain Goats", "Deserters")]
        matches = match_folders_to_requests(folders, requests)
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].folder.name, "Deserters (2022)")
        self.assertEqual(matches[0].request.id, 1)
        self.assertGreater(matches[0].score, 0.5)

    def test_no_match(self) -> None:
        folders = [FolderInfo(name="Random Album", path="/tmp/Random Album",
                              artist="", album="Random Album", file_count=10)]
        requests = [self._req(1, "The Mountain Goats", "Deserters")]
        matches = match_folders_to_requests(folders, requests)
        self.assertEqual(len(matches), 0)

    def test_multiple_requests_best_match(self) -> None:
        folders = [FolderInfo(name="Doves - 2002 - The Last Broadcast", path="/tmp/x",
                              artist="Doves", album="The Last Broadcast", file_count=12)]
        requests = [
            self._req(1, "Doves", "The Last Broadcast"),
            self._req(2, "Doves", "Lost Souls"),
            self._req(3, "The National", "Boxer"),
        ]
        matches = match_folders_to_requests(folders, requests)
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].request.id, 1)

    def test_scene_release_matches(self) -> None:
        folders = [FolderInfo(name="scene", path="/tmp/scene",
                              artist="Courtney Marie Andrews", album="Valentine",
                              file_count=10)]
        requests = [self._req(1, "Courtney Marie Andrews", "Valentine")]
        matches = match_folders_to_requests(folders, requests)
        self.assertEqual(len(matches), 1)

    def test_album_only_folder_matches_by_album_title(self) -> None:
        """Folder with no artist but matching album title should match."""
        folders = [FolderInfo(name="Deserters (2022)", path="/tmp/Deserters (2022)",
                              artist="", album="Deserters", file_count=107)]
        requests = [self._req(1, "The Mountain Goats", "Deserters")]
        matches = match_folders_to_requests(folders, requests)
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].request.id, 1)
        self.assertGreater(matches[0].score, 0.5)

    def test_empty_inputs(self) -> None:
        self.assertEqual(match_folders_to_requests([], []), [])


class TestParseImportResultStdout(unittest.TestCase):
    """Tests for extracting ImportResult JSON from import_one.py stdout."""

    def test_extracts_json(self) -> None:
        stdout = 'some log\n__IMPORT_RESULT__{"decision":"import","exit_code":0}\nmore log\n'
        result = parse_import_result_stdout(stdout)
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.decision, "import")
        self.assertEqual(result.exit_code, 0)

    def test_no_sentinel(self) -> None:
        result = parse_import_result_stdout("just some logs\n")
        self.assertIsNone(result)

    def test_malformed_json(self) -> None:
        result = parse_import_result_stdout("__IMPORT_RESULT__{bad json\n")
        self.assertIsNone(result)


class TestManualImportResult(unittest.TestCase):
    def test_success(self) -> None:
        r = ManualImportResult(success=True, exit_code=0, message="Imported OK")
        self.assertTrue(r.success)

    def test_failure(self) -> None:
        r = ManualImportResult(success=False, exit_code=2, message="beets failed",
                               import_result_json='{"decision":"error"}')
        self.assertFalse(r.success)
        self.assertIsNotNone(r.import_result_json)


class TestImportResultHelpers(unittest.TestCase):
    def test_failure_message_uses_import_result_error(self) -> None:
        ir = json.dumps({
            "version": 2,
            "exit_code": 2,
            "decision": "import_failed",
            "error": "Harness returned rc=2",
        })
        self.assertEqual(import_result_failure_message(ir, 2), "Harness returned rc=2")

    def test_failure_message_formats_downgrade(self) -> None:
        ir = json.dumps({
            "version": 2,
            "exit_code": 5,
            "decision": "downgrade",
            "new_measurement": {"min_bitrate_kbps": 239},
            "existing_measurement": {"min_bitrate_kbps": 320},
        })
        self.assertEqual(
            import_result_failure_message(ir, 5),
            "239kbps is not better than existing 320kbps",
        )

    def test_log_fields_extract_measurements(self) -> None:
        ir = json.dumps({
            "version": 2,
            "exit_code": 5,
            "decision": "downgrade",
            "new_measurement": {
                "min_bitrate_kbps": 239,
                "spectral_grade": "suspect",
                "spectral_bitrate_kbps": 192,
            },
            "existing_measurement": {"min_bitrate_kbps": 320},
        })
        fields = import_result_log_fields(ir)
        self.assertEqual(fields["bitrate"], 239000)
        self.assertEqual(fields["actual_min_bitrate"], 239)
        self.assertEqual(fields["spectral_grade"], "suspect")
        self.assertEqual(fields["spectral_bitrate"], 192)
        self.assertEqual(fields["existing_min_bitrate"], 320)


class TestRunManualImport(unittest.TestCase):
    @patch("lib.manual_import.subprocess.run")
    @patch("lib.manual_import.os.geteuid", return_value=1000)
    def test_non_root_uses_sudo(self, _mock_geteuid, mock_run) -> None:
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='__IMPORT_RESULT__{"decision":"import","exit_code":0}\n',
            stderr="",
        )
        with tempfile.TemporaryDirectory() as d:
            result = run_manual_import(
                request_id=1,
                mb_release_id="mbid-1",
                path=d,
                import_one_path="/tmp/import_one.py",
            )

        self.assertTrue(result.success)
        cmd = mock_run.call_args.args[0]
        self.assertEqual(cmd[:2], ["sudo", "-n"])
        self.assertEqual(cmd[2], ANY)

    @patch("lib.manual_import.subprocess.run")
    @patch("lib.manual_import.os.geteuid", return_value=0)
    def test_root_runs_import_directly(self, _mock_geteuid, mock_run) -> None:
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='__IMPORT_RESULT__{"decision":"import","exit_code":0}\n',
            stderr="",
        )
        with tempfile.TemporaryDirectory() as d:
            result = run_manual_import(
                request_id=1,
                mb_release_id="mbid-1",
                path=d,
                import_one_path="/tmp/import_one.py",
            )

        self.assertTrue(result.success)
        cmd = mock_run.call_args.args[0]
        self.assertNotEqual(cmd[0], "sudo")


if __name__ == "__main__":
    unittest.main()
