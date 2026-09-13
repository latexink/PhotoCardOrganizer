# Roadmap

This file records candidate work after version 0.7.1. It is not a promise that
every item will ship in the next release.

## Proposed Release Sequence

- Use version 0.7.2 for behavior-compatible maintenance: efficient monitoring,
  scan-plan reuse, metadata and manifest batching, cached storage status, and
  responsive background UI work. Do not require a library schema migration for
  this release.
- Use version 0.8.0 for architectural changes: an incremental library catalog,
  network-safe multi-client state coordination, and virtualized large-library
  views. Migrate metadata explicitly, atomically, and with backups while
  preserving compatibility with older records.
- Add repeatable performance fixtures for a large card, a mixed existing
  library, a sleeping platter destination, and a high-latency mounted library.
  Record scan count, bytes read, database transactions, drive probes, elapsed
  time, and peak memory so each optimization is demonstrated rather than
  assumed.

## Content-Aware Library Digestion

- Add a library-wide content fingerprint catalog so identical media can be
  recognized even when it arrives with a different filename or historical
  folder structure.
- Use a size-first candidate lookup and SHA-256 confirmation before treating
  content as identical.
- Record an already-present result without creating another physical copy.
- Preserve same-name, different-content files through the configured suffix or
  organized conflict-folder policy.
- Keep JPEG, RAW, video, and sidecar capture sets together while reporting new,
  already-present, and conflict counts in the preflight summary.

## Library Reorganization

- Add an explicit Reorganize library action to Library Management when saved
  organization rules differ from a library's recorded layout. Saving settings
  alone must never move existing media.
- Make the final reviewed action Save settings + Reorganize. Atomically save
  the organization options and write an immutable settings snapshot into the
  operation journal before changing media. Process the entire operation from
  that snapshot; if either commit fails, make no file changes.
- Add a read-only Preview organization button beside the organization rules.
  Show representative current path to proposed path mappings, filename
  changes, matched rules, unchanged files, and likely conflicts without
  writing media, settings, history, or manifests. Clearly state the sample
  size and whether the preview was truncated.
- Let the full reorganization wizard build a complete dry-run plan from the
  reviewed settings snapshot before enabling Save settings + Reorganize.
- Let the user scope the operation by named library, folder, date range, camera,
  rating, or media type.
- Show a dry-run plan before changes, including proposed paths, unchanged
  files, exact duplicates, conflicts, temporary and final space requirements,
  unavailable destinations, and files requiring review.
- Default to a staged copy-and-verify operation. Offer a space-efficient move
  only after an additional warning and confirmation, using atomic same-volume
  renames where possible and checksum verification where copying is required.
- Keep capture sets, sidecars, videos, watermarked derivatives, and transfer
  records associated while reorganizing.
- Write a versioned, resumable operation journal under
  `.photocard-organizer`, plus a matching local record. Support pause, restart
  recovery, and rollback where the completed operations make rollback safe.
- Never overwrite a destination. Route different-content collisions through
  the normal suffix, conflict-folder, or interactive review workflow.
- Plan destination writes in stable order to reduce unnecessary seek activity
  on platter drives.

## Watermarked Derivatives

- Keep ingested masters and source files immutable.
- Support optional text, PNG, and safely rendered SVG watermarks.
- Provide position, opacity, image-relative scale, margins, JPEG quality,
  metadata-retention, and preview controls.
- Add per-Digest-Inbox presets for local libraries and OS-mounted remote
  destinations.
- Offer export modes for no watermark, watermarked derivative only, or original
  plus watermarked derivative.
- Allow same-library subfolders, separate local folders, and mounted network
  folders, with normal conflict, free-space, verification, and session-record
  behavior.
- Reject SVG scripts, external resources, and excessive render dimensions.

## Portfolio Preparation

- Add a dedicated Portfolio preparation tab for curating presentation-ready
  selections from existing libraries, without moving or modifying originals.
- Save named collections as references to media; support filtering by rating,
  date, camera, and media type, with manual selection and presentation order.
- Reuse export and watermark presets for destination, sizing, format, quality,
  and metadata privacy, rather than duplicating export settings.
