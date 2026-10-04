# Interface review: 0.11.3.dev4

2026-10-04. Source preview only; no new installer or local installation.

## Changes

- Default folder rules, Backups & policies and General are direct sidebar choices
  beneath Settings. The redundant Settings tab row is gone.
- Backups and free-space limits share one scrolling page. Conflict naming,
  retries, transfer records and online place names expand when needed.
- Transfer-record folder and filename controls moved out of the media-rule tabs.
  Long built-in folder labels fit at the minimum window width.
- The new-import Copy/Move choice is visible. Hidden fallback paths and disabled
  retry or location fields retain their values; expanding sections does not mark
  settings changed.
- General groups monitoring, settings files and installation. Idle-card scanning
  is under Advanced monitoring; Manage installation remains directly available.
- Travel libraries and shared transfer folders use one page. Copy new files is
  the direct travel action; editing, opening and forgetting sources use More.
  Configured shared-folder profiles expand their section by default.
- Selection-only actions are disabled without a selection. Backup actions no
  longer mistake a previously focused row for an active selection.
- Source tab labels display literal ampersands correctly.

## Verification

- Full Windows suite: 293 tests, 292 passed, one expected Windows symlink skip.
  This includes all 64 interface tests.
- After the final record-folder layout adjustment, seven settings tests, the
  strengthened minimum-width dropdown test and two conditional-control tests
  passed. No second full-suite run was needed for the layout-only adjustment.
- Captured real Qt screens with disposable configuration at 980x660 and
  1440x900. Visually inspected General, backups, expanded record/location options
  and travel, including disconnected source profiles. Dense forms scroll; the
  footer and progress area remain fixed.
- Updated repository screenshots, README, changelog and the versioned guide.
  Rendered the final 24-page PDF; checked the complete page overview, navigation
  page, revised workflows and release history for layout problems.
- Transfer engine and settings schema are unchanged. No real photo libraries,
  settings or installed application files were changed.

The installed dev2 preview, previous PDFs and existing release downloads remain
unchanged. This is not a confirmed fix for the native 0.11.2 crashes. Linux,
physical-drive and isolated installer-lifecycle validation gaps remain open.
