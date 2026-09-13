# Changelog

## 0.11.0 - 2026-09-13

- Fixed the Integrity whole-library action to start verification, report empty libraries clearly, and clear stale results when switching libraries.
- Added selected-file recovery from multiple configured backups. Only staged copies matching immutable saved checksums are accepted; damaged originals and durable local/portable recovery journals are preserved.
- Added optional per-library folder and filename rules in Edit library > Organization settings. Global rules remain inherited unless overridden. Reorganization saves changes only to the selected library.
- Added removable/network backup types that refuse to create missing mount folders. Required backup failures retain move sources. Existing backup versions use no-overwrite archival renames.
- Improved jade-button text contrast and refreshed the guide and GitHub visuals. Added a Linux CI test job.
- Configuration schema 6 migrates older settings with the existing backup-before-migration workflow. Recovery currently searches matching relative paths in configured backup roots; archived versions can be supplied as a separate backup root. Scheduled repair and automatic backup catch-up remain future work.

## 0.10.2 - 2026-09-13

- Added the graphite, jade, and muted-coral visual theme, a matching SD-card/photo application mark, and a packaged SVG brand asset.

## 0.10.1 - 2026-09-13

- Added persistent progress timing with an estimated time remaining and average application read/write rates.
- Kept timing based on completed transfer payloads, including destination verification reads, without adding filesystem polling or extra media reads.
- Added regression coverage for progress formatting, copy accounting, and UI event timing details.

## 0.10.0 - 2026-09-08

- Added a dedicated Integrity screen for full-library or selected-file checksum checks, missing-baseline creation, cancellation, immutable baseline results, and matching portable/local reports. Legacy catalogs migrate into `.photocard-organizer/integrity` while retaining the original catalog as a rollback copy.
- Made detailed reorganization previews optional while retaining the final action summary and confirmation. Same-filesystem changes now use durable no-overwrite file and compatible whole-folder renames, with resumable catalog-update intents and preserved conflict routing.
- Hash verified copies while streaming, compare the destination once, reuse verified snapshots, filter duplicate candidates by size, reuse persistent metadata, and coordinate bulk copy/checksum I/O to reduce unnecessary disk reads.
- Added Day, numeric Month, Weekday, ISO week-year, capture hour, camera make/model, card ID, and extension organization choices, including an ISO-week tooltip.
- Consolidated navigation into Workspace, Library tools, and Settings; added General options and an updated compact progress surface. Updated packaging checks, icons, shortcuts, and the versioned PDF manual.

## 0.9.0 - 2026-09-07

- Added Libraries > Merge library with SHA-256 content comparison across the receiving library and an incoming folder, saved organization rules, and organized conflict-review destinations. Source files are retained.
- Added Libraries > Migrate library with full verified copying to an empty destination, metadata preservation, live index path relocation, and location commit after success. Original library files remain available for manual cleanup.
- Added portable integrity baselines and matching local audit records for merge jobs; existing baseline mismatches stop processing without replacing the recorded hash.
- Fixed new-dialog preview completion, worker cancellation, monitoring handoff, configured-default preservation, and copying an existing manifest during migration. Added regression tests for these workflows and destination/backup conflicts.
- Updated the versioned PDF manual. This preview does not yet include the complete multi-source/clone wizard, automatic source cleanup, streaming verification optimization, or scheduled integrity checks; media-routing settings remain a backend capability rather than a general import UI.

## 0.8.0 - 2026-09-06

- Added Libraries > Reorganize library: choose media classes and a folder layout, inspect current/proposed paths, and explicitly confirm checksum-verified moves inside the same library. Original filenames are retained; collisions are preserved for Conflict review.
- Optionally commit the selected folder rules for future imports when processing starts, and remove only previously scanned folders that become empty. Cancel does not save changes.
- Preserve a fixed preview snapshot, refuse changed sources, reuse pending verified copies after failures, and update local catalog paths while retaining append-only transfer records.
- Added Libraries > Export media with a named-library selector, media-type and inclusive capture-date filters, matching sidecars, and Select all matching. Group selection cannot bypass filters.
- Replaced export and reorganization preview cell widgets with on-demand table models for large catalogs.
- Reuse one database connection per scan, skip recorded files before metadata analysis, cache unchanged media/sidecar metadata, and scan for abandoned temporary files once per output directory per application session.
- New configurations check connections every 30 seconds and back off unchanged-card file scans up to 300 seconds. Scan now bypasses the delay; saved intervals remain unchanged, and adding a drive does not force rescanning other unchanged cards.
- Flush verified transfer data before completing copies, preserve per-write database commits, and retain required backup and logging checks before deleting originals.
- Updated the versioned PDF guide. No configuration schema change is required; catalog indexes are added without removing existing records.

