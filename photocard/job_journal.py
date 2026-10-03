"""Local, durable library-job plans and committed per-file progress."""

import json
import re
import sqlite3
import logging
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

from .integrity import checked_path, local_state_directory


def snapshot(value):
    return {name: int(getattr(value, name)) for name in
            ("st_dev", "st_ino", "st_size", "st_mtime_ns")}


def identity(path):
    value = path.stat()
    return [value.st_dev, value.st_ino]


def journal_path(identifier, local_root=None):
    if not re.fullmatch(r"[0-9a-f]{32}", identifier):
        raise ValueError("Invalid library job identifier")
    root = Path(local_root) if local_root else local_state_directory()
    return checked_path(root / "library-jobs" / f"{identifier}.sqlite3")


def plan_data(plan):
    return dict(
        id=plan.id, config=plan.config, mode=plan.mode,
        source_roots=[str(root) for root in plan.source_roots],
        migration_target=str(plan.migration_target) if plan.migration_target else None,
        backup_root=str(plan.backup_root) if plan.backup_root else None,
        migration_checksum=plan.migration_checksum,
        keep_originals=plan.keep_originals,
        entries=[dict(source=str(e.source), destination=str(e.destination), root=str(e.root),
                      digest=e.digest, snapshot=snapshot(e.snapshot), action=e.action,
                      backup=str(e.backup) if e.backup else None,
                      existing=str(e.existing) if e.existing else None) for e in plan.entries],
    )


class JobJournal:
    def __init__(self, plan, local_root=None):
        path = journal_path(plan.id, local_root)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        try:
            self.connection.execute("PRAGMA synchronous=FULL")
            self.connection.execute("CREATE TABLE IF NOT EXISTS job (id INTEGER PRIMARY KEY, data TEXT, anchors TEXT, phase TEXT, error TEXT)")
            self.connection.execute("CREATE TABLE IF NOT EXISTS files (position INTEGER PRIMARY KEY, state TEXT, evidence TEXT)")
            self.connection.execute("CREATE TABLE IF NOT EXISTS execution (method TEXT)")
            self.connection.execute("CREATE TABLE IF NOT EXISTS copies (position INTEGER, role TEXT, evidence TEXT, PRIMARY KEY(position, role))")
            stored = self.connection.execute("SELECT data, anchors FROM job WHERE id=1").fetchone()
            data = plan_data(plan)
            if stored is None:
                roots = {*plan.source_roots, *[entry.root for entry in plan.entries]}
                if plan.backup_root:
                    roots.add(plan.backup_root)
                anchors = {}
                for root in roots:
                    ancestor = checked_path(root)
                    while not ancestor.exists():
                        if ancestor == ancestor.parent:
                            raise FileNotFoundError(f"Unavailable destination: {root}")
                        ancestor = ancestor.parent
                    anchors[str(ancestor)] = identity(ancestor)
                with self.connection:
                    self.connection.execute("INSERT INTO job VALUES (1, ?, ?, 'planned', '')",
                                            (json.dumps(data), json.dumps(anchors)))
            else:
                if json.loads(stored[0]) != data:
                    raise ValueError("The saved job plan differs. Resume its original plan.")
                anchors = json.loads(stored[1])
            phase = self.phase
            moving = plan.mode == "migrate" and not plan.keep_originals
            source = plan.source_roots[0] if moving else None
            target = plan.migration_target if moving else None
            for path, expected in anchors.items():
                checked = checked_path(Path(path))
                if moving and checked == source and not source.exists():
                    if phase in {"renaming", "finalizing", "complete", "activated"} and identity(target) == expected:
                        continue
                    if phase in {"cleaning", "complete", "activated"} and self.method == "copy" and str(target) in anchors and identity(target) == anchors[str(target)]:
                        continue
                if moving and checked == target and phase == "renaming":
                    if not target.exists() and source.exists():
                        continue
                    if not source.exists() and identity(target) == anchors[str(source)]:
                        continue
                if identity(checked) != expected:
                    raise ValueError(f"A job drive or folder has been replaced: {path}")
        except BaseException:
            self.connection.close()
            raise

    @property
    def method(self):
        row = self.connection.execute("SELECT method FROM execution").fetchone()
        return row[0] if row else None

    def set_method(self, method):
        with self.connection:
            self.connection.execute("INSERT INTO execution VALUES (?)", (method,))

    @property
    def phase(self):
        return self.connection.execute("SELECT phase FROM job WHERE id=1").fetchone()[0]

    def set_phase(self, phase, error=""):
        logging.getLogger(__name__).info("Library job phase=%s; has_error=%s", phase, bool(error))
        with self.connection:
            self.connection.execute("UPDATE job SET phase=?, error=? WHERE id=1", (phase, error))

    def fail(self, error):
        with self.connection:
            self.connection.execute("UPDATE job SET error=? WHERE id=1", (str(error),))

    def anchor_target(self, target):
        anchors = json.loads(self.connection.execute("SELECT anchors FROM job WHERE id=1").fetchone()[0])
        anchors[str(target)] = identity(target)
        with self.connection:
            self.connection.execute("UPDATE job SET anchors=? WHERE id=1", (json.dumps(anchors),))

    def files(self):
        return {position: (state, json.loads(evidence)) for position, state, evidence in
                self.connection.execute("SELECT position, state, evidence FROM files")}

    def record(self, position, state, entry, event, *, destination_snapshot=None):
        evidence = dict(destination=snapshot(destination_snapshot if destination_snapshot is not None else entry.destination.stat()),
                        backup=snapshot(entry.backup.stat()) if entry.backup else None,
                        event=event)
        with self.connection:
            self.connection.execute("INSERT OR REPLACE INTO files VALUES (?, ?, ?)",
                                    (position, state, json.dumps(evidence)))

    def mark_done(self, position):
        with self.connection:
            self.connection.execute("UPDATE files SET state='done' WHERE position=?", (position,))

    def refresh_destination(self, position, value):
        """Refresh intentionally rewritten metadata, retaining all backup evidence."""
        row = self.connection.execute("SELECT evidence FROM files WHERE position=?", (position,)).fetchone()
        evidence = json.loads(row[0])
        evidence["destination"] = snapshot(value)
        with self.connection:
            self.connection.execute("UPDATE files SET state='done', evidence=? WHERE position=?",
                (json.dumps(evidence), position))

    def copy_receipt(self, position, role):
        row = self.connection.execute("SELECT evidence FROM copies WHERE position=? AND role=?", (position, role)).fetchone()
        return json.loads(row[0]) if row else None

    def remember_copy(self, position, role, value):
        with self.connection:
            self.connection.execute("INSERT OR REPLACE INTO copies VALUES (?, ?, ?)",
                                    (position, role, json.dumps(snapshot(value))))

    def retarget(self, plan, position, entry):
        # Persist a collision redirect before writing at its newly allocated path.
        entries = list(plan.entries)
        entries[position] = entry
        data = plan_data(plan)
        data["entries"][position] = dict(source=str(entry.source), destination=str(entry.destination), root=str(entry.root),
            digest=entry.digest, snapshot=snapshot(entry.snapshot), action=entry.action,
            backup=str(entry.backup) if entry.backup else None, existing=str(entry.existing) if entry.existing else None)
        with self.connection:
            self.connection.execute("UPDATE job SET data=? WHERE id=1", (json.dumps(data),))
            self.connection.execute("DELETE FROM copies WHERE position=?", (position,))
        plan.entries[:] = entries

    def close(self):
        self.connection.close()


