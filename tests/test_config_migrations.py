from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from photocard.config import (
    CURRENT_CONFIG_SCHEMA,
    build_client_settings,
    import_client_settings,
    load_config,
    normalize_config,
)


class ConfigMigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_schema_one_is_backed_up_before_in_place_migration(self) -> None:
        config_path = self.base / "PhotoCardOrganizer" / "config.json"
        config_path.parent.mkdir()
        original = {
            "schema": 1,
            "destination_root": str(self.base / "library"),
            "custom_future_safe_value": {"keep": True},
        }
        config_path.write_text(json.dumps(original), encoding="utf-8")

        migrated, resolved_path = load_config(config_path, create=True)

        self.assertEqual(config_path, resolved_path)
        self.assertEqual(CURRENT_CONFIG_SCHEMA, migrated["schema"])
        self.assertTrue(migrated["maintenance"]["backup_before_migration"])
        self.assertEqual({"keep": True}, migrated["custom_future_safe_value"])
        saved = json.loads(config_path.read_text(encoding="utf-8"))
        self.assertEqual(CURRENT_CONFIG_SCHEMA, saved["schema"])
        backups = list((config_path.parent / "backups").glob("config.schema-1.*.json"))
        self.assertEqual(1, len(backups))
        self.assertEqual(original, json.loads(backups[0].read_text(encoding="utf-8")))

        load_config(config_path, create=True)
        self.assertEqual(1, len(list((config_path.parent / "backups").glob("*.json"))))

    def test_read_only_load_migrates_in_memory_without_touching_source(self) -> None:
        config_path = self.base / "config.json"
        config_path.write_text('{"schema": 1}', encoding="utf-8")

        migrated, _ = load_config(config_path, create=False)

        self.assertEqual(CURRENT_CONFIG_SCHEMA, migrated["schema"])
        self.assertEqual({"schema": 1}, json.loads(config_path.read_text(encoding="utf-8")))
        self.assertFalse((self.base / "backups").exists())

    def test_config_and_client_settings_accept_utf8_bom(self) -> None:
        config_path = self.base / "config-with-bom.json"
        config_path.write_text(
            json.dumps({"schema": CURRENT_CONFIG_SCHEMA}),
            encoding="utf-8-sig",
        )

        loaded, _ = load_config(config_path, create=False)

        self.assertEqual(CURRENT_CONFIG_SCHEMA, loaded["schema"])

        settings_path = self.base / "settings-with-bom.json"
        portable = build_client_settings(
            normalize_config({"monitor": {"poll_seconds": 17}})
        )
        settings_path.write_text(json.dumps(portable), encoding="utf-8-sig")

        imported = import_client_settings(normalize_config({}), settings_path)

        self.assertEqual(17, imported["monitor"]["poll_seconds"])

    def test_future_schema_is_rejected_without_rewriting_it(self) -> None:
        config_path = self.base / "config.json"
        future = {"schema": CURRENT_CONFIG_SCHEMA + 1, "destination_root": "future"}
        config_path.write_text(json.dumps(future), encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "Install a newer"):
            load_config(config_path, create=True)

        self.assertEqual(future, json.loads(config_path.read_text(encoding="utf-8")))
        self.assertFalse((self.base / "backups").exists())

    def test_normalization_always_emits_current_schema(self) -> None:
        self.assertEqual(CURRENT_CONFIG_SCHEMA, normalize_config({"schema": 1})["schema"])

    def test_schema_two_adds_empty_travel_and_hub_profiles(self) -> None:
        normalized = normalize_config({"schema": 2})

        self.assertEqual(CURRENT_CONFIG_SCHEMA, normalized["schema"])
        self.assertEqual([], normalized["travel_libraries"])
        self.assertEqual([], normalized["transfer_hubs"])

    def test_schema_three_adds_empty_digest_inboxes(self) -> None:
        normalized = normalize_config({"schema": 3})

        self.assertEqual(CURRENT_CONFIG_SCHEMA, normalized["schema"])
        self.assertEqual([], normalized["digest_inboxes"])

    def test_portable_settings_strip_source_machine_paths(self) -> None:
        normalized = normalize_config(
            {
                "travel_libraries": [
                    {
                        "id": "laptop",
                        "name": "Laptop",
                        "root": str(self.base / "laptop"),
                        "enabled": True,
                    }
                ],
                "transfer_hubs": [
                    {
                        "id": "hub",
                        "name": "Hub",
                        "root": str(self.base / "hub"),
                        "enabled": True,
                        "role": "catch",
                    }
                ],
                "digest_inboxes": [
                    {
                        "id": "drop",
                        "name": "Drop",
                        "root": str(self.base / "drop"),
                        "enabled": True,
                        "auto_digest": True,
                    }
                ],
            }
        )

        settings = build_client_settings(normalized)["settings"]

        self.assertEqual("", settings["travel_libraries"][0]["root"])
        self.assertFalse(settings["travel_libraries"][0]["enabled"])
        self.assertEqual("", settings["transfer_hubs"][0]["root"])
        self.assertFalse(settings["transfer_hubs"][0]["enabled"])
        self.assertEqual("", settings["digest_inboxes"][0]["root"])
        self.assertFalse(settings["digest_inboxes"][0]["enabled"])
        self.assertFalse(settings["digest_inboxes"][0]["auto_digest"])

    def test_imported_digest_settings_keep_local_root_and_monitor_state(self) -> None:
        local = normalize_config(
            {
                "digest_inboxes": [
                    {
                        "id": "drop",
                        "name": "Local drop",
                        "root": str(self.base / "drop"),
                        "enabled": True,
                        "auto_digest": True,
                    }
                ]
            }
        )
        portable = build_client_settings(
            normalize_config(
                {
                    "digest_inboxes": [
                        {
                            "id": "drop",
                            "name": "Shared drop definition",
                            "action": "copy",
                            "include_subfolders": False,
                        }
                    ]
                }
            )
        )
        settings_path = self.base / "portable.json"
        settings_path.write_text(json.dumps(portable), encoding="utf-8")

        imported = import_client_settings(local, settings_path)
        inbox = imported["digest_inboxes"][0]

        self.assertEqual("Shared drop definition", inbox["name"])
        self.assertEqual(str(self.base / "drop"), inbox["root"])
        self.assertTrue(inbox["enabled"])
        self.assertTrue(inbox["auto_digest"])
        self.assertFalse(inbox["include_subfolders"])


if __name__ == "__main__":
    unittest.main()
