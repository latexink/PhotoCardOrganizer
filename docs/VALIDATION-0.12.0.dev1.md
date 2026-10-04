# 0.12.0.dev1 validation, 2026-10-04

Windows testing preview. Existing installations, real libraries and user settings
were not modified. This is not a confirmed fix for the native 0.11.2 crashes.

## Completed

- Application source: `9da2190b3cd71debf564a087df26d0f10a2bb8ba`.
  The packaging follow-up changes only documentation and the generated guide.
- Windows source suite: 297 tests, 296 passed and one expected symlink skip.
  The final focused UI run passed all 68 interface tests.
- Linux CI for the application source passed all 297 tests with two expected
  skips, plus the CLI version check:
  <https://github.com/latexink/PhotoCardOrganizer/actions/runs/37222749251>.
- Fresh packaging run: all 12 packaging tests passed. Existing dependencies were
  reused; the already-passed full Windows suite was not repeated by the build.
- Built the one-folder PyInstaller application and Inno Setup installer with
  Windows numeric version `0.12.0.1` and display version `0.12.0.dev1`.
- Packaged GUI startup and navigation passed for all 14 internal pages on
  Qt 6.11.2, using a disposable configuration and library. Report:
  `build/windows/work/gui-check.json`.
- Defender custom scans of the final application bundle and installer found no
  threats. Antivirus and real-time protection were enabled. No exclusions or
  protection settings were changed.
- The updated 24-page PDF was regenerated. Revised preview and release-history
  pages were rendered and inspected; the preceding UI pass inspected the full
  overview and revised workflow pages. Bundled and source PDF hashes match.
- The compiler's first invocation hit a PowerShell output-capture error. The same
  Inno script compiled successfully using explicit stdout/stderr log redirection;
  the completed application bundle was reused. No application-code fix was needed.

## Artifacts

- Installer: `artifacts/windows/PhotoCardOrganizer-Installer-0.12.0.dev1.exe`
- SHA-256 file: the installer filename followed by `.sha256`
- Guide: `output/pdf/PhotoCardOrganizer-0.12.0.dev1-User-Guide.pdf`
- Local application bundle: `build/windows/dist/PhotoCardOrganizer`

Installer size: 36,915,485 bytes.

Installer SHA-256:
`f0f80f0a04951413afae6922bea027d46ca52c1b6ce9d8e9c1c9b10394dd2a18`

Guide SHA-256:
`03eff4ad7bb1dd7cbb19799c6fcc806dba25eb825a91c97f3354624b9d78c20d`

## Validation gaps

- Clean installation, upgrade, repair and uninstall were not repeated. The earlier
  Windows Sandbox harness was blocked at its first installer invocation by
  Application Control, and that block remains unresolved. Protection was not
  bypassed. Packaged startup must not be treated as installer lifecycle testing.
- No physical internal-HDD-to-USB-HDD migration, cable interruption or removable
  drive reconnection test was performed. Automated filesystem/recovery tests are
  not a replacement for those checks.
- Linux CI validates source tests and CLI startup, not a Linux package or real
  desktop session. No Linux installer is included in this preview.
- Unsigned binaries can trigger publisher warnings. A clean Defender scan is a
  bounded result, not a guarantee that software is free of all vulnerabilities.

Use disposable media and independent backups when testing this prerelease.
Older PDFs, installers and diagnostic artifacts were preserved.
