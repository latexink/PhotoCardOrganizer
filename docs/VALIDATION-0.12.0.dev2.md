# 0.12.0.dev2 Testing Preview Checks

Checked on Windows on 2026-10-04. Real photo libraries were not changed. The
user-authorized local upgrade completed, with the settings file unchanged.

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
- Linux CI passed 308 tests with two expected skips and the CLI version check
  for application source `1a1bb31157502adb1ce37ceaf4eee19dd485cd62`:
  <https://github.com/latexink/PhotoCardOrganizer/actions/runs/37231536949>.
- All 12 packaging tests passed. Reused the completed source tests instead of
  repeating them during packaging; application code did not change afterward.
- Packaged GUI startup passed all 14 internal pages on Qt 6.11.2.
- Defender custom scans of the application bundle and installer found no threats.
  Antivirus and real-time protection were enabled; no exclusions were added.
- Built the versioned Inno Setup installer. The restricted compiler invocation
  was denied; compiling the same tested bundle with approved access succeeded.
- Local upgrade from 0.12.0.dev1 completed with exit code 0 and no restart.
  Backed up the config beforehand, then verified its SHA-256 remained unchanged.
- Installed version and disposable GUI check passed. Existing desktop and Start
  Menu shortcuts are present. No normal import session was launched.
- Installed and source PDF hashes match. Revised guide pages were rendered and
  visually checked.

## Files

- Installer: `artifacts/windows/PhotoCardOrganizer-Installer-0.12.0.dev2.exe`
- Size: 36,917,708 bytes
- Installer SHA-256: `3c2c461c23098a0f2a18b20b31d47269de3106c66a2c9c505b2f7704097076e4`
- Guide: `output/pdf/PhotoCardOrganizer-0.12.0.dev2-User-Guide.pdf`
- Guide SHA-256: `7bee9cfbb7f23c6bdfd79ecec800051e31c7138e8875fbba8ed4122d1b9e3a13`

## Limits

Clean installation, repair, uninstall and physical USB-drive tests were not
performed for this revision. The successful local upgrade is not a substitute
for the full isolated lifecycle suite. Linux source checks passed, but Linux
packages were not built. This is not a confirmed fix for the native 0.11.2 crashes.

Network destinations still use mounted, operating-system-authenticated folders.
No new remote login or sync service was introduced.
