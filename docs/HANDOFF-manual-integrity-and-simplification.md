# Manual integrity and simpler workflows: resumable checkpoint

Checkpoint: 2026-10-02. Resume in this working repository only:
`C:\Users\vroom\Documents\Codex\2026-07-12\i\work\PhotoCardOrganizer-ui`.
HEAD: `76422afd337b6afd563ac005f351698c29884ec5`.
Source version is now `0.11.3.dev2`; installed version remains `0.11.2`.
The substantial existing uncommitted work was preserved. Nothing was committed,
pushed, installed, or built during this backend pass.

## Latest decisions

- Checksums are manual library actions: check saved records, create missing
  records, compare with a chosen connected backup. Keep existing baselines.
- Exact duplicates are compared only on filename/destination conflicts, not on
  matching sizes across a library. Never automatically discard duplicates.
- Both identical and different collisions enter an organized local Conflicts
  folder, with unique appendages. Transfers continue; source removal and conflict
  resolution remain explicit. Required backup failures retain the source.
- Simplify to Libraries, Sources, Transfers, Settings, Help. Retain existing
  functionality under these areas, not 14 independent main navigation choices.
- Check usage periodically. At 5% remaining in the five-hour window, stop after
  saving tested progress and exact remaining steps. Do not consume reset credits.

These decisions supersede the automatic-backup-checksum suggestion in the older
HANDOFF-simplification-and-diagnostics.md. Real media/settings must stay untouched.

## Completed in this pass

- Config schema 8 migrates old automatic copy/move/replica checksum preferences
  to size checks, disables automatic baseline creation and duplicate prompts,
  and retains the existing pre-migration configuration backup mechanism.
- Ordinary card/folder transfers, new replicas, and library merge/reorganization
  copies no longer hash content. Actual collisions still use SHA-256 comparison.
  Integrity checks and verified restore retain their checksum support.
- Library merge planning no longer groups/hash-deduplicates same-sized files or
  revalidates saved baselines automatically. Different names remain different files.
- Exact and different-content collisions go to Conflict Review with hierarchy and
  appendages preserved. A copy-time destination race is durably redirected too.
- Same-filesystem library-job file moves use no-overwrite rename, without copying
  contents. Existing compatible folder renames remain supported.
- New pending-copy receipts and per-job copy receipts recognize completed copies
  by recorded device/file identity, size and modification time. Failed backups or
  history writes can retry without re-copying/re-hashing the primary file.
- Source-cleanup failure can retry even when portable/local transfer history
  already exists. Local index failure retains the source. Ambiguous legacy pending
  copies are preserved for conflict review, not assumed completed.
- Source receipts recognize previously merged files without content reads. Receipt
  destination paths follow within-library renames. Backup receipts avoid repeating
  content verification for already recorded clone copies.
- Required card backup conflicts are copied into local review without overwriting
  the backup or pausing for a conflict decision. Existing review copies are reused.
  Library-job backup conflicts process other items and leave the job pending for
  review/retry. Migrations still refuse ambiguous destination/backup changes before
  activation, preserving the source; do not pretend they can reroute metadata freely.
- Saved integrity baselines follow moves within or between libraries without
  rehashing. Cross-library baselines are marked verification pending.
- `IntegrityCatalog.compare_backup(config, backup_root, paths=None,
  cancel_event=None, progress=None)` is implemented. It reports matching/different
  bytes, files missing on either side, cancellation and unavailable drives. Reports
  are portable/local; no baselines or media are overwritten. A mismatch alone does
  not establish which copy is damaged. Restore from a trusted saved baseline remains
  the existing explicit path in integrity_restore.py.

## Verification

- Full Windows suite: 258 tests run, 257 passed, one expected symlink skip.
- After the final copy-race and manual backup-comparison additions: 59 focused
  tests passed (manual_transfer_policy, integrity_workflow, integrity_restore,
  library_jobs, job_recovery).
- `git diff --check` passed before checkpoint edits; repeat after the next pass.
- No packaged startup, GUI visual review, installer lifecycle testing, Defender
  scan, Linux runtime test, build, installation or publication in this pass.
- An earlier full run with `logging.disable(logging.CRITICAL)` caused three false
  failures in logging tests; rerunning normally passed. Do not suppress logging in
  the full suite. Expected synthetic failure tracebacks are normal test output.

