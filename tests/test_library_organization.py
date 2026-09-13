import copy
import tempfile
import unittest
from pathlib import Path

from photocard.config import normalize_config, library_media_rule
from photocard.reorganization import build_reorganization_plan
from photocard.library_jobs import build_library_job


class LibraryOrganizationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.root = self.base / "library"
        self.root.mkdir()
        self.config = normalize_config({"destination_root": str(self.root)})
        self.library = self.config["library_destinations"][0]

    def test_inheritance_and_override_do_not_mutate_globals(self):
        original = copy.deepcopy(self.config["media_rules"])
        self.assertEqual(library_media_rule(self.config, self.root, "video"), original["video"])
        self.library["organization_overrides"] = {"video": {"folder_segments": ["Clips"], "filename_template": "clip_{original}"}}
        normalized = normalize_config(self.config)
        self.assertEqual(library_media_rule(normalized, self.root, "video")["folder_segments"], ["Clips"])
        self.assertEqual(library_media_rule(normalized, self.base / "other", "video"), original["video"])
        self.assertEqual(normalized["media_rules"], original)

    def test_reorganization_honors_saved_names_and_explicit_preset(self):
        source = self.root / "old.mp4"
        source.write_bytes(b"synthetic video")
        self.library["organization_overrides"] = {"video": {"folder_segments": ["Clips"], "filename_template": "clip_{original}"}}
        plan = build_reorganization_plan(self.config, "Use current detailed rules", {"video"})
        self.assertEqual(plan.entries[0].destination, self.root / "Clips/clip_old.mp4")
        preset = build_reorganization_plan(self.config, "Media folder only", {"video"})
        self.assertEqual(preset.entries[0].destination, self.root / "Videos/old.mp4")
        self.assertEqual(self.library["organization_overrides"]["video"]["folder_segments"], ["Clips"])

    def test_merge_uses_receiving_library_rules(self):
        source = self.base / "incoming"
        source.mkdir()
        (source / "clip.mp4").write_bytes(b"synthetic video")
        self.library["organization_overrides"] = {"video": {"folder_segments": ["Cinema"], "filename_template": "{original}"}}
        plan = build_library_job(self.config, [source])
        self.assertEqual(plan.entries[0].destination, self.root / "Cinema/clip.mp4")
