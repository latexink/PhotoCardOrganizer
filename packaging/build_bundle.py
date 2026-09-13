from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from photocard.config import CURRENT_CONFIG_SCHEMA


def isolated_environment(*, build: bool = False) -> dict[str, str]:
    environment = os.environ.copy()
    for name in ("PYTHONPATH", "PYTHONHOME", "QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH", "QML2_IMPORT_PATH"):
        environment.pop(name, None)
    if os.name == "nt":
        windows = Path(os.environ["SystemRoot"])
        paths = [windows / "System32", windows]
        if build:
            paths.extend([Path(sys.executable).parent, Path(sys.base_prefix), Path(sys.base_prefix) / "DLLs"])
        # Unrelated tools on PATH can supply incompatible DLLs with system names.
        environment["PATH"] = os.pathsep.join(str(path) for path in paths)
    return environment


@contextmanager
def build_environment():
    previous = os.environ.copy()
    environment = isolated_environment(build=True)
    try:
        os.environ.clear()
        os.environ.update(environment)
        yield
    finally:
        os.environ.clear()
        os.environ.update(previous)


def check_bundle(executable: Path, work_directory: Path) -> None:
    environment = isolated_environment()
    environment["QT_QPA_PLATFORM"] = "offscreen"
    work_directory.mkdir(parents=True, exist_ok=True)
    report = work_directory / "gui-check.json"
    report.unlink(missing_ok=True)
    result = subprocess.run(
        [str(executable), "--check-gui", str(report)],
        cwd=executable.parent,
        env=environment,
        timeout=60,
        check=False,
    )
    if result.returncode != 0 or not report.is_file():
        detail = report.read_text(encoding="utf-8") if report.is_file() else "No startup report was produced."
        raise RuntimeError(f"Packaged GUI check failed with code {result.returncode}: {detail}")
    if json.loads(report.read_text(encoding="utf-8")).get("status") != "passed":
        raise RuntimeError(f"Packaged GUI check did not pass: {report}")


def project_version() -> str:
    import tomllib

    with (PROJECT_ROOT / "pyproject.toml").open("rb") as handle:
        return str(tomllib.load(handle)["project"]["version"])


def version_tuple(version: str) -> tuple[int, int, int, int]:
    numbers = []
    for part in version.split(".")[:4]:
        digits = "".join(character for character in part if character.isdigit())
        numbers.append(int(digits or 0))
    return tuple((numbers + [0, 0, 0, 0])[:4])  # type: ignore[return-value]


def build_user_guide(version: str) -> Path:
    guide_script = PROJECT_ROOT / "docs" / "build_user_guide.py"
    result = subprocess.run(
        [sys.executable, str(guide_script)],
        cwd=PROJECT_ROOT,
        timeout=60,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"User guide generation failed with code {result.returncode}.")
    guide = PROJECT_ROOT / "output" / "pdf" / f"PhotoCardOrganizer-{version}-User-Guide.pdf"
    if not guide.is_file():
        raise RuntimeError(f"User guide generation did not create the expected file: {guide}")
    return guide


def generate_assets(
    asset_directory: Path,
    version: str,
) -> tuple[Path, Path, Path, Path]:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PIL import Image
    from PySide6.QtWidgets import QApplication

    from photocard.qt_theme import application_icon

    asset_directory.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    master_icon = application_icon(1024)
    png_path = asset_directory / "PhotoCardOrganizer.png"
    if not master_icon.pixmap(1024, 1024).save(str(png_path), "PNG"):
        raise RuntimeError(f"Could not generate {png_path}")
    desktop_png_path = asset_directory / "PhotoCardOrganizer-256.png"
    desktop_icon = application_icon(256)
    if not desktop_icon.pixmap(256, 256).save(
        str(desktop_png_path), "PNG"
    ):
        raise RuntimeError(f"Could not generate {desktop_png_path}")
    ico_path = asset_directory / "PhotoCardOrganizer.ico"
    with Image.open(png_path) as image:
        image.save(
            ico_path,
            format="ICO",
            sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
        )

    numeric = version_tuple(version)
    version_path = asset_directory / "windows-version.txt"
    version_path.write_text(
        """VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=%r,
    prodvers=%r,
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable(
        '040904B0',
        [StringStruct('CompanyName', 'Photo Card Organizer'),
         StringStruct('FileDescription', 'Photo Card Organizer'),
         StringStruct('FileVersion', '%s'),
         StringStruct('InternalName', 'PhotoCardOrganizer'),
         StringStruct('OriginalFilename', 'PhotoCardOrganizer.exe'),
         StringStruct('ProductName', 'Photo Card Organizer'),
         StringStruct('ProductVersion', '%s')]
      )
    ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
""" % (numeric, numeric, version, version),
        encoding="utf-8",
    )
    app.processEvents()
    return png_path, desktop_png_path, ico_path, version_path