## 0.7.2 - 2026-09-06

- Fixed Windows packaging so unrelated build-machine DLLs cannot shadow Qt dependencies.
- Added a disposable full-GUI installation check that opens every screen before an installer is produced.

## 0.7.1 - 2026-07-31

- Routed every different-content filename collision into the local conflict-review folder without pausing an active import, while preserving both files and the organized hierarchy.
- Renamed the primary destination screen to `Libraries` and separated its two main tasks into `Set up library` and `Import or merge`.
- Clarified that connecting a library destination does not scan, import, or move media, while importing or merging always uses the reviewed copy or verified-move workflow.
- Carried the selected or default destination into the import workflow and made unavailable selections fall back to an enabled library.
- Replaced the generic metadata-upgrade action with state-specific initialize, check, upgrade, or repair wording that explicitly leaves media unchanged.
- Hid optional storage-profile choices behind `Show storage options` to keep ordinary library setup focused.
- Hid event/project routing fields until a named import grouping is selected and kept long destination paths readable through a full-path tooltip.
- Expanded the automated suite to 120 passing checks with one expected Windows symlink-permission skip.

## 0.7.0 - 2026-07-28

- Added a dedicated Library management page for multiple named local, OS-mounted network, and removable primary destinations with a selectable default.
- Added versioned `library.json` metadata under `.photocard-organizer`, including explicit initialization, future-schema refusal, backup-before-upgrade, atomic replacement, and resumable legacy export-session migration.
- Migrated configuration schema 4 to schema 5 while retaining the existing primary library, stable library identity, local paths, and portable client-settings behavior.
- Added case-by-case Wedding, Client Shoot, Trip, and safe custom destination routes to card and existing-library imports.
- Added a standalone Month organization level and optional high-confidence long-exposure bracket folders based on capture time, camera, shutter duration, and exposure bias.
- Reduced bracket-analysis I/O by pre-reading only JPEG/RAW candidates when the conditional folder token is active, reusing that metadata during transfer, and applying matching groups to sidecars during normal processing.
- Added a persistent full-width progress center, visible combo-box indicators, read-only managed destination fields, and a guided existing-library `Save + Process` action.
- Changed Save Settings emphasis so it appears only for unsaved configuration and blocked normal processing from silently using live, unsaved controls.
- Kept mounted network authentication under the operating system for this release; no application-managed remote credentials or inbound service were added.
- Expanded migration, metadata, organization, bracket-confidence, I/O, export-log, saved-settings, and workflow coverage to 117 passing tests with one expected Windows symlink-permission skip.

## 0.6.1 - 2026-07-27

- Replaced the ambiguous large/deep-library warning with separate preview-limit and folder-depth explanations that explicitly confirm the eventual import scans the complete selected scope.
- Corrected the 10,000-file analysis boundary so an exactly full preview is not reported as truncated unless another filesystem file actually exists.
- Increased detected and editable source-folder mappings from four to twelve levels and made the mapping dialog vertically scrollable without horizontal overflow.
- Expanded the main Organization editor with add/remove destination levels and editable fixed-folder values so deep detected mappings remain intact through review, configuration reload, and execution.
- Limited preview counts and depth reporting to enabled media and folders that actually contain it, avoiding mappings or warnings caused by disabled, irrelevant, or unsupported files.
- Added Digest queue visible-versus-total counts and explicit omitted-error counts wherever long retained result lists are summarized.
- Made destination-redirection and conflict-archive exhaustion fail explicitly instead of ending at a hidden internal boundary.
- Renamed the standalone Windows artifact to `PhotoCardOrganizer-Installer-0.6.1.exe`, clarified that `PhotoCardOrganizer.exe` is the application launcher, and added a Start Menu uninstall shortcut.
- Added focused deep-hierarchy, exact-preview-boundary, redirect-exhaustion, scroll-layout, installer, and end-to-end import regression coverage.

## 0.6.0 - 2026-07-27

