from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .autostart import install_autostart, remove_autostart
from .config import (
    default_config_path,
    export_client_settings,
    import_client_settings,
    load_config,
    save_config,
    upsert_card_profile,
)
from .discovery import folder_import_source, load_card_identity, write_card_identity
from .digest import run_digest_profile
from .organizer import Organizer
from .single_instance import SingleInstance


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Monitor identified camera cards and organize their media.")
    parser.add_argument("--version", action="version", version=f"Photo Card Organizer {__version__}")
    parser.add_argument("--check-gui", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--config", type=Path, help="Path to the application configuration JSON file.")
    parser.add_argument("--service", action="store_true", help="Start in the tray with the settings window hidden.")
    parser.add_argument("--scan-once", action="store_true", help="Scan all connected identified cards once, then exit.")
    parser.add_argument("--dry-run", action="store_true", help="Show planned transfers without writing files or history.")
    parser.add_argument(
        "--confirm-move",
        action="store_true",
        help="Explicitly authorize configured move operations during a one-time scan.",
    )
    parser.add_argument("--init-config", action="store_true", help="Create a default configuration file and exit.")
    parser.add_argument("--print-config", action="store_true", help="Print the resolved configuration and exit.")
    parser.add_argument(
        "--export-settings",
        metavar="PATH",
        type=Path,
        help="Export portable client settings without machine-specific paths.",
    )
    parser.add_argument(
        "--import-settings",
        metavar="PATH",
        type=Path,
        help="Merge portable client settings into this computer's configuration.",
    )
    parser.add_argument("--install-autostart", action="store_true", help="Start the tray agent when this user logs in.")
    parser.add_argument("--remove-autostart", action="store_true", help="Remove the login autostart entry.")
    parser.add_argument("--create-identity", metavar="ROOT", type=Path, help="Create an identity folder at a volume root.")
    parser.add_argument("--card-name", default="Camera card", help="Display name used with --create-identity.")
    parser.add_argument("--card-id", default="", help="Stable identifier used with --create-identity.")
    parser.add_argument("--card-action", choices=("copy", "move"), default="copy")
    parser.add_argument("--source-folder", action="append", dest="source_folders", help="Source folder relative to the root; repeatable.")
    parser.add_argument(
        "--import-folder",
        metavar="PATH",
        type=Path,
        help="Import media from an arbitrary folder using the configured organization rules.",
    )
    parser.add_argument("--import-name", default="", help="Display name for --import-folder.")
    parser.add_argument(
        "--library-subfolder",
        "--destination-prefix",
        dest="destination_prefix",
        default="",
        help="Optional subfolder inside the main library for --import-folder.",
    )
    parser.add_argument(
        "--no-subfolders",
        action="store_true",
        help="Only import files directly inside --import-folder.",
    )
    parser.add_argument(
        "--digest-inbox",
        action="append",
        dest="digest_inboxes",
        metavar="PROFILE_ID",
        help="Run a saved Digest Inbox once; repeatable.",
    )
    parser.add_argument(
        "--digest-all",
        action="store_true",
        help="Run every enabled saved Digest Inbox once.",
    )
    return parser


def _print_results(results, errors) -> int:
    for error in errors:
        print(f"Import error: {error}", file=sys.stderr)
    if not results:
        print("No media sources were processed.")
    for result in results:
        print(
            f"{result.card_name}: {result.imported} imported, {result.skipped} already handled, "
            f"{result.blocked} blocked, {result.failed} failed"
        )
        if result.pending_destructive_confirmation:
            print("  Move is waiting for explicit confirmation. Re-run with --confirm-move.")
        for warning in result.warnings:
            print(f"  Warning: {warning}")
        for error in result.errors:
            print(f"  Error: {error}", file=sys.stderr)
    return 1 if errors or any(result.failed for result in results) else 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.check_gui:
        from .installation_check import check_gui

        return check_gui(args.check_gui)
    instance = SingleInstance(default_config_path().parent / "instance.lock")
    if not instance.acquire():
        if instance.last_error is not None:
            print(
                f"Could not create the per-user instance lock: {instance.last_error}",
                file=sys.stderr,
            )
            return 1
        gui_launch = not any(
            (
                args.scan_once,
                args.dry_run,
                args.init_config,
                args.print_config,
                args.export_settings,
                args.import_settings,
                args.install_autostart,
                args.remove_autostart,
                args.create_identity,
                args.import_folder,
                args.digest_inboxes,
                args.digest_all,
            )
        )
        if gui_launch and instance.activate_existing():
            return 0
        if not instance.acquire():
            print(
                "Photo Card Organizer is already running. Use its existing window or tray icon.",
                file=sys.stderr,
            )
            return 1
    try:
        return _main_locked(args, instance)
    finally:
        instance.release()


def _main_locked(args: argparse.Namespace, instance: SingleInstance) -> int:
    try:
        config, config_path = load_config(args.config, create=True)
    except (OSError, ValueError) as exc:
        print(f"Could not load configuration: {exc}", file=sys.stderr)
        return 1

    if args.init_config:
        save_config(config, config_path)
        print(config_path)
        return 0
    if args.import_settings:
        try:
            config = import_client_settings(config, args.import_settings)
            save_config(config, config_path)
        except (OSError, ValueError) as exc:
            print(f"Could not import settings: {exc}", file=sys.stderr)
            return 1
        print(config_path)
        return 0
    if args.export_settings:
        try:
            exported = export_client_settings(config, args.export_settings)
        except (OSError, ValueError) as exc:
            print(f"Could not export settings: {exc}", file=sys.stderr)
            return 1
        print(exported)
        return 0
    if args.print_config:
        print(json.dumps(config, indent=2))
        return 0
    if args.install_autostart:
        print(install_autostart(config_path))
        return 0
    if args.remove_autostart:
        print(remove_autostart())
        return 0
    if args.create_identity:
        identification = config["identification"]
        identity_path = write_card_identity(
            args.create_identity,
            identification["folder_name"],
            identification["identity_filename"],
            identification["history_folder_name"],
            args.card_name,
            args.card_id,
            args.card_action,
            args.source_folders or ["DCIM"],
        )
        resolved_root = args.create_identity.expanduser().resolve()
        configured = config["identification"]["configured_roots"]
        if str(resolved_root) not in configured:
            configured.append(str(resolved_root))
        card = load_card_identity(
            resolved_root,
            identification["folder_name"],
            identification["identity_filename"],
            identification["history_folder_name"],
            config["safety"]["default_action"],
        )
        upsert_card_profile(
            config,
            {
                "id": card.card_id,
                "name": card.name,
                "last_root": str(card.root),
                "action": card.action,
                "source_folders": list(card.source_folders),
                "destination_prefix": card.destination_prefix,
                "camera_name": card.camera_name,
                "enabled": card.enabled,
                "delete_empty_folders_after_move": card.delete_empty_folders_after_move,
            },
        )
        save_config(config, config_path)
        print(identity_path)
        return 0
    if args.digest_inboxes or args.digest_all:
        configured = list(config.get("digest_inboxes", []))
        requested_ids = set(args.digest_inboxes or [])
        if args.digest_all:
            selected = [
                profile for profile in configured if profile.get("enabled", True)
            ]
        else:
            selected = [
                profile
                for profile in configured
                if str(profile.get("id", "")) in requested_ids
            ]
        missing = requested_ids - {
            str(profile.get("id", "")) for profile in selected
        }
        for profile_id in sorted(missing):
            print(
                f"Digest Inbox profile not found: {profile_id}",
                file=sys.stderr,
            )
        if not selected:
            if not missing:
                print("No enabled Digest Inbox profiles are configured.")
            return 1 if missing else 0
        results = []
        errors = []
        for profile in selected:
            try:
                digest = run_digest_profile(
                    config,
                    profile,
                    allow_destructive=args.confirm_move,
                )
                results.append(digest.stats)
            except Exception as exc:
                errors.append(f"{profile['name']}: {exc}")
        return _print_results(results, errors)
    if args.import_folder:
        try:
            source = folder_import_source(
                args.import_folder,
                name=args.import_name,
                action=args.card_action,
                include_subfolders=not args.no_subfolders,
                destination_prefix=args.destination_prefix,
            )
            organizer = Organizer(config, dry_run=args.dry_run)
            result = organizer.scan_card(source, allow_destructive=args.confirm_move)
        except Exception as exc:
            print(f"Folder import could not start: {exc}", file=sys.stderr)
            return 1
        return _print_results([result], [])
    if args.scan_once or args.dry_run:
        try:
            organizer = Organizer(config, dry_run=args.dry_run)
            results, errors = organizer.scan_all(allow_destructive=args.confirm_move)
        except Exception as exc:
            print(f"Import could not start: {exc}", file=sys.stderr)
            return 1
        return _print_results(results, errors)

    from .gui import run_gui

    run_gui(
        config,
        config_path,
        start_minimized=args.service or config["monitor"].get("start_minimized", False),
        instance_guard=instance,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