def build_bundle(
    dist_directory: Path,
    work_directory: Path,
    package_kind: str,
    *,
    skip_smoke: bool = False,
) -> Path:
    try:
        import PyInstaller.__main__
    except ImportError as exc:
        raise RuntimeError(
            "PyInstaller is not installed. Install requirements-build.txt in the build environment."
        ) from exc

    version = project_version()
    user_guide = build_user_guide(version)
    generated = work_directory / "generated"
    spec_directory = work_directory / "spec"
    png_path, desktop_png_path, ico_path, version_path = generate_assets(
        generated, version
    )
    dist_directory.mkdir(parents=True, exist_ok=True)
    spec_directory.mkdir(parents=True, exist_ok=True)

    arguments = [
        str(PROJECT_ROOT / "app.py"),
        "--name=PhotoCardOrganizer",
        "--onedir",
        "--noconfirm",
        "--clean",
        f"--distpath={dist_directory}",
        f"--workpath={work_directory / 'pyinstaller'}",
        f"--specpath={spec_directory}",
        f"--paths={PROJECT_ROOT}",
        "--exclude-module=tkinter",
    ]
    if os.name == "nt":
        arguments.extend(
            [
                "--windowed",
                f"--icon={ico_path}",
                f"--version-file={version_path}",
            ]
        )
    else:
        # A console-capable executable keeps the installed Linux CLI functional.
        # Desktop launchers use Terminal=false, so normal GUI launches stay quiet.
        arguments.extend(["--console", f"--icon={desktop_png_path}"])

    with build_environment():
        PyInstaller.__main__.run(arguments)
    bundle = dist_directory / "PhotoCardOrganizer"
    executable = bundle / ("PhotoCardOrganizer.exe" if os.name == "nt" else "PhotoCardOrganizer")
    if not executable.is_file():
        raise RuntimeError(f"PyInstaller did not create the expected executable: {executable}")

    shutil.copy2(PROJECT_ROOT / "README.md", bundle / "README.md")
    shutil.copy2(
        PROJECT_ROOT / "CHANGELOG.md", bundle / "CHANGELOG.md"
    )
    shutil.copy2(user_guide, bundle / user_guide.name)
    shutil.copy2(png_path, bundle / "PhotoCardOrganizer.png")
    shutil.copy2(
        desktop_png_path, bundle / "PhotoCardOrganizer-256.png"
    )
    brand_mark = PROJECT_ROOT / "assets" / "photo-card-organizer-mark.svg"
    if brand_mark.is_file():
        shutil.copy2(brand_mark, bundle / brand_mark.name)
    if os.name == "nt":
        shutil.copy2(ico_path, bundle / "PhotoCardOrganizer.ico")
    release = {
        "format": "photo-card-organizer-release",
        "version": version,
        "config_schema": CURRENT_CONFIG_SCHEMA,
        "package_kind": package_kind,
        "executable": executable.name,
        "user_guide": user_guide.name,
    }
    (bundle / "release.json").write_text(
        json.dumps(release, indent=2) + "\n",
        encoding="utf-8",
    )

    if not skip_smoke:
        check_bundle(executable, work_directory)
    return bundle


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the self-contained application bundle.")
    parser.add_argument("--dist", type=Path, required=True)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument(
        "--package-kind",
        choices=("windows", "deb", "appimage", "linux"),
        required=True,
    )
    parser.add_argument("--skip-smoke", action="store_true")
    args = parser.parse_args()
    bundle = build_bundle(
        args.dist.resolve(),
        args.work.resolve(),
        args.package_kind,
        skip_smoke=args.skip_smoke,
    )
    print(bundle)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
