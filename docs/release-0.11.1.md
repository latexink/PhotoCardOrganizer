# 0.11.1 - Reliable Catalog Startup

Testing prerelease for Windows, with Linux source support.

## Changes

- Catalog initialization is now atomic, so the Digest Inbox view cannot read a half-created SQLite schema.
- Digest inboxes and transfer hubs run their first eligible monitoring pass immediately after application start.
- Linux CI now handles Windows-only installer checks correctly and passes the full suite.

## Verified

- Targeted Windows regressions cover the catalog startup race, platform-specific installation detection, and first-pass digest scheduling.
- GitHub Actions Linux suite passed for commit `42c86b8` before this packaging release.
- This release build validates packaged GUI startup, scans the installer and packaged application with Microsoft Defender, and provides the versioned PDF guide.
- Windows Sandbox validated a clean 0.11.0 installation, upgrade to 0.11.1, repair, packaged GUI startup across all 14 pages, and uninstall while preserving sandbox user data.

## Remaining Validation

- The unsigned installer remains a testing release. Keep independent backups when importing or reorganizing real media.
