from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable

from .discovery import MarkerError, folder_import_source
from .models import ActivityEvent, CardMarker, DecisionRequest, DecisionResult, ImportStats
from .organizer import Organizer


EventCallback = Callable[[ActivityEvent], None]
DecisionCallback = Callable[[DecisionRequest], DecisionResult]

DIGEST_STATE_FOLDER = ".photocard-digest"


@dataclass(frozen=True)
class DigestCandidate:
    source: Path
    relative_path: Path
    media_kind: str
    source_key: str
    size: int
    mtime_ns: int


@dataclass
class DigestRunResult:
    profile_id: str
    profile_name: str
    run_id: str
    stats: ImportStats
    pending: int = 0
    processed: int = 0
    failed: int = 0
    conflicts: int = 0
    errors: list[str] = field(default_factory=list)


def digest_source(profile: dict) -> CardMarker:
    root = str(profile.get("root", "")).strip()
    if not root:
        raise MarkerError("The Digest Inbox folder is not configured.")
    source = folder_import_source(
        root,
        name=f"Digest: {profile['name']}",
        stable_id=f"digest-{profile['id']}",
        action=str(profile.get("action", "copy")),
        include_subfolders=bool(profile.get("include_subfolders", True)),
        destination_prefix=str(profile.get("destination_prefix", "")),
        camera_name=str(profile.get("camera_name", "")),
    )
    return replace(
        source,
        source_type="digest",
        ignored_source_folders=(DIGEST_STATE_FOLDER,),
    )


def digest_card_id(profile_id: str) -> str:
    stable = re.sub(
        r"[^A-Za-z0-9._-]+",
        "-",
        f"digest-{profile_id}",
    ).strip("-") or "digest"
    return f"folder-{stable}"


def run_digest_profile(
    config: dict,
    profile: dict,
    *,
    allow_destructive: bool = False,
    event_callback: EventCallback | None = None,
    decision_callback: DecisionCallback | None = None,
) -> DigestRunResult:
    profile_id = str(profile["id"])
    profile_name = str(profile["name"])
    card = digest_source(profile)
    if card.action == "move" and not allow_destructive:
        raise ValueError(
            "Move-mode Digest Inboxes require an explicit manual confirmation."
        )

    source_errors: dict[str, str] = {}

    def relay(event: ActivityEvent) -> None:
        source = str(event.details.get("source", "")).strip()
        if event.level == "error" and source:
            source_errors[source] = event.message
        if event_callback:
            event_callback(event)

    organizer = Organizer(
        config,
        event_callback=relay,
        decision_callback=decision_callback,
    )
    run_id = uuid.uuid4().hex
    organizer.manifest.start_digest_run(run_id, profile_id, profile_name)
    candidates: list[DigestCandidate] = []
    preflight_errors: dict[str, str] = {}
    stats = ImportStats(
        card_id=card.card_id,
        card_name=card.name,
        action=card.action,
    )

    try:
        for source, media_kind in organizer.source_candidates(card):
            relative = source.relative_to(card.root)
            try:
                source_stat = source.stat()
            except OSError as exc:
                source_key = organizer.source_key(card, relative, 0, 0)
                message = f"Cannot inspect {source}: {exc}"
                preflight_errors[str(source)] = message
                organizer.manifest.record_digest_item(
                    profile_id=profile_id,
                    source_key=source_key,
                    source_path=source,
                    relative_path=relative,
                    media_kind=media_kind,
                    source_size=0,
                    source_mtime_ns=0,
                    status="failed",
                    error=message,
                )
                continue
            source_key = organizer.source_key(
                card,
                relative,
                source_stat.st_size,
                source_stat.st_mtime_ns,
            )
            candidates.append(
                DigestCandidate(
                    source=source,
                    relative_path=relative,
                    media_kind=media_kind,
                    source_key=source_key,
                    size=source_stat.st_size,
                    mtime_ns=source_stat.st_mtime_ns,
                )
            )
            organizer.manifest.record_digest_item(
                profile_id=profile_id,
                source_key=source_key,
                source_path=source,
                relative_path=relative,
                media_kind=media_kind,
                source_size=source_stat.st_size,
                source_mtime_ns=source_stat.st_mtime_ns,
                status="pending",
            )

        stats = organizer.scan_card(
            card,
            allow_destructive=allow_destructive,
        )
        if preflight_errors:
            stats.discovered += len(preflight_errors)
            stats.failed += len(preflight_errors)
            stats.errors.extend(preflight_errors.values())
        run_conflicts = 0
        for candidate in candidates:
            record = organizer.manifest.import_record(candidate.source_key)
            conflict = organizer.manifest.conflict_for_source(
                candidate.source_key,
                status="open",
            )
            error = source_errors.get(str(candidate.source), "")
            if record is not None:
                item_status = "conflict" if conflict is not None else "processed"
                destination = str(record.get("destination_path", ""))
                if conflict is not None:
                    run_conflicts += 1
            elif error:
                item_status = "failed"
                destination = ""
            else:
                item_status = "pending"
                destination = ""
            organizer.manifest.record_digest_item(
                profile_id=profile_id,
                source_key=candidate.source_key,
                source_path=candidate.source,
                relative_path=candidate.relative_path,
                media_kind=candidate.media_kind,
                source_size=candidate.size,
                source_mtime_ns=candidate.mtime_ns,
                status=item_status,
                destination_path=destination,
                error=error,
            )

        summary = organizer.manifest.digest_summary(profile_id)
        issue_count = stats.failed + stats.blocked
        run_status = "completed_with_issues" if issue_count else "completed"
        organizer.manifest.finish_digest_run(
            run_id,
            status=run_status,
            discovered=stats.discovered,
            imported=stats.imported,
            skipped=stats.skipped,
            blocked=stats.blocked,
            failed=stats.failed,
            conflicts=run_conflicts,
            error="\n".join(preflight_errors.values()),
        )
        result = DigestRunResult(
            profile_id=profile_id,
            profile_name=profile_name,
            run_id=run_id,
            stats=stats,
            pending=int(summary["pending"]),
            processed=int(summary["processed"]),
            failed=int(summary["failed"]),
            conflicts=int(summary["conflict"]),
            errors=list(stats.errors),
        )
        relay(
            ActivityEvent(
                "warning" if issue_count else "success",
                (
                    f"{profile_name}: digest complete with {stats.imported} imported, "
                    f"{stats.skipped} already handled, {issue_count} issue(s)."
                ),
                details={
                    "profile_id": profile_id,
                    "imported": stats.imported,
                    "skipped": stats.skipped,
                    "failed": issue_count,
                },
            )
        )
        return result
    except Exception as exc:
        organizer.manifest.finish_digest_run(
            run_id,
            status="failed",
            discovered=len(candidates) + len(preflight_errors),
            imported=stats.imported,
            skipped=stats.skipped,
            blocked=stats.blocked,
            failed=max(1, stats.failed + len(preflight_errors)),
            conflicts=0,
            error=str(exc),
        )
        raise
