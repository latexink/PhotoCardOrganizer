# Interface review: 0.12.0.dev1

2026-10-04. Source preview only. No installer or installed application changed.

## Workflow conventions

- The sidebar is the only main navigation: Libraries, Sources, Transfers,
  Settings and Help. Section headings are not clickable destinations.
- Import, export and file checks are library tasks, reached from Libraries and
  returned from with a Libraries back button. Media tabs in Default folder rules
  remain because they edit distinct rule sets, rather than navigate the app.
- List pages keep frequent actions visible and put edit/open/forget actions in
  More menus. Those actions follow the current selection and running-job state.
- Permanent card identity filenames live in General, not beside the card list.
- Save settings is visible on settings/help pages or whenever settings are dirty.
  Operations still require saved settings, and final confirmations are retained.
- Copy and Move are explicit choices during relocation. Changing that choice
  invalidates a prior plan; a resumed or running plan locks its choices.
- Capture grouping is an expandable export section. Its content scrolls at the
  minimum window size without compressing or overlapping the controls.
- Headings and watched-folder dialogs use the same names as their entry points.
  Help sections and import status use unframed layouts rather than nested cards.

## Verification

- Full Windows suite: 297 tests, 296 passed and one expected symlink skip.
  This includes 68 interface tests, four newly added for this pass.
- Disposable UI capture at 980x660 and 1440x900. Reviewed main pages, expanded
  grouping and identity controls, and move/reorganization dialogs. No real media
  was involved. Old captures in ignored build folders are not release evidence.
- Updated README, changelog, six repository screenshots and the versioned guide.
  Rendered all 24 PDF pages and inspected the overview plus revised navigation,
  move, export and release-history pages at reading size.
- Transfer engine, configuration schema and library metadata formats are
  unchanged. Existing photo libraries, settings and installation were untouched.

## Remaining limits

This is not a confirmed fix for the native 0.11.2 crashes. No packaged startup,
installer lifecycle or physical-drive checks were performed for this source
preview. Linux CI is separate from Linux packaging and real desktop testing.
The last baseline Linux CI passed for ac9af16; check this preview's own CI result
before packaging. Prior PDFs and published downloads are retained.

## Resumption checkpoint

The requested usage guard is 5% remaining in the five-hour window. This pass was
completed above that threshold; no rate-limit reset credit was used. The next
decision is whether to package this interface preview for user testing. Reuse
existing build and verification scripts; preserve the installed application and
real libraries during isolated tests. Do not treat source tests as an installer
validation or a crash-resolution claim.
