# 0.12.0.dev2 Source Preview Checks

Checked on Windows on 2026-10-04. Real photo libraries, user settings and the
installed application were not changed.

## Completed

- Final Windows suite: 308 tests, 307 passed and one expected symlink skip.
- GUI startup and navigation passed for all 14 retained internal pages on
  Qt 6.11.2. The visible sidebar has six main pages.
- Rendered the real interface at 980x660 and 1440x900 using disposable state.
  Checked Libraries, Sources, import options and Settings for clipping and
  navigation consistency; retained scrolling for smaller windows.
- Updated the 24-page PDF guide. Rendered the guide, inspected the contact sheet
  and checked revised navigation, import and release-history pages at readable size.
- Verified temporary import choices do not alter saved defaults, and explicit
  rule saves affect only the chosen library and media layouts.
- Verified older source identities and selections survive the simpler menus.
  Offline and busy sources cannot start another import through the new controls.
- Synthetic-media tests cover reusing unchanged older merge receipts, completing
  required backups and preserving conflicts when a recorded destination changes.
- Exports exclude the configured conflict folder unless explicitly requested.
- Reviewed the final diff and refreshed the GitHub description. Historical PDF
  guides remain unchanged; the previous README is archived separately.

## Limits

This is a source preview, not a new installer. No Windows build, Defender scan,
installation, upgrade, repair or uninstall was performed for this revision.
The latest packaged preview is still 0.12.0.dev1.

Linux validation is provided by the cross-platform GitHub workflow after pushing;
its result must be checked separately. Physical USB-drive and installer lifecycle
checks remain outstanding. This is not a confirmed fix for the native 0.11.2 crashes.

Network destinations still use mounted, operating-system-authenticated folders.
No new remote login or sync service was introduced.
