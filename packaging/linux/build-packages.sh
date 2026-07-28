#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROJECT_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/../.." && pwd)
cd "$PROJECT_ROOT"

SKIP_DEPENDENCIES=0
SKIP_TESTS=0
while [ "$#" -gt 0 ]; do
    case "$1" in
        --skip-dependencies) SKIP_DEPENDENCIES=1 ;;
        --skip-tests) SKIP_TESTS=1 ;;
        *) printf '%s\n' "Unknown option: $1" >&2; exit 2 ;;
    esac
    shift
done

PYTHON=${PYTHON:-python3}
BUILD_ROOT="$PROJECT_ROOT/build/linux"
BUILD_VENV="$BUILD_ROOT/venv"
DIST_ROOT="$BUILD_ROOT/dist"
WORK_ROOT="$BUILD_ROOT/work"
ARTIFACT_ROOT="$PROJECT_ROOT/artifacts/linux"

if [ "$SKIP_DEPENDENCIES" -eq 0 ]; then
    if [ ! -x "$BUILD_VENV/bin/python" ]; then
        "$PYTHON" -m venv "$BUILD_VENV"
    fi
    "$BUILD_VENV/bin/python" -m pip install --disable-pip-version-check \
        -r requirements.txt -r requirements-build.txt
elif [ ! -x "$BUILD_VENV/bin/python" ]; then
    printf '%s\n' "The Linux build environment is missing. Run without --skip-dependencies first." >&2
    exit 1
fi

BUILD_PYTHON="$BUILD_VENV/bin/python"
if [ "$SKIP_TESTS" -eq 0 ]; then
    QT_QPA_PLATFORM=offscreen "$BUILD_PYTHON" -m unittest discover -s tests -v
fi

VERSION=$(
    "$BUILD_PYTHON" -c \
        "import tomllib, pathlib; print(tomllib.loads(pathlib.Path('pyproject.toml').read_text(encoding='utf-8'))['project']['version'])"
)
"$BUILD_PYTHON" packaging/build_bundle.py \
    --dist "$DIST_ROOT" --work "$WORK_ROOT" --package-kind linux
BUNDLE="$DIST_ROOT/PhotoCardOrganizer"

if command -v dpkg >/dev/null 2>&1; then
    DEB_ARCH=$(dpkg --print-architecture)
else
    case "$(uname -m)" in
        x86_64) DEB_ARCH=amd64 ;;
        aarch64|arm64) DEB_ARCH=arm64 ;;
        *) printf '%s\n' "Unsupported Debian architecture: $(uname -m)" >&2; exit 1 ;;
    esac
fi
case "$DEB_ARCH" in
    amd64) APPIMAGE_ARCH=x86_64 ;;
    arm64) APPIMAGE_ARCH=aarch64 ;;
    *) printf '%s\n' "Unsupported AppImage architecture: $DEB_ARCH" >&2; exit 1 ;;
esac

mkdir -p "$ARTIFACT_ROOT"

DEB_ROOT="$BUILD_ROOT/deb-root"
rm -rf -- "$DEB_ROOT"
mkdir -p \
    "$DEB_ROOT/DEBIAN" \
    "$DEB_ROOT/usr/bin" \
    "$DEB_ROOT/usr/lib/photo-card-organizer" \
    "$DEB_ROOT/usr/share/applications" \
    "$DEB_ROOT/usr/share/icons/hicolor/256x256/apps"
cp -a "$BUNDLE/." "$DEB_ROOT/usr/lib/photo-card-organizer/"
install -m 0755 "$SCRIPT_DIR/photo-card-organizer-cli" "$DEB_ROOT/usr/bin/photo-card-organizer"
install -m 0644 "$SCRIPT_DIR/photo-card-organizer.desktop" \
    "$DEB_ROOT/usr/share/applications/photo-card-organizer.desktop"
install -m 0644 "$BUNDLE/PhotoCardOrganizer-256.png" \
    "$DEB_ROOT/usr/share/icons/hicolor/256x256/apps/photo-card-organizer.png"
INSTALLED_SIZE=$(du -sk "$DEB_ROOT/usr" | awk '{print $1}')
sed \
    -e "s/@VERSION@/$VERSION/g" \
    -e "s/@ARCH@/$DEB_ARCH/g" \
    -e "s/@INSTALLED_SIZE@/$INSTALLED_SIZE/g" \
    "$SCRIPT_DIR/debian-control.in" > "$DEB_ROOT/DEBIAN/control"

if ! command -v dpkg-deb >/dev/null 2>&1; then
    printf '%s\n' "dpkg-deb is required to build the Debian package." >&2
    exit 1
fi
DEB_ARTIFACT="$ARTIFACT_ROOT/photo-card-organizer_${VERSION}_${DEB_ARCH}.deb"
dpkg-deb --build --root-owner-group "$DEB_ROOT" "$DEB_ARTIFACT"
dpkg-deb --info "$DEB_ARTIFACT" >/dev/null

APPDIR="$BUILD_ROOT/PhotoCardOrganizer.AppDir"
rm -rf -- "$APPDIR"
mkdir -p \
    "$APPDIR/usr/bin" \
    "$APPDIR/usr/lib/photo-card-organizer" \
    "$APPDIR/usr/share/applications" \
    "$APPDIR/usr/share/icons/hicolor/256x256/apps"
cp -a "$BUNDLE/." "$APPDIR/usr/lib/photo-card-organizer/"
install -m 0755 "$SCRIPT_DIR/AppRun" "$APPDIR/AppRun"
install -m 0644 "$SCRIPT_DIR/photo-card-organizer.desktop" \
    "$APPDIR/photo-card-organizer.desktop"
install -m 0644 "$BUNDLE/PhotoCardOrganizer-256.png" "$APPDIR/photo-card-organizer.png"
install -m 0644 "$BUNDLE/PhotoCardOrganizer-256.png" \
    "$APPDIR/usr/share/icons/hicolor/256x256/apps/photo-card-organizer.png"
ln -s ../lib/photo-card-organizer/PhotoCardOrganizer \
    "$APPDIR/usr/bin/photo-card-organizer"
ln -s photo-card-organizer.png "$APPDIR/.DirIcon"

APPIMAGETOOL=${APPIMAGETOOL:-}
if [ -z "$APPIMAGETOOL" ]; then
    APPIMAGETOOL=$(command -v appimagetool || true)
fi
if [ -z "$APPIMAGETOOL" ]; then
    printf '%s\n' \
        "appimagetool was not found. Install it or set APPIMAGETOOL to its path." >&2
    exit 1
fi
APPIMAGE_ARTIFACT="$ARTIFACT_ROOT/PhotoCardOrganizer-${VERSION}-${APPIMAGE_ARCH}.AppImage"
ARCH="$APPIMAGE_ARCH" APPIMAGE_EXTRACT_AND_RUN=1 \
    "$APPIMAGETOOL" "$APPDIR" "$APPIMAGE_ARTIFACT"
chmod 0755 "$APPIMAGE_ARTIFACT"

if ! command -v sha256sum >/dev/null 2>&1; then
    printf '%s\n' "sha256sum is required to write the release checksum manifest." >&2
    exit 1
fi
(
    cd "$ARTIFACT_ROOT"
    sha256sum "$(basename "$DEB_ARTIFACT")" "$(basename "$APPIMAGE_ARTIFACT")" > SHA256SUMS
)

printf '\n%s\n%s\n%s\n%s\n' \
    "Linux packages built and verified:" \
    "$DEB_ARTIFACT" \
    "$APPIMAGE_ARTIFACT" \
    "$ARTIFACT_ROOT/SHA256SUMS"