- Replaced the long existing-library form with a guided Source, Organize, and Review workflow whose navigation remains pinned at minimum window size.
- Stopped folder selection from opening a premature structure-analysis prompt; analysis is now an explicit read-only action after source scope and media types are configured.
- Unified existing-library validation, on-page review, confirmation, and execution around one generated import plan.
- Added readable recursive/top-level scan scope to import summaries and automatic invalidation of detected mappings when the source, recursion setting, or enabled media changes.
- Added an end-to-end UI test proving a guided existing-library copy runs only after confirmation, retains source media, and never creates card identity metadata in the source.
- Split direct laptop reconciliation and shared USB/SMB/cloud-folder transfer hubs into separate Travel sync tabs.
- Added a disabled accent-button style so unavailable primary actions no longer look ready across import, sync, digest, hub, and export workflows.
- Made the cumulative changelog readable inside the application and made the Windows Start Menu shortcut open it explicitly with Notepad.
- Expanded workflow, summary, tab-separation, structure-invalidation, changelog, and packaging regression coverage.

## 0.5.0 - 2026-07-26

- Added retained Digest Inboxes for mixed legacy folders, removable media, network paths, and locally synchronized cloud folders.
- Added per-file pending, processed, failed, and conflict state plus one retained run record for every digest session.
- Kept copy as the Digest Inbox default, made background digestion copy-only, and required a separate manual warning before verified move.
- Added automatic copy polling with configurable intervals and local-manifest skipping of unchanged source files.
- Added the same JPEG, RAW, video, sidecar, EXIF/XMP organization, backups, conflict, retry, space, and verification policies to digest sources.
- Added configuration schema 4 migration and portable Digest Inbox definitions that retain each client's local path and monitoring state.
- Added paged and searchable conflict history with true counts, bounded 200-row pages, multi-selection, and bulk review status updates.
- Added a final Help & about page with a version-matched PDF manual launcher and project credits.
- Added a stable Windows taskbar application identity, a multi-resolution live icon, a 1024-pixel release icon, and dedicated Linux desktop icon assets.
- Added Windows/Linux CLI commands for one or all saved Digest Inboxes.
- Expanded integration, migration, packaging, icon, conflict-scale, and Qt workflow coverage.

## 0.4.0 - 2026-07-26

- Consolidated Windows install, repair/upgrade, and uninstall maintenance behind the same distributed Setup executable.
- Added retained copy-only travel-library sources for repeat laptop-to-desktop reconciliation.
- Added transport-neutral Shared Transfer Hubs for removable USB drives, SMB/NAS paths, and locally synchronized cloud folders.
- Added separate publish and catch roles, per-client producer channels, verified offline backfill, automatic catch polling, and matching local/shared digestion receipts.
- Added a library export workflow for individual or multiple captures with SHA-256 verification and separate export-session records.
- Added JPEG/RAW/sidecar capture cohesion across configured media-root folders, plus bracket/burst and regular interval-sequence detection.
- Added editing-export source-change detection, destination-race protection, and configured free-space reserve enforcement.
- Added per-client abandoned-partial cleanup after an interrupted import, hub publication, or editing export.
- Reflowed dense Dashboard, Travel sync, and Library export controls, stabilized wizard browse buttons, and made form scrolling consistent over option fields.
- Accepted UTF-8 byte-order marks in configuration, portable settings, and card identity JSON created by common Windows tools.
- Added configuration schema 3 migration and portable definitions that omit machine-specific travel and hub paths.
- Added end-to-end tests for a fresh laptop publication into an existing desktop master, idempotent repeat catch, and offline hub backfill.

## 0.3.0 - 2026-07-18

- Added a per-user, upgradeable Inno Setup wizard with repair/upgrade detection.
- Added selectable Start Menu, desktop, and login-monitoring shortcuts.
- Added an uninstall flow that preserves user data by default and never targets imported libraries or card records.
- Added versioned configuration migration with pre-migration backups and future-schema protection.
- Added PyInstaller release bundles and synchronized release metadata.
- Added Debian and AppImage packaging with a functional Linux CLI and complete argument forwarding.
- Added packaged-runtime detection for installation maintenance and login autostart.
- Added packaging, migration, installation, and autostart regression tests.
- Updated the illustrated PDF user guide to 0.3.0 and made future package builds regenerate it from project version metadata.

## 0.2.0 - 2026-07-15

- Added the PySide6 desktop and tray interface, multi-card queue, library import, structure detection, retained card profiles, backup destinations, conflict review, and workflow polish.

## 0.1.0 - 2026-07-15

- Preserved the first working camera-card import release as a separate snapshot.