Commands from the repository:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests
.\.venv\Scripts\python.exe -m unittest tests.test_manual_transfer_policy tests.test_integrity_workflow tests.test_integrity_restore tests.test_library_jobs tests.test_job_recovery
git diff --check
```

## Next pass: GPT-6.1 Sol, Medium

1. Preserve this checkpoint and inspect the dirty tree before editing. No blind
   staging, resets, replacement of older project copies, or real media operations.
2. Finish checksum consistency in export and hub publication. `library_tools.py`
   export still defaults to SHA-256, and `transfer_hub.py` publication still hashes
   every file. Prefer the established size/source-change/staged-copy implementation;
   content comparisons should be conflict-only. Maintain honest verification labels,
   old session-record readability and receipt behavior. Do not add network auth or
   imply that mounted folders constitute secure remote transport.
3. Remove obsolete checksum selectors/checkboxes from ordinary import, safety,
   reorganization, migration and backup controls. New migration plans ignore the old
   flag, but saved old job plans retain their original verification contract when
   resumed. Existing UI summaries still mention SHA-256 and must be corrected.
4. Add Libraries -> Check files using the existing saved-check/baseline/restore
   APIs and the new compare_backup method. One clear scope control, no second whole-
   library button. Never automatically choose a winner when two copies differ.
5. Consolidate navigation and overlapping Add media/merge actions according to
   UI-SIMPLIFICATION-PLAN.md. Keep named page routing rather than fragile numerical
   indexes as navigation changes. Retain per-library rules/inheritance, backup status,
   offline-drive indicators, persistent progress, history/retry and conflict review.
6. Review source receipts during cross-library reorganizations and whole-library
   migration: live index references and external-source-to-destination mappings
   should follow the new location. Currently within-library rename mappings are
   updated, and integrity baselines move across libraries, but cross-library source
   manifest mappings need a focused follow-up. Do not force a content scan to fix it.
7. Test optional-backup/same-disk optimization separately: library jobs with an
   explicit clone currently retain copy-first behavior before source cleanup;
   rename-only optimization is used without an explicit clone. Do not weaken the
   required-backup retention behavior merely to remove a read.
8. Add tests for cancellation during manual comparison, conflict review/delete
   confirmations, new export/hub defaults and UI navigation. Visually inspect at
   980x660 and 1440x900 with disposable settings. Run the full suite once after this
   coordinated pass; do not repeat it for every small documentation edit.
9. Update config.example.json, changelog, README, screenshots and PDF guide only
   after the workflow labels settle. The existing diagnostic ZIP must remain
   immutable. Choose a new preview version before any new build so artifacts do not
   replace the diagnostic build. Do not install into the user's normal application
   or change real libraries. Follow the existing build/Defender/disposable smoke
   tooling when a preview build is ready; disclose any unperformed lifecycle checks.

Escalate to High only for a concrete recovery/protocol correctness issue. Use Astra
only with a focused evidence-backed handoff, not for routine UI wiring or docs.

## Native crash evidence stays separate

The installed 0.11.2 native Qt crashes are not established as fixed. Preserve
`artifacts/PhotoCardOrganizer-Diagnostic-0.11.3.dev1.zip` and the isolated diagnostic
launcher. Full dumps stay local; require the existing CAPTURE confirmation and
never upload dumps automatically. No new real-world diagnostic reproduction has
been reported here.

## Medium pass completed, 2026-10-02

- Five main navigation areas now route through named secondary tabs. Libraries
  opens first; Add media has folder/import and library-combination choices.
  Manage library contains reorganization, move and cached size measurement.
- Check files has one scope selector, saved-checksum checking, baseline creation,
  a chosen-backup comparison with confirmation, cancellation and reports. Recovery
  still requires a trusted saved baseline; comparison mismatches never choose a winner.
- Removed ordinary checksum and duplicate-prompt choices from visible settings,
  onboarding and library dialogs. Compatibility objects remain internal so existing
  saved settings and dialog wiring continue to work. Resumed old plans retain their
  original checksum contract.
- New exports use size/source-state checks. Hash reads occur only on existing-path
  comparisons; an explicit lower-level checksum export API remains compatible.
- Shared-folder publication uses persisted file-identity receipts, not routine
  hashes or checksum logs. Unrecorded/changed hub destinations remain unchanged,
  with incoming files preserved in local Conflict Review. Local conflicts are
  excluded from publication. Old archive-and-replace selections no longer overwrite.
- Updated schema-8 example settings, changelog, README preview notes, screenshots
  and a separate dev2 PDF. Existing dev1 PDF and diagnostic ZIP remain untouched.
- Full suite: 264 tests, 263 passed, one expected Windows symlink skip. After final
  UI-only changes, 68 focused Qt/export/hub tests passed. Diff whitespace check passed.
- Real Qt screenshots were generated with disposable disconnected settings at
  980x660 and 1440x900; Libraries, Check files and Conflicts were visually checked.
  The guide was rendered with Poppler and visually inspected.
- No build, installation, commit, push, Defender scan or Linux runtime test in this
  source pass. No real library/settings changes. Native crashes remain unresolved.

## Next handoff: focused High review before a build

The remaining correctness gate is item 6 above. Review
`library_jobs.relocate_migrated_index` and cross-library `relocate_baseline`/manifest
updates against copied versus renamed file identities. Paths currently relocate,
but copied receipt snapshots can retain old inode/device evidence; originating
source-library mappings may not follow a moved destination. This is conservative
(later imports can become extra review conflicts), but should be repaired without
trusting size/time alone or introducing full-media scans. Add focused retry tests
for whole-folder rename, cross-filesystem copy/move and cross-library reorganization.
Do not overwrite immutable historical session records.

Then review unresolved required-backup conflicts for a clear explicit retry workflow
(marking reviewed is not replacement), and run targeted recovery tests. Source dev2
is not ready for a public release until these gates and isolated packaged checks are
complete. Keep the earlier dev1 diagnostic package available for crash reproduction.
Do not install into the real app or libraries. Continue economical usage checks and
save a checkpoint if the five-hour allowance reaches 5% remaining.

## High correctness pass, 2026-10-02

The source remains dev2. Installed 0.11.2 and immutable diagnostic dev1 artifacts
remain untouched. Existing dirty work was preserved; no commit or push.

- Whole-library migration now refreshes copy/pending/conflict receipts only from
  the verified operation's before/after evidence, in the same transaction as path
  relocation. Stale identities remain stale. Reapplying finalization is idempotent.
- Cross-library moves carry proven origin receipts and completed import mappings
  to the target before updating the source index or deleting media. Pending imports
  are not falsely promoted to completed target history. Earlier indexes without
  receipt tables are tolerated. Existing target mappings are not overwritten.
- Within-library relocation no longer legitimizes a stale imported-copy receipt.
- Fixed a cleanup race: migration finalization no longer replaces recorded media
  and backup evidence with later current snapshots. Only the deliberately rewritten
  metadata index is refreshed. Late destination/backup changes retain originals.
- Library-job completion and index relocation use the original destination proof;
  a second source/destination check precedes source removal after index work.
- Added bounded conflict identity records: unchanged required-backup conflicts can
  retry without checksum reads or another review copy, including reviewed entries.
  Changed identities still require comparison; unavailable/missing copies are not
  assumed healthy. These receipts follow copied whole-library migration too.
- Backup review now shows a pending warning and confirms even a single backup
  review. Status updates do not replace backups or unblock transfer completion.
- Added tests for rename/copy/move, interrupted target/source-index commits,
  migration finalization, old tables, stale receipts, backup changes and retry I/O.

The focused correctness gate above is addressed. Next is the packaging/testing
pass, suitable for Medium: inspect final source diff; use existing build scripts
for a distinct dev2 preview (never replace diagnostic dev1); packaged GUI startup,
Defender scans, disposable installer lifecycle checks, physical cross-device and
Linux runtime coverage where available. Do not install into the user's normal app
or operate on real libraries. Native 0.11.2 crashes still have no confirmed fix.

### Final verification and next commands

- Final full Windows suite: 283 tests run, 282 passed, one expected symlink skip.
- Source/backend focused tests also passed during development, including late
  destination changes during index updates. `git diff --check` passed.
- Updated dev2 PDF rendered; modified migration and integrity pages visually checked.
- Diagnostic dev1 ZIP reverified unchanged:
  `c06678f3a6f622272731270c6bd1f193bb65269874a3f2fe804aea0e0e5854a2`.
- Latest usage snapshot: five-hour window 63% used / 37% remaining. No reset credits
  consumed. No shell/test sessions remain running at this checkpoint.

For the next Medium pass, read PACKAGING.md and the existing Windows build script.
The normal entry point is `build-windows-installer.bat`; confirm prerequisites and
ensure outputs are named for dev2 before running. The existing Sandbox test file is
still named for 0.11.1, so inspect/update a separate dev2 test harness rather than
assuming it tests the new build. Do not modify the original dev1 diagnostic bundle.

Remaining validation: packaged startup, Defender installer/bundle scans, disposable
clean install/upgrade/repair/uninstall, Linux runtime, and actual removable-drive
behavior. Current cross-filesystem tests force the copy branch using disposable
folders; they do not establish physical USB disconnect handling. Native dump
investigation remains separate. Commit/push/publication have not been performed.

## Packaging pass, 2026-10-03

Built dev2 and verified all 14 packaged pages. Defender bundle and installer scans
found no threats. Fixed numeric Windows preview metadata; 12 packaging tests passed.
Guide regenerated/rendered; diagnostic dev1 ZIP remains unchanged. Full suite was
not repeated after the packaging-only fix. See VALIDATION-0.11.3.dev2.md.

Sandbox launched, but Application Control blocked the first installer before any
lifecycle step passed. Do not bypass protection or test against the real installation.
Separate guarded dev2 harness is ready for a compatible isolated environment.
WSL is unavailable; physical USB testing remains outstanding. Real libraries and
installed settings remain untouched. Source synchronization is authorized; this is
still a development preview, not a public release or a confirmed native-crash fix.
