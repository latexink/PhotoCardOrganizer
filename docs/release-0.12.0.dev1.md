# 0.12.0.dev1 - One Sidebar, Clearer Library Tasks

An interface testing preview for Photo Card Organizer.

- Navigate sources, transfers and settings directly from one sidebar.
- Use compact card and watched-folder actions, with secondary choices in More.
- Choose Move or Copy explicitly when relocating a library.
- See Save settings when editing settings or when there are unsaved changes.
- Expand export grouping options when needed, with scrolling in small windows.
- Read the updated, version-matched PDF guide.

The Windows installer is self-contained and unsigned. It supports installation
and maintenance, but install/upgrade/repair/uninstall lifecycle validation is
incomplete: Application Control blocked the earlier Windows Sandbox harness.
Packaged startup is not installer lifecycle testing. Do not bypass protection.

Windows and Linux source tests passed. Packaged startup and navigation passed on
Windows. Defender scan results and the installer hash are in
`docs/VALIDATION-0.12.0.dev1.md` in the repository.

Use disposable media and keep independent backups. This is not a confirmed fix
for the native 0.11.2 crashes. Real USB-drive interruption and Linux package tests
remain outstanding. The maintainer's existing installation, libraries and
settings were not modified while preparing this package.
