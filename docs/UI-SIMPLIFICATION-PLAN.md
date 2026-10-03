# Simpler workflows and manual integrity checks

Review date: 2026-10-02. This records the agreed scope. Backend source work has
started; see HANDOFF-manual-integrity-and-simplification.md for the tested checkpoint.
The installed application and existing diagnostic package have not changed.

## Accepted direction

Checksums become a manual library-management tool. Ordinary copying, moving,
reorganization, and backup creation use completion and size/source-change checks.
Keep staged writes, durable operation records, cancellation, and recoverable source
cleanup. These support reliable file operations without extra checksum reads.
Retain existing integrity records; do not erase them when changing defaults.

Place a **Check files** action on each library. It opens three clear actions:

- **Check against saved checksums**: verify selected files or the whole library.
- **Compare with backup**: explicitly select a configured, connected backup and
  compare contents. Missing backup files and unavailable drives remain distinct.
- **Create checksum record**: save current file hashes for later checks, without
  overwriting older baselines. Explain that this records today's contents rather
  than proving historical health.

Offer **Restore from backup** for failed checks. Preserve the damaged file and
use an explicitly verified backup candidate. Use SHA-256; algorithm selection
need not appear in the ordinary interface. Full-memory crash diagnostics remain
under Help, separate from media integrity.

## Confirmed sources of extra checksum I/O

- `photocard/organizer.py`: card moves select move_checksum_algorithm; backup
  copies select replica_verification. Both currently default to SHA-256.
- `photocard/library_jobs.py`: merge planning hashes same-sized candidates and
  compares saved baselines in some paths; execution selects SHA-256 outside
  checksum-disabled migration. An optional migration checkbox alone does not
  implement a manual-only checksum policy.
- Conflict comparison, pending-transfer reconciliation, and verified restore
  also calculate hashes. Each needs an explicit decision, not a blanket removal
  of checksum calls.

Exact duplicate comparison runs only for filename or destination-path conflicts.
Do not add a whole-library duplicate scan. Both identical and different conflicts
go to the library's Conflicts folder, preserving the intended hierarchy with unique
filenames. Ordinary jobs continue without a conflict prompt. Deletion is an explicit
review action, never an automatic duplicate policy. Filename, camera, size, and
timestamp do not prove identical contents. Restart cleanup requires durable file
identity and transfer evidence; an unrecorded existing destination is not proof of
a completed copy.

## Navigation: fourteen pages to five main areas

| Main area | Existing functions brought together |
| --- | --- |
| Libraries | Selected-library details, organization, add media, export, move, backups, manual checks |
| Sources | Cards, watched folders, travel libraries, mounted USB/network/cloud folders |
| Transfers | Current progress, history, pending recovery, retry remaining, conflicts |
| Settings | Application defaults, monitoring, advanced conflict/space policies, portable settings, maintenance |
| Help | Manual, diagnostic logs, diagnostic export, version and credits |

Library settings take precedence where explicitly customized. Show **Use app
defaults** versus **Custom for this library** and the effective rules together.
Keep Photos/RAW and Videos destinations easy to configure. Global Organization
should become default rules in Settings rather than an unrelated main page.

## Condense or omit from the everyday interface

1. Combine **Import or merge** and **Merge library** into **Add media**, retaining
   source-specific handling internally. Default to the currently selected library.
2. Keep **Add media** and **Export** visible. Place occasional management actions
   in a labeled **Manage library** menu, including Reorganize, Move, Locate moved
   folder, and Forget. These remain distinct operations with accurate summaries.
3. Replace Digest terminology with **Watched folders**. Travel sources and shared
   transfer hubs belong in Sources with their distinct copy-only rules visible.
   Do not imply that mounted-folder support is authenticated network transport.
4. Remove the second whole-library verify button. One Check action with a clear
   whole-library/selected-files scope is sufficient.
5. Place backup destinations in library details, with one settings surface for
   destination, enabled/required status, and manual comparison. Remove the normal
   backup checksum algorithm selector.
6. Move checksum-log filename templates, identity/history naming, low-level retry
   counts, fallback chains, and online place-name provider settings to Advanced.
7. Condense confirmation text to source, destination, copy/move, organization,
   backups, and source removal. Provide details on demand. Avoid separate
   algorithm warnings for actions that do not calculate checksums.
8. Show conflict counts and Retry/Resume in Transfers. Preserve the existing
   side-by-side conflict review and damaged-file retention.
9. Avoid a separate dashboard when Libraries and persistent transfer progress
   provide the same status. Keep capacity and disconnected-drive indicators.

Do not remove folder rules, configurable extensions, sidecar association, backups,
or operation recovery: these are central requirements. Watermarking, bracket/interval
grouping, and advanced export options can stay within Export and appear only when
needed. Defer themes, notification sounds, portfolio tools, and additional wizards
until the core workflows are stable.

## Implementation order

1. Preserve the existing diagnostic package for reproducing the transfer crash.
2. Implement and test the manual checksum policy, including saved settings and
   recovery semantics. Inspect duplicate handling and backup restoration explicitly.
3. Consolidate navigation and library actions around existing processing APIs.
4. Validate ordinary import, cross-drive move, reorganization, conflicts, manual
   comparison, disconnected backups, and restart recovery with disposable media.
5. Update the guide and package a new preview after reviewing the resulting UI.

The source implementation is in progress. No release, installation change, or
removal of configured libraries has been performed.
