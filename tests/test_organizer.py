from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from photocard.config import (
    build_client_settings,
    export_client_settings,
    import_client_settings,
    normalize_config,
    upsert_card_profile,
)
from photocard.discovery import (
    discover_cards,
    folder_import_source,
    write_card_identity,
)
from photocard.manifest import ImportManifest
from photocard.models import DecisionResult
from photocard.organizer import Organizer
from photocard.portable_log import PortableTransferSession


class OrganizerIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def config(self, card_root: Path, destination: Path, **overrides) -> dict:
        config = normalize_config(
            {
                "destination_root": str(destination),
                "identification": {
                    "auto_detect": False,
                    "configured_roots": [str(card_root)],
                },
                "monitor": {"settle_seconds": 0},
                "safety": {
                    "minimum_destination_free_percent": 0,
                    "minimum_destination_free_gb": 0,
                    "warn_source_free_percent": 0,
                },
                **overrides,
            }
        )
        return config

    def identify(self, card_root: Path, config: dict, action: str = "copy") -> None:
        card_root.mkdir(parents=True, exist_ok=True)
        identification = config["identification"]
        write_card_identity(
            card_root,
            identification["folder_name"],
            identification["identity_filename"],
            identification["history_folder_name"],
            "Canon R5 Card A",
            "canon-r5-a",
            action,
            ["DCIM"],
        )

    @staticmethod
    def jpeg(path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        image = Image.new("RGB", (16, 12), "#4f725d")
        exif = Image.Exif()
        exif[36867] = "2024:05:06 07:08:09"
        exif[271] = "Canon"
        exif[272] = "Canon EOS R5"
        exif[18246] = 4
        image.save(path, exif=exif)

    @staticmethod
    def make_old(path: Path) -> None:
        timestamp = datetime(2024, 5, 6, 7, 8, 9).timestamp()
        os.utime(path, (timestamp, timestamp))

    def test_root_identity_folder_is_discovered(self) -> None:
        card = self.base / "CARD"
        config = self.config(card, self.base / "library")
        self.identify(card, config)

        identity = card / ".photocard" / "identity.json"
        self.assertTrue(identity.is_file())
        self.assertTrue((card / ".photocard" / "transfers").is_dir())
        cards, errors = discover_cards(config)
        self.assertEqual([], errors)
        self.assertEqual(["canon-r5-a"], [item.card_id for item in cards])
        self.assertEqual(card.resolve(), cards[0].root)

    def test_conflict_history_pages_searches_and_bulk_reviews(self) -> None:
        library = self.base / "library"
        manifest = ImportManifest(library)
        for index in range(205):
            manifest.record_conflict(
                source_key=f"source-{index:04d}",
                card_id="legacy-library",
                source_path=self.base / "incoming" / f"IMG_{index:04d}.JPG",
                existing_path=library / "Photos" / f"IMG_{index:04d}.JPG",
                incoming_path=library
                / "Conflicts"
                / f"IMG_{index:04d}.JPG",
                conflict_type="different_content",
                resolution="conflict_folder",
            )

        first_page = manifest.conflicts("open", limit=200)
        second_page = manifest.conflicts("open", limit=200, offset=200)

        self.assertEqual(205, manifest.conflict_count("open"))
        self.assertEqual(200, len(first_page))
        self.assertEqual(5, len(second_page))
        self.assertTrue(
            {record["id"] for record in first_page}.isdisjoint(
                record["id"] for record in second_page
            )
        )
        matches = manifest.conflicts(
            "open", limit=200, search="IMG_0123"
        )
        self.assertEqual(1, len(matches))
        selected_ids = [int(record["id"]) for record in first_page[:25]]
        manifest.mark_conflicts_reviewed(selected_ids)
        self.assertEqual(180, manifest.conflict_count("open"))
        self.assertEqual(25, manifest.conflict_count("reviewed"))

    def test_identity_file_accepts_utf8_bom(self) -> None:
        card = self.base / "BOM_CARD"
        config = self.config(card, self.base / "library")
        self.identify(card, config)
        identity = card / ".photocard" / "identity.json"
        payload = identity.read_text(encoding="utf-8")
        identity.write_text(payload, encoding="utf-8-sig")

        cards, errors = discover_cards(config)

        self.assertEqual([], errors)
        self.assertEqual(["canon-r5-a"], [item.card_id for item in cards])

    def test_inaccessible_volume_probe_does_not_abort_discovery(self) -> None:
        card = self.base / "PROTECTED_CARD"
        card.mkdir()
        config = self.config(card, self.base / "library")

        with patch("photocard.discovery.Path.is_file", side_effect=PermissionError("denied")):
            cards, errors = discover_cards(config)

        self.assertEqual([], cards)
        self.assertEqual([], errors)

    def test_multiple_cards_create_independent_transfer_sessions(self) -> None:
        cards = [self.base / "CARD_A", self.base / "CARD_B"]
        destination = self.base / "multi-card-library"
        config = normalize_config(
            {
                "destination_root": str(destination),
                "identification": {
                    "auto_detect": False,
                    "configured_roots": [str(card) for card in cards],
                },
                "monitor": {"settle_seconds": 0},
                "safety": {
                    "minimum_destination_free_percent": 0,
                    "minimum_destination_free_gb": 0,
                    "warn_source_free_percent": 0,
                },
            }
        )
        identification = config["identification"]
        for index, card in enumerate(cards, 1):
            card.mkdir()
            write_card_identity(
                card,
                identification["folder_name"],
                identification["identity_filename"],
                identification["history_folder_name"],
                f"Camera Card {index}",
                f"camera-card-{index}",
                "copy",
                ["DCIM"],
            )
            source = card / "DCIM" / f"MVI_{index:04d}.MP4"
            source.parent.mkdir()
            source.write_bytes(f"video-{index}".encode("ascii"))
            self.make_old(source)

        results, errors = Organizer(config).scan_all()

        self.assertEqual([], errors)
        self.assertEqual(2, len(results))
        self.assertEqual([1, 1], [result.imported for result in results])
        for index, card in enumerate(cards, 1):
            self.assertEqual(1, len(list((card / ".photocard" / "transfers").rglob("*.jsonl"))))
            local = destination / ".photocard-organizer" / "transfer-records" / f"camera-card-{index}"
            self.assertEqual(1, len(list(local.rglob("*.jsonl"))))

    def test_destination_inside_card_is_blocked_before_transfer(self) -> None:
        card = self.base / "OVERLAP_CARD"
        destination = card / "Destination"
        config = self.config(card, destination)
        self.identify(card, config, action="move")
        source = card / "DCIM" / "MVI_0001.MP4"
        source.parent.mkdir()
        source.write_bytes(b"video")
        self.make_old(source)
        discovered, errors = discover_cards(config)

        result = Organizer(config).scan_card(discovered[0], allow_destructive=True)

        self.assertEqual([], errors)
        self.assertEqual(1, result.failed)
        self.assertIn("overlaps the source root", result.errors[0])
        self.assertTrue(source.is_file())
        self.assertEqual([], list((card / ".photocard" / "transfers").rglob("*.jsonl")))

    def test_mixed_media_copy_creates_one_session_and_is_shared(self) -> None:
        card = self.base / "CARD"
        destination = self.base / "library-one"
        config = self.config(card, destination)
        config["media_rules"]["photo"]["folder_segments"] = [
            "Photos", "{date:%Y}", "{camera}", "{rating}"
        ]
        config["media_rules"]["raw"]["folder_segments"] = ["RAW", "{date:%Y}"]
        config["media_rules"]["video"]["folder_segments"] = ["Videos", "{date:%Y}"]
        config["media_rules"]["sidecar"]["folder_segments"] = ["Sidecars", "{date:%Y}"]
        self.identify(card, config)

        dcim = card / "DCIM" / "100CANON"
        photo = dcim / "IMG_0001.JPG"
        self.jpeg(photo)
        raw = dcim / "IMG_0002.CR3"
        raw.write_bytes(b"raw-camera-data")
        video = dcim / "MVI_0003.MP4"
        video.write_bytes(b"video-camera-data")
        sidecar = dcim / "IMG_0004.XMP"
        sidecar.write_text(
            '<x:xmpmeta xmlns:x="adobe:ns:meta/" xmlns:xmp="http://ns.adobe.com/xap/1.0/">'
            '<xmp:Description xmp:Rating="3" xmp:CreateDate="2024-05-06T07:08:09" />'
            "</x:xmpmeta>",
            encoding="utf-8",
        )
        for path in (photo, raw, video, sidecar):
            self.make_old(path)

        results, errors = Organizer(config).scan_all()
        self.assertEqual([], errors)
        self.assertEqual(4, results[0].imported)
        self.assertTrue((destination / "Photos" / "2024" / "Canon EOS R5" / "4 stars" / photo.name).is_file())
        self.assertTrue((destination / "RAW" / "2024" / raw.name).is_file())
        self.assertTrue((destination / "Videos" / "2024" / video.name).is_file())
        self.assertTrue((destination / "Sidecars" / "2024" / sidecar.name).is_file())

        sessions = list((card / ".photocard" / "transfers").rglob("*.jsonl"))
        self.assertEqual(1, len(sessions))
        local_sessions = list(
            (destination / ".photocard-organizer" / "transfer-records" / "canon-r5-a").rglob("*.jsonl")
        )
        self.assertEqual(1, len(local_sessions))
        self.assertEqual(sessions[0].read_bytes(), local_sessions[0].read_bytes())
        records = [json.loads(line) for line in sessions[0].read_text(encoding="utf-8").splitlines()]
        self.assertEqual("session", records[0]["record_type"])
        self.assertEqual(4, sum(record.get("record_type") == "transfer" for record in records))

        second_config = self.config(
            card,
            self.base / "library-two",
            instance={"id": "second-computer", "name": "Laptop", "library_id": "second-library"},
        )
        second_results, _ = Organizer(second_config).scan_all()
        self.assertEqual(0, second_results[0].imported)
        self.assertEqual(4, second_results[0].skipped)

    def test_move_waits_for_confirmation_then_verifies_and_deletes(self) -> None:
        card = self.base / "MOVE_CARD"
        destination = self.base / "move-library"
        config = self.config(card, destination)
        config["safety"]["move_checksum_algorithm"] = "sha512"
        config["identification"]["checksum_filename_template"] = "{card}_{session}.{algorithm}"
        self.identify(card, config, action="move")
        source = card / "DCIM" / "IMG_0100.JPG"
        self.jpeg(source)
        self.make_old(source)
        discovered, _ = discover_cards(config)

        blocked = Organizer(config).scan_card(discovered[0], allow_destructive=False)
        self.assertTrue(blocked.pending_destructive_confirmation)
        self.assertEqual(1, blocked.blocked)
        self.assertTrue(source.exists())

        completed = Organizer(config).scan_card(discovered[0], allow_destructive=True)
        self.assertEqual(1, completed.imported)
        self.assertEqual(0, completed.failed)
        self.assertFalse(source.exists())
        self.assertEqual(1, len(list((card / ".photocard" / "transfers").rglob("*.jsonl"))))
        checksum_logs = list((card / ".photocard" / "transfers").rglob("*.sha512"))
        self.assertEqual(1, len(checksum_logs))
        checksum_lines = [
            line for line in checksum_logs[0].read_text(encoding="utf-8").splitlines() if not line.startswith("#")
        ]
        digest, relative_path = checksum_lines[0].split("  ", 1)
        self.assertEqual(128, len(digest))
        self.assertTrue(relative_path.endswith("IMG_0100.JPG"))
        local_checksum_logs = list(
            (destination / ".photocard-organizer" / "transfer-records" / "canon-r5-a").rglob("*.sha512")
        )
        self.assertEqual(1, len(local_checksum_logs))
        self.assertEqual(checksum_logs[0].read_bytes(), local_checksum_logs[0].read_bytes())

    def test_move_keeps_source_when_portable_history_cannot_be_written(self) -> None:
        card = self.base / "SAFE_MOVE_CARD"
        destination = self.base / "safe-move-library"
        config = self.config(card, destination)
        self.identify(card, config, action="move")
        source = card / "DCIM" / "IMG_0200.JPG"
        self.jpeg(source)
        self.make_old(source)
        discovered, _ = discover_cards(config)

        original_append = PortableTransferSession.append_verified

        def fail_only_portable(session, **kwargs):
            if session.history.history_dir == discovered[0].history_dir:
                raise OSError("read-only card")
            return original_append(session, **kwargs)

        with patch.object(
            PortableTransferSession,
            "append_verified",
            autospec=True,
            side_effect=fail_only_portable,
        ):
            result = Organizer(config).scan_card(discovered[0], allow_destructive=True)

        self.assertEqual(0, result.imported)
        self.assertEqual(1, result.blocked)
        self.assertTrue(source.exists())
        self.assertTrue(any("portable history" in warning for warning in result.warnings))
        copied_files = [
            path for path in destination.rglob("*.JPG") if ".photocard-organizer" not in path.parts
        ]
        self.assertEqual(1, len(copied_files))
        local_sessions = list(
            (destination / ".photocard-organizer" / "transfer-records" / "canon-r5-a").rglob("*.jsonl")
        )
        self.assertEqual(1, len(local_sessions))

    def test_filename_conflict_routes_to_review_without_pausing(self) -> None:
        card = self.base / "CONFLICT_CARD"
        destination = self.base / "conflict-library"
        config = self.config(card, destination)
        config["safety"]["conflict_policy"] = "ask"
        config["safety"]["manual_conflict_prompt"] = True
        self.identify(card, config)
        source = card / "DCIM" / "IMG_0300.JPG"
        self.jpeg(source)
        self.make_old(source)
        requested = destination / "Photos" / "2024" / "2024-05-06" / "Canon EOS R5" / source.name
        requested.parent.mkdir(parents=True, exist_ok=True)
        requested.write_bytes(b"different existing image")
        requests = []

        def decide(request):
            requests.append(request)
            return DecisionResult("stop")

        result, _ = Organizer(config, decision_callback=decide).scan_all()
        self.assertEqual(1, result[0].imported)
        self.assertEqual([], requests)
        self.assertTrue(requested.exists())
        relative = requested.relative_to(destination)
        conflict_copy = destination / "Conflicts" / relative
        self.assertTrue(conflict_copy.exists())
        conflicts = ImportManifest(destination).conflicts("open")
        self.assertEqual(1, len(conflicts))
        self.assertEqual("filename_conflict", conflicts[0]["conflict_type"])
        self.assertEqual("conflict_folder", conflicts[0]["resolution"])

    def test_configured_space_fallback_redirects_the_card(self) -> None:
        card = self.base / "SPACE_CARD"
        primary = self.base / "full-library"
        fallback = self.base / "fallback-library"
        config = self.config(card, primary)
        config["safety"]["space_policy"] = "fallback_then_block"
        config["safety"]["manual_space_prompt"] = False
        config["safety"]["fallback_destination_roots"] = [str(fallback)]
        self.identify(card, config)
        source = card / "DCIM" / "IMG_0400.JPG"
        self.jpeg(source)
        self.make_old(source)
        original_space_status = Organizer._destination_space_status

        def space_status(organizer, root, size):
            if Path(root) == primary:
                return False, "Configured reserve reached.", False
            return original_space_status(organizer, root, size)

        with patch.object(Organizer, "_destination_space_status", autospec=True, side_effect=space_status):
            results, _ = Organizer(config).scan_all()

        self.assertEqual(1, results[0].imported)
        self.assertEqual(1, len(list(fallback.rglob("IMG_0400.JPG"))))
        self.assertEqual(0, len(list(primary.rglob("IMG_0400.JPG"))))

    def test_destination_redirect_guard_reports_exhaustion(self) -> None:
        card = self.base / "REDIRECT_CARD"
        destination = self.base / "redirect-library"
        config = self.config(card, destination)
        self.identify(card, config)
        source = card / "DCIM" / "IMG_0450.JPG"
        self.jpeg(source)
        self.make_old(source)
        decisions = []

        def decide(_request):
            target = self.base / f"redirect-{len(decisions) + 1}"
            decisions.append(target)
            return DecisionResult("choose_destination", value=str(target))

        with (
            patch.object(
                Organizer,
                "_destination_space_status",
                return_value=(False, "Configured reserve reached.", False),
            ),
            patch("photocard.organizer.MAX_DESTINATION_REDIRECTS", 2),
        ):
            results, _ = Organizer(
                config,
                decision_callback=decide,
            ).scan_all()

        self.assertEqual(2, len(decisions))
        self.assertEqual(1, results[0].failed)
        self.assertTrue(
            any(
                "Stopped after 2 destination changes" in error
                for error in results[0].errors
            )
        )
        self.assertTrue(source.exists())

    def test_hard_space_limit_never_offers_continue(self) -> None:
        card = self.base / "HARD_SPACE_CARD"
        destination = self.base / "hard-space-library"
        config = self.config(card, destination)
        self.identify(card, config)
        source = card / "DCIM" / "IMG_0500.JPG"
        self.jpeg(source)
        self.make_old(source)

        def decide(request):
            self.assertEqual("space_hard", request.kind)
            self.assertNotIn("continue", {action for action, _label in request.options})
            return DecisionResult("skip")

        with patch.object(
            Organizer,
            "_destination_space_status",
            return_value=(False, "No physical free space.", True),
        ):
            results, _ = Organizer(config, decision_callback=decide).scan_all()

        self.assertEqual(0, results[0].imported)
        self.assertEqual(1, results[0].blocked)
        self.assertTrue(source.exists())

    def test_transient_copy_error_is_retried(self) -> None:
        card = self.base / "RETRY_CARD"
        destination = self.base / "retry-library"
        config = self.config(card, destination)
        config["safety"]["io_retry_count"] = 1
        config["safety"]["io_retry_delay_seconds"] = 0
        self.identify(card, config)
        source = card / "DCIM" / "IMG_0600.JPG"
        self.jpeg(source)
        self.make_old(source)
        original_copy = Organizer._copy_and_verify
        attempts = {"count": 0}

        def flaky_copy(organizer, source_path, destination_path, verification, expected_stat=None):
            attempts["count"] += 1
            if attempts["count"] == 1:
                raise OSError("temporary destination disconnect")
            return original_copy(
                organizer,
                source_path,
                destination_path,
                verification,
                expected_stat=expected_stat,
            )

        with patch.object(Organizer, "_copy_and_verify", autospec=True, side_effect=flaky_copy):
            results, _ = Organizer(config).scan_all()

        self.assertEqual(2, attempts["count"])
        self.assertEqual(1, results[0].imported)
        self.assertEqual(0, results[0].failed)

    def test_source_changed_during_copy_is_deferred_without_partial_output(self) -> None:
        card = self.base / "CHANGING_CARD"
        destination = self.base / "changing-library"
        config = self.config(card, destination)
        self.identify(card, config)
        source = card / "DCIM" / "IMG_0650.JPG"
        self.jpeg(source)
        self.make_old(source)
        original_copy = shutil.copy2

        def copy_then_mutate(source_path, destination_path, *args, **kwargs):
            result = original_copy(source_path, destination_path, *args, **kwargs)
            with Path(source_path).open("ab") as handle:
                handle.write(b"camera still writing")
            return result

        with patch("photocard.organizer.shutil.copy2", side_effect=copy_then_mutate):
            results, _ = Organizer(config).scan_all()

        self.assertEqual(0, results[0].imported)
        self.assertEqual(1, results[0].blocked)
        self.assertEqual(0, results[0].failed)
        self.assertTrue(source.exists())
        self.assertFalse(any(destination.rglob("IMG_0650.JPG")))

    def test_destination_created_during_copy_is_not_overwritten(self) -> None:
        card = self.base / "RACE_CARD"
        destination = self.base / "race-library"
        config = self.config(card, destination)
        config["safety"]["io_retry_delay_seconds"] = 0
        self.identify(card, config)
        source = card / "DCIM" / "IMG_0660.JPG"
        self.jpeg(source)
        self.make_old(source)
        original_copy = shutil.copy2
        attempts = {"count": 0}

        def copy_with_external_race(source_path, temporary_path, *args, **kwargs):
            result = original_copy(source_path, temporary_path, *args, **kwargs)
            attempts["count"] += 1
            if attempts["count"] == 1:
                Path(temporary_path).parent.joinpath(Path(source_path).name).write_bytes(
                    b"external process content"
                )
            return result

        with patch("photocard.organizer.shutil.copy2", side_effect=copy_with_external_race):
            results, _ = Organizer(config).scan_all()

        imported_files = list(destination.rglob("IMG_0660*.JPG"))
        organized_file = next(path for path in imported_files if "Conflicts" not in path.parts)
        conflict_file = next(path for path in imported_files if "Conflicts" in path.parts)
        self.assertEqual(1, results[0].imported)
        self.assertEqual(2, len(imported_files))
        self.assertEqual(b"external process content", organized_file.read_bytes())
        self.assertNotEqual(organized_file.read_bytes(), conflict_file.read_bytes())

    def test_future_source_timestamp_does_not_skip_forever(self) -> None:
        card = self.base / "FUTURE_CARD"
        destination = self.base / "future-library"
        config = self.config(card, destination, monitor={"settle_seconds": 5})
        self.identify(card, config)
        source = card / "DCIM" / "IMG_0670.JPG"
        self.jpeg(source)
        future = time.time() + 3600
        os.utime(source, (future, future))

        results, _ = Organizer(config).scan_all()

        self.assertEqual(1, results[0].imported)
        self.assertTrue(any(destination.rglob("IMG_0670.JPG")))

    def test_linked_source_file_is_not_imported(self) -> None:
        card = self.base / "LINK_CARD"
        destination = self.base / "link-library"
        outside = self.base / "outside" / "OUTSIDE.JPG"
        config = self.config(card, destination)
        self.identify(card, config)
        self.jpeg(outside)
        self.make_old(outside)
        linked = card / "DCIM" / "LINKED.JPG"
        linked.parent.mkdir(parents=True, exist_ok=True)
        try:
            linked.symlink_to(outside)
        except OSError as exc:
            self.skipTest(f"File links are unavailable on this system: {exc}")

        results, _ = Organizer(config).scan_all()

        self.assertEqual(0, results[0].imported)
        self.assertFalse(any(destination.rglob("*.JPG")))

    def test_folder_import_supports_recursive_and_top_level_modes(self) -> None:
        source_root = self.base / "folder-source"
        top_level = source_root / "TOP.JPG"
        nested = source_root / "nested" / "NESTED.JPG"
        self.jpeg(top_level)
        self.jpeg(nested)
        self.make_old(top_level)
        self.make_old(nested)

        top_destination = self.base / "folder-top-library"
        top_config = self.config(source_root, top_destination)
        top_source = folder_import_source(source_root, include_subfolders=False)
        top_result = Organizer(top_config).scan_card(top_source)
        self.assertEqual(1, top_result.imported)
        self.assertEqual(1, len(list(top_destination.rglob("TOP.JPG"))))
        self.assertEqual(0, len(list(top_destination.rglob("NESTED.JPG"))))

        recursive_destination = self.base / "folder-recursive-library"
        recursive_config = self.config(source_root, recursive_destination)
        recursive_source = folder_import_source(source_root, include_subfolders=True)
        recursive_result = Organizer(recursive_config).scan_card(recursive_source)
        self.assertEqual(2, recursive_result.imported)
        self.assertEqual(1, len(list(recursive_destination.rglob("TOP.JPG"))))
        self.assertEqual(1, len(list(recursive_destination.rglob("NESTED.JPG"))))
        self.assertFalse((source_root / ".photocard-folder-import-disabled").exists())

        repeated = Organizer(recursive_config).scan_card(recursive_source)
        self.assertEqual(0, repeated.imported)
        self.assertEqual(2, repeated.skipped)

    def test_retained_travel_identity_survives_a_changed_mount_path(self) -> None:
        first_mount = self.base / "mapped-laptop"
        second_mount = self.base / "unc-laptop"
        photo = first_mount / "Trip" / "IMG_3001.JPG"
        self.jpeg(photo)
        self.make_old(photo)
        shutil.copytree(first_mount, second_mount, copy_function=shutil.copy2)
        destination = self.base / "desktop-master"
        config = self.config(first_mount, destination)
        first_source = folder_import_source(
            first_mount,
            stable_id="travel-field-laptop",
        )
        second_source = folder_import_source(
            second_mount,
            stable_id="travel-field-laptop",
        )

        first = Organizer(config).scan_card(first_source)
        repeated_from_new_path = Organizer(config).scan_card(second_source)

        self.assertEqual(first_source.card_id, second_source.card_id)
        self.assertEqual(1, first.imported)
        self.assertEqual(0, repeated_from_new_path.imported)
        self.assertEqual(1, repeated_from_new_path.skipped)
        self.assertTrue(photo.is_file())
        self.assertTrue((second_mount / "Trip" / "IMG_3001.JPG").is_file())

    def test_retained_profile_overrides_connected_card_identity(self) -> None:
        card = self.base / "PROFILE_CARD"
        config = self.config(card, self.base / "profile-library")
        self.identify(card, config)
        upsert_card_profile(
            config,
            {
                "id": "canon-r5-a",
                "name": "Retained Studio Card",
                "last_root": str(card),
                "action": "move",
                "source_folders": ["MEDIA"],
                "destination_prefix": "Studio/R5",
                "enabled": True,
                "delete_empty_folders_after_move": True,
            },
        )

        cards, errors = discover_cards(config)
        self.assertEqual([], errors)
        self.assertEqual(1, len(cards))
        self.assertEqual("Retained Studio Card", cards[0].name)
        self.assertEqual("move", cards[0].action)
        self.assertEqual(("MEDIA",), cards[0].source_folders)
        self.assertEqual("Studio/R5", cards[0].destination_prefix)
        self.assertTrue(cards[0].delete_empty_folders_after_move)

    def test_exact_content_duplicate_is_preserved_with_custom_appendage(self) -> None:
        card = self.base / "EXACT_DUPLICATE_CARD"
        destination = self.base / "exact-duplicate-library"
        config = self.config(card, destination)
        config["safety"]["exact_duplicate_policy"] = "rename"
        config["safety"]["conflict_filename_appendage"] = " ({number})"
        self.identify(card, config)
        source = card / "DCIM" / "IMG_0700.JPG"
        self.jpeg(source)
        self.make_old(source)
        requested = (
            destination
            / "Photos"
            / "2024"
            / "2024-05-06"
            / "Canon EOS R5"
            / source.name
        )
        requested.parent.mkdir(parents=True, exist_ok=True)
        requested.write_bytes(source.read_bytes())

        results, _ = Organizer(config).scan_all()
        self.assertEqual(1, results[0].imported)
        self.assertTrue(requested.is_file())
        self.assertTrue(requested.with_name("IMG_0700 (2).JPG").is_file())
        conflicts = ImportManifest(destination).conflicts()
        self.assertEqual(1, len(conflicts))
        self.assertEqual("exact_duplicate", conflicts[0]["conflict_type"])
        ImportManifest(destination).mark_conflict_reviewed(int(conflicts[0]["id"]))
        self.assertEqual("reviewed", ImportManifest(destination).conflicts("reviewed")[0]["status"])

    def test_conflict_folder_retains_organized_hierarchy(self) -> None:
        card = self.base / "CONFLICT_FOLDER_CARD"
        destination = self.base / "conflict-folder-library"
        config = self.config(card, destination)
        config["safety"]["conflict_policy"] = "conflict_folder"
        config["safety"]["manual_conflict_prompt"] = False
        self.identify(card, config)
        source = card / "DCIM" / "IMG_0800.JPG"
        self.jpeg(source)
        self.make_old(source)
        organized = (
            Path("Photos") / "2024" / "2024-05-06" / "Canon EOS R5" / source.name
        )
        requested = destination / organized
        requested.parent.mkdir(parents=True, exist_ok=True)
        requested.write_bytes(b"different-existing-content")

        results, _ = Organizer(config).scan_all()
        self.assertEqual(1, results[0].imported)
        self.assertEqual(b"different-existing-content", requested.read_bytes())
        self.assertEqual(source.read_bytes(), (destination / "Conflicts" / organized).read_bytes())

    def test_required_replica_is_verified_and_has_matching_session(self) -> None:
        card = self.base / "REPLICA_CARD"
        destination = self.base / "primary-library"
        replica = self.base / "replica-library"
        config = self.config(
            card,
            destination,
            replica_destinations=[
                {
                    "id": "backup-one",
                    "name": "Backup One",
                    "root": str(replica),
                    "enabled": True,
                    "required": True,
                    "include_history": True,
                    "conflict_policy": "block",
                }
            ],
        )
        self.identify(card, config)
        source = card / "DCIM" / "IMG_0900.JPG"
        self.jpeg(source)
        self.make_old(source)

        results, _ = Organizer(config).scan_all()
        self.assertEqual(1, results[0].imported)
        primary_file = next(
            path for path in destination.rglob(source.name) if ".photocard-organizer" not in path.parts
        )
        relative = primary_file.relative_to(destination)
        self.assertEqual(primary_file.read_bytes(), (replica / relative).read_bytes())
        primary_sessions = list(
            (destination / ".photocard-organizer" / "transfer-records" / "canon-r5-a").rglob(
                "*.jsonl"
            )
        )
        replica_sessions = list(
            (replica / ".photocard-organizer" / "transfer-records" / "canon-r5-a").rglob(
                "*.jsonl"
            )
        )
        self.assertEqual(1, len(primary_sessions))
        self.assertEqual(1, len(replica_sessions))
        self.assertEqual(primary_sessions[0].read_bytes(), replica_sessions[0].read_bytes())
        records = [
            json.loads(line)
            for line in primary_sessions[0].read_text(encoding="utf-8").splitlines()
        ]
        transfer = next(record for record in records if record.get("record_type") == "transfer")
        self.assertEqual("verified", transfer["replicas"][0]["status"])

    def test_required_replica_retry_reuses_pending_primary_copy(self) -> None:
        card = self.base / "REPLICA_RETRY_CARD"
        destination = self.base / "retry-primary"
        replica = self.base / "retry-replica"
        config = self.config(
            card,
            destination,
            replica_destinations=[
                {
                    "id": "required-backup",
                    "name": "Required Backup",
                    "root": str(replica),
                    "enabled": True,
                    "required": True,
                    "include_history": True,
                }
            ],
        )
        self.identify(card, config)
        source = card / "DCIM" / "IMG_1000.JPG"
        self.jpeg(source)
        self.make_old(source)

        with patch.object(
            Organizer,
            "_replicate_file",
            return_value=([{"status": "failed", "required": True}], False),
        ):
            first, _ = Organizer(config).scan_all()
        self.assertEqual(0, first[0].imported)
        self.assertEqual(1, first[0].blocked)
        primary_files = [
            path for path in destination.rglob(source.name) if ".photocard-organizer" not in path.parts
        ]
        self.assertEqual(1, len(primary_files))

        second, _ = Organizer(config).scan_all()
        self.assertEqual(1, second[0].imported)
        all_primary_jpegs = [
            path for path in destination.rglob("*.JPG") if ".photocard-organizer" not in path.parts
        ]
        self.assertEqual(1, len(all_primary_jpegs))
        relative = all_primary_jpegs[0].relative_to(destination)
        self.assertTrue((replica / relative).is_file())
        self.assertIsNone(ImportManifest(destination).pending(Organizer.source_key(
            discover_cards(config)[0][0],
            source.relative_to(card),
            source.stat().st_size,
            source.stat().st_mtime_ns,
        )))

    def test_optional_replica_failure_does_not_block_primary(self) -> None:
        card = self.base / "OPTIONAL_REPLICA_CARD"
        destination = self.base / "optional-primary"
        replica = self.base / "optional-replica"
        config = self.config(
            card,
            destination,
            replica_destinations=[
                {
                    "id": "optional-backup",
                    "name": "Optional Backup",
                    "root": str(replica),
                    "enabled": True,
                    "required": False,
                    "include_history": True,
                }
            ],
        )
        self.identify(card, config)
        source = card / "DCIM" / "IMG_1100.JPG"
        self.jpeg(source)
        self.make_old(source)
        original_copy = Organizer._copy_and_verify

        def fail_optional_copy(
            organizer,
            source_path,
            destination_path,
            verification,
            expected_stat=None,
        ):
            try:
                destination_path.relative_to(replica)
            except ValueError:
                return original_copy(
                    organizer,
                    source_path,
                    destination_path,
                    verification,
                    expected_stat=expected_stat,
                )
            raise OSError("optional destination disconnected")

        with patch.object(
            Organizer,
            "_copy_and_verify",
            autospec=True,
            side_effect=fail_optional_copy,
        ):
            results, _ = Organizer(config).scan_all()

        self.assertEqual(1, results[0].imported)
        self.assertTrue(any("Optional Backup failed" in warning for warning in results[0].warnings))
        session = next(
            (destination / ".photocard-organizer" / "transfer-records" / "canon-r5-a").rglob(
                "*.jsonl"
            )
        )
        records = [json.loads(line) for line in session.read_text(encoding="utf-8").splitlines()]
        transfer = next(record for record in records if record.get("record_type") == "transfer")
        self.assertEqual("failed", transfer["replicas"][0]["status"])

    def test_client_settings_are_portable_without_clearing_local_paths(self) -> None:
        export_card_root = self.base / "export-card"
        export_replica = self.base / "export-replica"
        source_config = normalize_config(
            {
                "destination_root": str(self.base / "export-library"),
                "identification": {"configured_roots": [str(export_card_root)]},
                "local_history": {"directory": str(self.base / "export-local-history")},
                "safety": {"fallback_destination_roots": [str(self.base / "export-fallback")]},
                "card_profiles": [
                    {
                        "id": "portable-card",
                        "name": "Portable Card",
                        "last_root": str(export_card_root),
                        "source_folders": ["DCIM"],
                    }
                ],
                "replica_destinations": [
                    {
                        "id": "portable-backup",
                        "name": "Portable Backup",
                        "root": str(export_replica),
                        "enabled": True,
                    }
                ],
            }
        )
        portable = build_client_settings(source_config)
        self.assertNotIn("destination_root", portable["settings"])
        self.assertEqual("", portable["settings"]["card_profiles"][0]["last_root"])
        self.assertEqual("", portable["settings"]["replica_destinations"][0]["root"])
        settings_path = self.base / "client-settings.json"
        export_client_settings(source_config, settings_path)

        target_card_root = self.base / "target-card"
        target_replica = self.base / "target-replica"
        target_local_history = self.base / "target-local-history"
        target_fallback = self.base / "target-fallback"
        target_config = normalize_config(
            {
                "destination_root": str(self.base / "target-library"),
                "instance": {
                    "id": "target-instance",
                    "name": "Target PC",
                    "library_id": "target-library-id",
                },
                "identification": {"configured_roots": [str(target_card_root)]},
                "local_history": {"directory": str(target_local_history)},
                "safety": {"fallback_destination_roots": [str(target_fallback)]},
                "card_profiles": [
                    {
                        "id": "portable-card",
                        "name": "Old Name",
                        "last_root": str(target_card_root),
                        "source_folders": ["OLD"],
                    }
                ],
                "replica_destinations": [
                    {
                        "id": "portable-backup",
                        "name": "Old Backup Name",
                        "root": str(target_replica),
                        "enabled": True,
                    }
                ],
            }
        )
        imported = import_client_settings(target_config, settings_path)
        self.assertEqual(str(self.base / "target-library"), imported["destination_root"])
        self.assertEqual("target-instance", imported["instance"]["id"])
        self.assertEqual([str(target_card_root)], imported["identification"]["configured_roots"])
        self.assertEqual(str(target_local_history), imported["local_history"]["directory"])
        self.assertEqual([str(target_fallback)], imported["safety"]["fallback_destination_roots"])
        self.assertEqual(str(target_card_root), imported["card_profiles"][0]["last_root"])
        self.assertEqual("Portable Card", imported["card_profiles"][0]["name"])
        self.assertEqual(str(target_replica), imported["replica_destinations"][0]["root"])
        self.assertTrue(imported["replica_destinations"][0]["enabled"])

    def test_duplicate_replica_roots_are_deduplicated(self) -> None:
        replica_root = self.base / "same-replica"
        config = normalize_config(
            {
                "destination_root": str(self.base / "dedupe-primary"),
                "replica_destinations": [
                    {
                        "id": "first",
                        "name": "First",
                        "root": str(replica_root),
                    },
                    {
                        "id": "second",
                        "name": "Second",
                        "root": str(replica_root),
                    },
                ],
            }
        )
        self.assertEqual(["first"], [item["id"] for item in config["replica_destinations"]])


if __name__ == "__main__":
    unittest.main()