def load_job(identifier, local_root=None):
    from .library_jobs import LibraryJobEntry, LibraryJobPlan
    path = journal_path(identifier, local_root)
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as connection:
        data = json.loads(connection.execute("SELECT data FROM job WHERE id=1").fetchone()[0])
    if data["id"] != identifier:
        raise ValueError("Saved job identity does not match its filename")
    entries = []
    for entry in data.pop("entries"):
        entry["snapshot"] = SimpleNamespace(**entry["snapshot"])
        for key in ("source", "destination", "root", "backup", "existing"):
            entry[key] = checked_path(Path(entry[key])) if entry[key] else None
        entries.append(LibraryJobEntry(**entry))
    data["source_roots"] = [checked_path(Path(root)) for root in data["source_roots"]]
    for key in ("migration_target", "backup_root"):
        data[key] = checked_path(Path(data[key])) if data[key] else None
    plan = LibraryJobPlan(entries=entries, **data)
    if plan.mode not in {"merge", "migrate", "reorganize"} or not plan.source_roots:
        raise ValueError("Invalid saved library operation")
    output_roots = {checked_path(Path(plan.config["destination_root"]))}
    output_roots.update(checked_path(Path(item["root"])) for item in
                        plan.config.get("library_destinations", []) if item.get("root"))
    for entry in entries:
        source_roots = plan.source_roots if plan.mode == "migrate" else [*plan.source_roots, *output_roots]
        if not any(entry.source.is_relative_to(root) for root in source_roots):
            raise ValueError("Saved source is outside its library")
        if plan.mode != "migrate" and entry.root not in output_roots:
            raise ValueError("Saved output library is not configured")
        if entry.action not in {"Keep", "Copy", "Duplicate", "Move", "Conflict review", "Move library", "Copy (retain original)"}:
            raise ValueError("Unknown saved file operation")
        if not entry.destination.is_relative_to(entry.root):
            raise ValueError("Saved destination is outside its library")
        if entry.backup and (not plan.backup_root or not entry.backup.is_relative_to(plan.backup_root)):
            raise ValueError("Saved backup is outside its library")
        if plan.mode == "migrate" and (entry.root != plan.migration_target or
                entry.destination != plan.migration_target / entry.source.relative_to(plan.source_roots[0])):
            raise ValueError("Saved migration paths are inconsistent")
    return plan


def pending_jobs(root, local_root=None):
    directory = (Path(local_root) if local_root else local_state_directory()) / "library-jobs"
    results = []
    for path in sorted(directory.glob("*.sqlite3")):
        checked_path(path)
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as connection:
            row = connection.execute("SELECT data, phase, error FROM job WHERE id=1").fetchone()
        if row is None:
            continue
        data, phase, error = json.loads(row[0]), row[1], row[2]
        relevant = [*data["source_roots"], data["config"]["destination_root"], data.get("migration_target")]
        if str(root) in relevant and phase != "activated" and (phase != "complete" or data["mode"] == "migrate"):
            results.append(dict(id=data["id"], mode=data["mode"], phase=phase, error=error,
                                sources=data["source_roots"]))
    return results


def migration_pending(root, local_root=None):
    return any(job["mode"] == "migrate" and str(root) in job["sources"]
               for job in pending_jobs(root, local_root))


def mark_activated(identifier, local_root=None):
    with closing(sqlite3.connect(journal_path(identifier, local_root))) as connection:
        with connection:
            connection.execute("UPDATE job SET phase='activated' WHERE phase='complete'")
