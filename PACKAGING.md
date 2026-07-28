# Packaging Photo Card Organizer

Release binaries are built on the target operating system. PyInstaller is not a cross-compiler: use Windows for the Inno Setup package and Linux for the Debian and AppImage packages.

## Versioning

Set the release version in `pyproject.toml` and `photocard/__init__.py`. The packaging tests require them to match. Keep the Inno Setup `AppId` unchanged so a newer package upgrades the existing per-user installation.

The PDF user guide reads the version from `pyproject.toml`. Every package build regenerates `output/pdf/PhotoCardOrganizer-VERSION-User-Guide.pdf`, verifies that it exists, and includes it in the application bundle.

Configuration has its own integer schema in `photocard/config.py`. Add a tested migration whenever that value changes. Older configuration is backed up before an in-place migration, while a newer unsupported schema is left untouched.

## Windows

Prerequisites:

- Python 3.11 or newer with the project dependencies installed
- Inno Setup 7, or a compatible Inno Setup 6 installation

Build from PowerShell or File Explorer:

```powershell
.\build-windows-installer.bat
```

The build installs `requirements-build.txt`, regenerates the versioned PDF user guide, runs the complete test suite, creates a one-folder PyInstaller bundle, smoke-tests its version command, and compiles:

```text
artifacts/windows/PhotoCardOrganizer-Installer-VERSION.exe
artifacts/windows/PhotoCardOrganizer-Installer-VERSION.exe.sha256
```

The setup is self-contained and does not download Python or dependencies on the client. It is unsigned, so Windows may display an unrecognized-publisher warning. Code signing can be added later without changing the stable application ID or configuration layout.

The released Installer executable is separate from the installed `PhotoCardOrganizer.exe` application launcher and is also the user-facing maintenance entry point. When it detects the stable application ID, it offers repair/upgrade or uninstall before changing files. The Start Menu exposes the registered Inno uninstaller directly, while imported media and per-user settings stay outside the application directory.

For repeat builds after dependencies are present:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File packaging\windows\build-installer.ps1 -SkipDependencyInstall
```

## Linux

Build on a Debian- or Ubuntu-compatible Linux host with Python 3.11+, `dpkg-deb`, and `appimagetool` available. Set `APPIMAGETOOL` when the executable is not on `PATH`.

```bash
sh build-linux-packages.sh
```

The build creates and tests the Linux PyInstaller bundle, then writes:

```text
artifacts/linux/photo-card-organizer_VERSION_ARCH.deb
artifacts/linux/PhotoCardOrganizer-VERSION-ARCH.AppImage
artifacts/linux/SHA256SUMS
```

The Debian launcher and AppImage `AppRun` forward every argument. Verify both GUI and CLI entry points on the oldest supported distribution:

```bash
photo-card-organizer --version
photo-card-organizer --scan-once --dry-run
./PhotoCardOrganizer-VERSION-ARCH.AppImage --version
./PhotoCardOrganizer-VERSION-ARCH.AppImage --scan-once --dry-run
```

## Upgrade Checks

Before publishing a release:

1. Install an older package with test configuration and card profiles.
2. Install the new package over it and confirm the wizard reports an upgrade.
3. Verify the configuration backup and migrated schema after first launch.
4. Run a dry scan and a copy-only test import against disposable media.
5. Run the same Installer file, select repair/upgrade, and verify settings remain unchanged.
6. Run the same Installer file or Start Menu uninstall shortcut, preserve user data, reinstall, and verify settings return.
7. Test the optional data-removal choice only with an isolated test account or disposable configuration.

Never perform release verification with move enabled against the only copy of media.
