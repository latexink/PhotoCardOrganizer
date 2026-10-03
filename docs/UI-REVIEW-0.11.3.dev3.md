# Interface review: 0.11.3.dev3

2026-10-03. Source preview only; no installer build or local installation.

## Changes

- Libraries has one primary action row: Add media, Export media, Reorganize library,
  and Move library. Set up library is beside the library summary.
- More holds default-library selection, size refresh, metadata checks, resume and
  forget. Menu labels and enabled states follow the selected library.
- Import, export and file checks are task pages reached through library actions,
  with a back button instead of duplicate library tabs. Other areas retain their
  existing named tabs. The duplicate footer Open library action is hidden here.
- The table prioritizes library name, state, free space, library size and total
  drive size. The selected folder appears below it; type and default remain
  available in saved library details and the default-library summary.
- Import's three steps and final confirmation are preserved. Copy and Move are
  mutually exclusive visible choices, synchronized with the existing settings.
  Optional transfer labels and advanced organization controls are collapsed.
- The reorganization dialog shows only relevant save-layout options. Optional
  preview, cancellation, media selection, cleanup and final confirmation remain.

## Verification

- Full Windows suite: 288 tests, 287 passed, one expected Windows symlink skip.
- Focused interface suite: 59 tests passed.
- Added tests for task/back navigation, transfer-method synchronization, live
  maintenance menu labels, selected paths and advanced-option disclosure.
- Captured real Qt screens with disposable data at 980x660 and 1440x900. Visually
  checked Libraries, import options and reorganization. Default import options fit
  at the smaller size without scrolling. Advanced options remain scrollable.
- Updated screenshots and generated the separate dev3 PDF. Rendered all pages;
  inspected navigation and release history. The final guide is 24 pages.
- No transfer engine or settings schema changes. No real photo-library operations.

The installed dev2 preview and its historical PDF are preserved. Existing native
crash, Linux, installer-lifecycle and physical-drive validation gaps are unchanged.
Settings, backup and travel pages retain their existing layouts; this pass focuses
on the library and import/reorganization workflows.
