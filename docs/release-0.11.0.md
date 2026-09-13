# 0.11.0 - Library Rules and Verified Recovery

Testing prerelease for Windows, with Linux source support.

## Changes

- The Integrity whole-library button now starts a check and reports empty results.
- Restore selected changed or missing files from multiple configured backups, accepting only copies matching saved checksums. Damaged originals are preserved with recovery journals.
- Configure library-specific folder and filename rules, or inherit global organization. Reorganization saves layout changes only for that library.
- Mark removable/network backups explicitly; unavailable roots are not created. Earlier backup versions use no-overwrite archival moves.
- Graphite/jade theme with improved button contrast, actual application screenshots, and updated 24-page PDF guide.
- Configuration schema 6 preserves older settings through the existing backed-up migration path.

## Verified Locally

- Windows suite: 205 tests run, 204 passed, one expected symlink-permission skip.
- Synthetic import, conflict, reorganization, integrity, recovery, cancellation, and interrupted-operation tests included.
- Packaged Qt 6.11.2 startup and all 14 application pages passed.
- Interface rendered at 980x660 and 1440x900; key new screens visually checked.
- PDF rendered to 24 page images and inspected as a contact sheet.
- Windows Defender custom scans of the installer and complete application bundle returned no threats.
- Historical PDF guides retained. Real libraries and the user's installed application were not modified.

Installer SHA-256:

```text
f2ad82b70d7e1c4c1c50d1156caf0b350f9d1a04e7a1d95b6752f96c35a5fdfd
```

## Remaining Validation and Limits

- Clean installation, upgrade from 0.9.0, repair, and uninstall still require an isolated Windows account or VM. Do not run those lifecycle tests against the user's installation. The packaged executable startup check is not a substitute for installer lifecycle validation.
- WSL/Linux is not installed on the build computer. A Linux GitHub Actions job is included; check its result before claiming Linux validation.
- Recovery searches the same relative path in enabled backup destinations. Add an archived-version root separately to recover an older archived copy. There is no automatic archive search, parity repair, scheduled scrub, or automatic backup catch-up yet.
- A crash during replacement can leave the original and verified staged copy in the integrity recovery folder. Rerun verification and recovery with a connected backup; keep the recovery journal and originals until reviewed.
- The unsigned installer is for testing with independent backups, not a 1.0 stability claim.

## Resume Checkpoint

Finish isolated installer lifecycle checks, inspect the Linux CI result, then update this validation record. Preserve the current source and release artifacts; do not rerun a full build unless a fix is needed. Work stopped to respect the user's five-hour usage reserve.