- Preview the selection and export summary before creating derivatives. Handle
  unavailable originals and destination conflicts explicitly without overwrites.
- Keep portfolio preparation separate from library organization; website
  publishing and hosting are not part of the initial scope.

## Card Onboarding Clarity

- Label the card or drive root as the current Windows drive letter or Linux
  mount location, not as an identity. Explain that it may change after
  reconnecting the same card.
- Generate a unique stable card ID automatically during normal onboarding and
  show it in the final review. Put manual ID editing behind an Advanced
  control.
- Never derive the stable ID from a drive letter. Continue discovering cards
  by reading their root `.photocard/identity.json` marker across available
  drive letters and mount points.
- Keep the stable ID read-only after onboarding unless a dedicated identity
  migration workflow safely updates retained profiles and historical indexes.
- Replace the ambiguous Add offline card action with an Advanced pending-card
  profile workflow. State plainly that a pending profile is stored only on the
  current client and does not export or write files to a disconnected card.
- Let connected-card onboarding select a pending profile, review its settings,
  and write the root identity only after confirmation. Existing disconnected
  cards that already have identities should appear automatically as retained
  profiles and should not require a separate Add action.
- Keep portable settings export focused on client configuration. Do not imply
  that it creates a card identity package or requires users to copy internal
  files onto cards manually.

## Guided Setup Wizards

- Target the complete wizard set for a minor feature release such as `0.8.0`;
  keep `0.7.2` focused on behavior-compatible maintenance and performance work.
  The first-run and Add library wizard may ship first if the work is staged.
- Show an optional first-run wizard when no managed library exists. Guide the
  user through naming the library, selecting or creating its destination,
  choosing local/removable/OS-mounted network storage, and confirming that the
  folder is suitable before initializing `.photocard-organizer`.
- Offer the same Add library wizard from Library Management at any time. Prefill
  known settings when adopting an existing managed library, and clearly
  distinguish creating metadata from importing or reorganizing media.
- Add a folder-import wizard for ordinary folders, varied existing libraries,
  Digest Inboxes, travel sources, and shared transfer folders. Let one session
  add and review multiple source folders without starting an import
  prematurely.
- Use a logical progression: source and scan scope, media types, destination
  library and optional named route, detected structure, organization rules,
  duplicate/conflict policy, backups and free-space limits, monitoring, and
  final review.
- Present safe recommended defaults first and place infrequent identifiers,
  templates, checksum choices, and migration controls under Advanced sections.
  Keep field names plain and provide focused hover help.
- Provide read-only structure detection and representative input-to-output path
  examples before saving. The final summary must show every source,
  destination, enabled media class, organization rule, monitoring choice, and
  whether the operation copies, performs a verified move, or only creates a
  retained profile.
- Do not change files, initialize library metadata, or begin monitoring until
  the final confirmation. A move must retain its additional warning and
  explicit confirmation.
- Allow wizard choices to be saved as named presets or retained source
  profiles. Returning users should be able to select a preset, review only the
  changed fields, and proceed through a compact fast path.
- Make Back, Cancel, and resume behavior reliable. Cancel before confirmation
  must leave no partial library, identity, import, or monitoring state; longer
  analysis should be cancellable without discarding already reviewed choices.
- Keep direct expert controls available outside the wizards. Wizards should
  improve discovery and first-time setup without making repeat imports slower.

## Efficient Source Monitoring

- Split monitoring into a lightweight presence check and a media scan. The
  regular 30-second poll should enumerate mount roots and read only the
  `.photocard/identity.json` marker needed to recognize connected cards.
- Prefer Windows device-arrival/removal notifications and Linux mount events
  over periodic filesystem probes. Do not touch unchanged fixed-volume roots
  merely to confirm that they are still present.
- Exclude ordinary library destinations and other fixed disks from automatic
  card discovery unless they are explicitly configured as monitored sources.
- Do not recursively rescan an unchanged card on every presence poll. Run a
  media scan when a card is newly connected or reconnected, its retained
  source settings change, the user selects Scan now, or a watched source folder
  reports a change.
