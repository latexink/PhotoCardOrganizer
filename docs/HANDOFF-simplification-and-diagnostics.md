# Focused handoff: reliability and simpler workflows

## Scope and budget

The user wants fewer overlapping features and less disk I/O. Prioritize diagnostics,
reported failures, and clear existing workflows. Defer tagging, themes, routing
wizards, and other new features. Check live usage; checkpoint at 5% remaining in
the five-hour window. Do not build or replace the installed app until this source
pass is ready and the user requests that release step.

Work only in this repository. Keep real libraries untouched; use disposable media.
The installed version is 0.11.2. Current changes are uncommitted.

## Evidence collected on 2026-09-19

- Windows Application events show PhotoCardOrganizer 0.11.2 access violations
  (0xc0000005) in Qt6Core.dll 6.11.2 at 2026-09-16 23:39:55 and
  2026-09-17 00:01:59. Offsets differ. These establish native crashes but do not
  establish a root cause. No dump or native stack was available in this pass.
- LibraryJobDialog invalidation left visible rows whose renderer dereferenced
  self.plan after it became None. Fixed by clearing rows before invalidation and
  capturing immutable rendering values. Source path formatting also called
  resolve() on each rendered cell; replaced with plan paths without disk probes.
  This is a confirmed bug, not a proven explanation of the native crashes.
- Forget explicitly refuses the last library. normalize_config recreates a
  default library when the list is empty. Other removals only mark settings dirty.
  A reliable empty state needs coordinated config, monitor, and UI changes; do not
  just remove the UI guard. Offline last-library removal is a user requirement.
- Reorganization computes library-specific overrides before global rules.
  Saving a preset creates per-library overrides, so later global changes may be
  masked. Explain and expose inheritance clearly; do not silently erase overrides.
  The reported current-rules failure has not been reproduced.
- Migration currently copies and retains originals. It updates saved root only
  after the job dialog succeeds. Durable restart recovery is incomplete.

## Implemented foundation

- photocard/diagnostics.py: rotating application error logs (2 MiB, three backups),
  main-thread and worker exception hooks, native faulthandler output preserving
  the previous launch report, a detailed-level setter, and redacted ZIP report
  export. Hooks restore on close.
- CLI initializes diagnostics after acquiring the singleton, applies the saved
  detailed-logging preference after configuration loads, and closes on exit.
  --version and --check-gui retain their existing early paths.
- Reorganization, merge/migration, and monitor worker failures now log Python
  tracebacks as well as showing the concise activity message.
- Preview invalidation/rendering fix described above and focused tests.
- Help & about contains saved Detailed diagnostic logging, Open log folder, and
  Export diagnostic report controls. Exported reports contain redacted logs only;
  they do not include media or settings. The page scrolls at compact window sizes.
- Forget now persists immediately, including the final configured library. The
  explicit empty-library state pauses monitor ingestion and rejects manual imports
  until an enabled destination is added. No library media or metadata is removed.

## Next implementation steps

1. Add operation IDs/stages and selective Qt message handling without logging every
   file or duplicating handlers. Keep minimal error evidence enabled. Local logs
   can contain paths; redact exports and never include media or full settings. Test
   native-fault capture only in an isolated child process, never the main client.
2. Reproduce the reported current-rules behavior with small test configurations.
   Library overrides still intentionally take precedence over global rules; expose
   that inheritance more clearly without silently erasing presets.
3. Consolidate overlapping commands into Add media, Move library, and Reorganize.
   Keep final summaries; detailed previews optional. Make library overrides and
   global inheritance visible. Locate moved library must reconnect manually moved
   folders without merging them into themselves.

## Transfer and recovery design for the subsequent core pass

- Move library defaults to move; Keep originals selects copy. Same-filesystem
  operations use no-overwrite rename with no media hashes. Use filesystem identity,
  not drive letters. Space planning must not reserve a duplicate copy for rename.
- Latest user preference supersedes prior checksum-heavy defaults: routine primary
  operations use basic checks; automatic checksums are for backup libraries.
  Exact-content deduplication remains explicit because it requires content reads.
  Keep ambiguous duplicates and route them to review; do not infer byte identity
  solely from size and timestamps. Retain existing integrity baselines.
- Recovery needs durable plan ID, source/destination identity, snapshots, operation
  stage, and committed progress. Persist intent BEFORE renames/deletion. Reconcile
  filesystem state after crashes; never blindly replay deletes. Update library
  location/catalogs and cleanup through a recoverable state machine.
- Cross-filesystem move: staged copy, size/source-change checks, durable destination
  commit, then source cleanup. Do not delete anything whose identity changed.
  Account for crashes between cleanup, settings commit, and metadata relocation.
- Automatically retry transient file failures with bounded backoff; continue
  independent files. Pause the destination on disconnection/full disk, persist
  unresolved items across restarts, and expose Retry remaining. Permission,
  authentication, and integrity failures must not loop endlessly.
- Use bounded batches and existing I/O coordination. Compute backup hashes during
  copy and verify once. Do not adopt an unbounded in-memory checksum queue or claim
  an HDD speedup without measurement. Avoid background scans competing with moves.

