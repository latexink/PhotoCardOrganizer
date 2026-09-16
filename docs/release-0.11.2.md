# 0.11.2 - Library Capacity and Migration Choices

Testing prerelease for Windows, with Linux source support.

## Changes

- Libraries now shows cached library size and total drive capacity beside free space.
- Refresh library size measures directory metadata on demand in a background worker, without reading media contents or repeatedly scanning idle drives.
- Migrate library keeps SHA-256 verification enabled by default and now offers faster size-only verification when retaining the original library is sufficient.
- Migration records identify whether each transfer used SHA-256 or size verification.

## Verified

- The Windows suite passed 211 tests with one expected symlink-privilege skip.
- The packaged GUI startup check passed all 14 pages on Qt 6.11.2, and the 24-page PDF guide rendered cleanly.
- Microsoft Defender custom scans found no threats in the installer or packaged application bundle.

## Limits

- Size-only verification detects truncation and source changes but cannot detect same-size content corruption. Use SHA-256 for removable drives, network destinations, and archival migrations.
- Library size is a cached logical file total. It includes library metadata and conflicts, excludes links, and can differ from allocated disk space.
- The unsigned installer remains a testing release. Keep independent backups when importing, migrating, or reorganizing real media.