- Use debounced Windows and Linux filesystem notifications while a card remains
  connected so files added by a tethered camera or another application are
  still discovered after the initial connection scan.
- Provide a configurable, low-frequency fallback rescan for missed filesystem
  events and filesystems without reliable notifications. Make avoiding wake-up
  of sleeping platter drives the default.
- Track connection generations so removal and reinsertion at the same drive
  letter or mount path still triggers a new scan.
- Cache free-space and availability values instead of refreshing them on every
  monitor or dashboard tick. Refresh a platter destination for an explicit
  status request, enabled operation, or transfer preflight, when waking it is
  actually necessary.
- Show separate status times for the last presence check and last completed
  media scan. Keep card scans sequential by default to avoid unnecessary
  platter-drive contention.

## Version 0.7.2 Performance Maintenance

- Build one immutable scan plan per operation and reuse it for preview,
  confirmation, digest classification, and execution. Do not walk and stat the
  same source tree again when the reviewed plan is still valid; revalidate each
  source immediately before it is changed.
- Keep one explicitly closed manifest session per operation, use bulk lookups
  and bounded transactions, and check the local manifest before lazily loading
  portable card history. Cache immutable portable session files by filename,
  size, and modified time without rewriting their audit records.
- Reuse the digest plan during execution instead of scanning the source a
  second time. Batch digest status updates while retaining resumable item-level
  state.
- Keep a persistent ExifTool worker or use batch mode, cache its resolved
  executable path, and invoke secondary EXIF readers only for fields that are
  still missing. Cache metadata by canonical path, size, and modified time for
  reuse by import, preview, conflict review, and export.
- Compare file sizes before hashing possible duplicates. Hash the source while
  streaming a copy, verify the completed destination with one read, and reuse
  the resulting content hash for replicas and transfer records. Never weaken
  the verification or durable-record requirements before a move deletes its
  source.
- Preflight aggregate temporary and final space for the reviewed plan. Cache
  capacity during an operation, then revalidate at configurable thresholds and
  before a hard limit instead of probing every destination for every file.
- Cache created destination directories, clean abandoned partials once per
  touched directory, and reserve conflict names per directory in memory while
  retaining an atomic no-overwrite check at commit time.
- Make each library's storage profile operational. Favor a single sequential
  write queue and deferred status probes for HDDs, bounded metadata and hash
  concurrency for SSDs, and conservative batched operations for network
  destinations.
- Move library availability, capacity, metadata-status, and image-preview work
  off the UI thread. Use cancellable workers, bounded thumbnail decoding, and
  cached status so an offline share or large image cannot freeze navigation.
- Prevent system sleep during an active verified transfer using the native
  Windows and Linux inhibition mechanisms, then always release the inhibition
  when the operation finishes, fails, or is cancelled.
- Aggregate repetitive success notifications in the UI while retaining
  detailed per-file records on disk. Keep pause and cancel boundaries between
  files so partial work remains recoverable.

## Version 0.8.0 Library Scalability

- Add an incremental library catalog keyed by stable library identity,
  canonical relative path, size, and modified time. Re-extract metadata or
  content hashes only for new or changed entries, and reuse the catalog for
  digest, export, reorganization, and duplicate detection.
- Do not place SQLite WAL state directly on SMB or other network filesystems.
  Use a client-local transactional index backed by canonical append-only
  library records, or an explicitly leased single-writer design, with defined
  recovery and multi-client conflict rules.
- Replace unbounded table population with paged or virtualized item models for
  large catalogs, activity history, digest queues, and conflict review.
  Preserve stable selection and filtering while pages load.
- Queue work by physical source and destination so simultaneous jobs do not
  thrash one platter drive. Permit bounded parallelism only when the storage
  profiles and independent devices make it beneficial.
- Detect cloud placeholders or recall-on-access files where the operating
  system exposes that state. Include required downloads and quota impact in
  preflight instead of silently hydrating an entire cloud-backed library.
- Deduplicate reverse-geocoding requests by coordinate bucket, share a
  client-local cache across libraries, and negative-cache temporary failures
  with an expiry. Skip geocoding entirely when no active organization rule
  needs a place name.