## Handoff boundary

### Astra checkpoint, 2026-09-19

Source review found and corrected additional issues in the previous pass:
- Forget no longer clears or discards unrelated pending settings. Save failures
  preserve selection and configuration. Removal commits under the monitor scan
  lock and refuses while busy. Replacement defaults are named in confirmation.
- Monitor rechecks configuration/pause state after acquiring the scan lock, so a
  queued scan cannot reactivate a forgotten destination using an older snapshot.
- Configuration schema is now 7. Older clients must reject the explicit empty
  library state rather than silently rebuilding and using the old destination.
- Reorganization defaults to saved library rules. A separate Global rules from
  Organization choice bypasses overrides explicitly; saving that choice removes
  selected overrides and restores inheritance. Applying saved rules does not
  create new overrides. No broad navigation rename was done.
- Diagnostics record operation boundary IDs at normal verbosity, Qt warnings and
  errors, and manual reorganization exceptions. ZIP export now uses a fixed file
  allowlist, includes rotated logs, limits reads to 2 MiB per log, and handles
  Windows path case/slash variants. Relative names and paths outside known roots
  may still be present; exported reports need review before sharing.

Validation: 118 targeted tests passed; git diff --check passed. Output is in
build/core-review-tests.log. Targeted tests cover pending
settings, failed saves, monitor locking, global versus per-library rules, and Qt
message-handler restoration. No native crash has been reproduced; synthetic
native-fault child-process testing remains outstanding. No build or install.

Next core session: first read the test result above and current git diff. Then
exercise dialogs in isolated child processes with repeated preview/cancel/change
cycles. Inspect library job replay: migration currently refuses a nonempty target
after interruption, and job plans are not persisted for restart. Implement durable
plan storage/reconciliation before changing migration to move-by-default. The
existing move/copy semantics were deliberately left unchanged in this budget pass.

UI logging controls, report export, wording, and documentation are suitable for
Terra. Native crash diagnosis and the destructive-operation recovery state machine
still merit a focused Astra pass if they remain unresolved. Do not label them
routine UI work or claim the reported crashes are fixed without evidence.

### Astra recovery checkpoint, 2026-09-20

This checkpoint supersedes the copy-only migration notes above. Source changes
remain uncommitted. No installer, release, push, reinstall, or real-library test
was performed. Preserve all pending changes.

Implemented and exercised with disposable data:
- SQLite library-job journals persist the immutable plan, directory identities,
  file snapshots, committed destinations, execution method, and finalization phase.
- Libraries has Resume interrupted operation. Completed files use snapshot checks
  rather than another content read. Failed dialogs expose Retry remaining and lock
  the original operation options to avoid changing a partially executed plan.
- Move library defaults to moving. Keep originals selects copy. SHA-256 is optional
  and off by default. Same-filesystem migration renames the whole library using the
  existing no-overwrite primitive; it does not copy or hash media by default.
- Cross-filesystem migration commits copies before source cleanup, with recorded
  cleanup progress. Changed/recreated sources and replaced folders require review.
  Interrupted index updates and completed moves awaiting settings activation resume.
- An unfinished migration prevents Organizer from recreating the old library folder
  or importing there after restart. Activate the saved migration to clear this guard.
- Optional hashes during a folder rename are stored in its migration report;
  existing portable integrity catalogs are preserved. New searchable baselines are
  still created through Integrity, not silently substituted for existing baselines.
- Native failure capture passed in a disposable child process. Repeated Qt preview,
  cancellation, and invalidation passed 18 child-process cases. This does NOT
  reproduce or diagnose the user's original native Qt crash.

Validation: full Windows suite passed 235 tests (one expected symlink skip), in
build/core-recovery-full-tests.log. A subsequent journal-load containment correction
also passed all 10 recovery tests in build/recovery-tests.log. The correction permits
existing output-library media in merge plans while rejecting unrelated paths.
UI screenshots were rendered at 980x660 and 1440x900; the Libraries page and migration
dialog were visually inspected. git diff --check passed.

Remaining scope, not claimed complete:
- Automatic bounded retry/backoff and continuing independent failed items are not
  implemented. Recovery currently requires the explicit resume/retry command.
- Merge still uses the existing content-deduplication/checksum policy. Simplifying
  merge into basic primary-library file operations requires a separate focused pass.
- Recovery records intentionally refuse changed snapshots; an explicit review or
  abandon/replan workflow for irrecoverably changed plans is still needed.
- No Linux runtime, physical cross-device, network-disconnect, power-loss, packaged
  crash reproduction, or installer lifecycle validation was done for these changes.
- Reorganization recovery remains its existing mechanism; the new resume picker
  covers library merge/migration jobs, not every operation in the application.
- The PDF/release documentation and installer have not been updated. Do not ship
  the source checkpoint as a fully validated release.

Handoff: Terra can handle wording, docs, and focused UI tests from this checkpoint.
Keep unattended deletion/retry semantics and the remaining crash investigation in
a focused core review. Latest usage check was 65% of the five-hour allowance used;
refresh usage before continuing and stop at 95% used as requested.
