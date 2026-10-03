# 0.11.3.dev2 validation, 2026-10-03

Testing preview only. The installed 0.11.2 application, real libraries and user
settings were not modified. Native 0.11.2 crashes have no confirmed fix.

## Completed

- Previous final source pass: 283 tests, 282 passed, one expected Windows symlink skip.
- This pass: 12 packaging tests passed, including preview numeric-version metadata.
- Built with Python 3.12.14, PyInstaller 6.22.2 and Inno Setup 7.1.0.
- Packaged GUI startup/navigation passed for all 14 internal pages, Qt 6.11.2.
- Defender custom scans of the entire application bundle and installer found no threats.
  No exclusions or protection changes were made.
- Regenerated the 24-page guide; rendered and visually checked its navigation page.
  Earlier source passes visually checked the migration and integrity pages.
- Diff whitespace and targeted credential-pattern checks passed.
- Immutable diagnostic dev1 ZIP retains SHA-256
  `c06678f3a6f622272731270c6bd1f193bb65269874a3f2fe804aea0e0e5854a2`.

## Gaps

- Network-disabled Windows Sandbox was launched using the separate
  `PhotoCardOrganizer-dev2-Test.wsb` harness. Its first installer invocation was
  blocked by Application Control. No install, upgrade, repair or uninstall step
  passed. Protection was not bypassed. Report: `build/windows/sandbox-preview-dev2.json`.
- The harness is prepared for clean installation, upgrade from 0.11.2, repair,
  packaged GUI and uninstall with a disposable user-data marker. It refuses to run
  outside the Sandbox account. A compatible isolated VM remains necessary.
- WSL reports that it is not installed. No live Linux runtime validation this pass.
- Cross-filesystem tests simulate the copy branch; physical USB disconnect/reconnect
  and removable-drive behavior remain unverified.
- Packaged GUI startup is not installer lifecycle testing or crash reproduction.

## Artifacts

- `artifacts/windows/PhotoCardOrganizer-Installer-0.11.3.dev2.exe`
- Matching `.exe.sha256` file (authoritative build hash).
- `output/pdf/PhotoCardOrganizer-0.11.3.dev2-User-Guide.pdf`
- Local bundle: `build/windows/dist/PhotoCardOrganizer`.

Do not treat this preview as a validated public release. Use disposable media for
testing. Historical PDFs and the separate dev1 diagnostic package are preserved.
