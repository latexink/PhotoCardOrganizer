from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from photocard.config import normalize_config
from photocard.structure_detection import detect_existing_structure


class StructureDetectionTests(unittest.TestCase):
    def test_detects_editable_media_date_hierarchy(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = root / "Videos" / "2024" / "2024-05-06" / "MVI_0001.MP4"
            second = root / "Videos" / "2025" / "2025-06-07" / "MVI_0002.MP4"
            for path in (first, second):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"video")
            config = normalize_config({})

            analysis = detect_existing_structure(root, config["media_rules"])

            self.assertEqual(2, analysis.matched_files)
            self.assertEqual(
                ["{media}", "{date:%Y}", "{date:%Y-%m-%d}"],
                analysis.rules["video"].segments,
            )
            self.assertFalse(analysis.truncated)

    def test_unknown_folder_names_are_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "Commissioned Work" / "Final Selects" / "clip.mov"
            path.parent.mkdir(parents=True)
            path.write_bytes(b"video")
            config = normalize_config({})

            analysis = detect_existing_structure(root, config["media_rules"])

            self.assertEqual(
                ["{source_dir:1}", "{source_dir:2}"],
                analysis.rules["video"].segments,
            )

    def test_deep_supported_hierarchy_exposes_every_editable_level(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = (
                root
                / "Videos"
                / "Archive"
                / "2026"
                / "2026-07-27"
                / "Project"
                / "Final"
                / "clip.mp4"
            )
            path.parent.mkdir(parents=True)
            path.write_bytes(b"video")
            config = normalize_config({})

            analysis = detect_existing_structure(root, config["media_rules"])

            self.assertEqual(6, analysis.deepest_level)
            self.assertEqual(6, len(analysis.rules["video"].levels))
            self.assertEqual(
                [
                    "{media}",
                    "{source_dir:2}",
                    "{date:%Y}",
                    "{date:%Y-%m-%d}",
                    "{source_dir:5}",
                    "{source_dir:6}",
                ],
                analysis.rules["video"].segments,
            )

    def test_preview_limit_only_reports_truncation_when_more_files_exist(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in ("one.mp4", "two.mp4"):
                (root / name).write_bytes(name.encode("ascii"))
            config = normalize_config({})

            exact = detect_existing_structure(
                root,
                config["media_rules"],
                max_files=2,
            )
            self.assertFalse(exact.truncated)
            self.assertEqual(2, exact.scanned_files)

            (root / "three.mp4").write_bytes(b"three")
            truncated = detect_existing_structure(
                root,
                config["media_rules"],
                max_files=2,
            )
            self.assertTrue(truncated.truncated)
            self.assertEqual(2, truncated.scanned_files)
            self.assertEqual(2, truncated.matched_files)

    def test_irrelevant_deep_folders_do_not_trigger_media_depth_warning(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "clip.mp4").write_bytes(b"video")
            irrelevant = root / "cache" / "one" / "two" / "three" / "four" / "five"
            irrelevant.mkdir(parents=True)
            (irrelevant / "notes.txt").write_text("unsupported", encoding="utf-8")
            config = normalize_config({})

            analysis = detect_existing_structure(root, config["media_rules"])

            self.assertEqual(0, analysis.deepest_level)
            self.assertEqual(1, analysis.matched_files)

    def test_disabled_media_classes_do_not_influence_analysis(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "clip.mp4").write_bytes(b"video")
            (root / "photo.jpg").write_bytes(b"photo")
            config = normalize_config({})
            config["media_rules"]["photo"]["enabled"] = False

            analysis = detect_existing_structure(root, config["media_rules"])

            self.assertEqual(1, analysis.matched_files)
            self.assertEqual(1, analysis.rules["video"].file_count)
            self.assertEqual(0, analysis.rules["photo"].file_count)


if __name__ == "__main__":
    unittest.main()