- After functional packaging tests, prune unused Qt modules and translations
  and defer nonessential page initialization to reduce installer size, startup
  time, and idle memory. Treat this as lower priority than media integrity and
  storage efficiency.

## Program and Packaging Consolidation

- Audit developer launchers, build wrappers, generated resources, and legacy
  maintenance scripts. Remove superseded or duplicate files and keep the
  project root focused on the few entry points a developer actually uses.
- Keep one canonical source of truth for the application version, icons,
  bundled manual, changelog, and installer metadata. Generate platform
  resources from it instead of maintaining matching copies by hand.
- Give the installed Windows application one primary executable, one internal
  runtime/resources directory, and one registered maintenance/uninstall path.
  Do not install source-only batch launchers or duplicate versioned program
  folders.
- Make the single Windows installer executable the maintenance front door.
  Offer Install when the app is absent and Repair, Update, Uninstall, or Cancel
  when an installation is detected. Show the installed and packaged versions,
  the selected operation, affected program location, shortcuts, and preserved
  data before enabling the final confirmation.
- Define Update as an offline upgrade to the version bundled in the installer
  until online update checks are implemented. Repair should verify and replace
  missing or changed installer-owned program files without resetting user
  configuration.
- Detect a running application before maintenance, request a graceful close,
  and verify the selected operation afterward. Show an explicit success or
  failure result and retain a diagnostic log the user can open.
- Keep settings and application logs by default during uninstall, with a
  separate confirmation for removing them. Never offer the installer a path to
  delete imported media, card identity/history data, or library state.
- Build the Windows and Linux packages from a shared payload manifest while
  retaining the genuinely platform-specific installer and launcher code each
  operating system requires.
- Record every installer-owned program file so repair and upgrade can replace
  obsolete managed files and uninstall can remove them without touching
  settings, transfer history, library state, card identities, or media.
- Keep the repository private and do not apply a project license until the
  owner completes an explicit licensing review. Before any public source
  release, select an OSI-approved license, add matching package metadata, and
  document how contributions may be relicensed.
- Audit every bundled dependency before the next distributed package and
  include required third-party notices, license texts, source-access details,
  and relinking rights. Revisit GPL for the desktop and AGPL for a separately
  hosted remote-sync service if application-managed networking is introduced.
- Consolidate redundant files, not unrelated responsibilities. Keep focused
  source modules and split oversized UI or workflow modules when that improves
  maintainability rather than combining code merely to reduce the file count.

## General Options and Interface Themes

- Begin the next iteration with a focused styling review.
- Add a dedicated General options tab that consolidates app-wide settings
  currently scattered across the interface, with one authoritative control for
  each option.
- Place appearance, startup and tray behavior, monitoring defaults, and
  maintenance preferences there. Keep library destinations, organization
  rules, transfer safety, and conflict behavior in their focused sections.
- Use 30 seconds as the monitoring interval for new clients. Keep it
  configurable, preserve existing client values during upgrades, and retain
  explicit per-source overrides.
- Keep the polished dark theme as the default and add selectable system, light,
  and high-contrast themes.
- Store the theme per client so portable library settings do not overwrite a
  computer's display preference.
- Add optional sound notifications during the same interface iteration, with a
  master toggle, volume control, sound preview, and separate choices for card
  connection, operation completion, attention required, and failure. Avoid
  per-file sounds and coalesce repeated background events.
- Provide quiet hours and an option to use native Windows or Linux notification
  sounds. Store audio preferences per client rather than in portable library
  settings.
- Always pair sounds with visible tray, progress-center, and Activity status.
  Never make a transfer warning, move confirmation, or failure understandable
  only through audio.
- Verify every theme on Windows and Linux, including dropdown indicators,
  progress states, dialogs, conflict previews, disabled controls, keyboard
  focus visibility, notification sounds, mute state, and quiet hours.

## Remote Transport

- Continue to support OS-authenticated SMB/NAS and synchronized cloud folders.
- Before adding application-managed remote access, design device pairing,
  passkeys or MFA, short-lived tokens, revocation, encrypted transport and
  storage, least privilege, audit logs, safe path handling, integrity checks,
  and conservative remote-delete defaults.
