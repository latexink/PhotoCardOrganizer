#!/bin/sh
set -u

cd "$(dirname "$0")" || exit 1

printf '%s\n' "Photo Card Organizer Installer"
printf '%s\n' ""
printf '%s\n' "This creates or updates the project virtual environment and installs required packages."
printf "Continue with installation? [y/N] "
read -r answer
case "$answer" in
    y|Y|yes|YES) ;;
    *)
        printf '%s\n' "Installation cancelled. No installation operations were performed."
        printf "Press Enter to close. "
        read -r _confirmation
        exit 0
        ;;
esac

PYTHON_CMD=""
for candidate in python3.14 python3.13 python3.12 python3.11 python3 python; do
    if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; raise SystemExit(sys.version_info < (3, 11))' >/dev/null 2>&1; then
        PYTHON_CMD="$candidate"
        break
    fi
done

if [ -z "$PYTHON_CMD" ]; then
    printf '%s\n' "" "Installation failed: Python 3.11 or newer was not found."
    printf '%s\n' "Install Python with your distribution package manager, then run install.sh again."
    printf "Press Enter to confirm the failure and close. "
    read -r _confirmation
    exit 1
fi

if [ ! -x .venv/bin/python ] || ! .venv/bin/python -c 'import sys; raise SystemExit(sys.version_info < (3, 11))' >/dev/null 2>&1; then
    printf '%s\n' "Creating the virtual environment..."
    "$PYTHON_CMD" -m venv .venv || status=1
else
    status=0
fi

if [ "${status:-0}" -eq 0 ]; then
    printf '%s\n' "Installing or updating Photo Card Organizer..."
    .venv/bin/python -m pip install -e . || status=1
fi

if [ "${status:-0}" -eq 0 ]; then
    printf '%s\n' "Verifying the installation..."
    QT_QPA_PLATFORM=offscreen .venv/bin/python -c "import PIL, exifread, PySide6, photocard; from photocard.cli import build_parser; from photocard.gui import PhotoCardApp; build_parser()" || status=1
fi

if [ "${status:-0}" -ne 0 ]; then
    printf '%s\n' "" "Installation or verification failed. Review the error above and run install.sh again."
    printf "Press Enter to confirm the failure and close. "
    read -r _confirmation
    exit 1
fi

printf '%s\n' "" "Installation verification passed."
printf "Launch Photo Card Organizer now? [y/N] "
read -r launch
case "$launch" in
    y|Y|yes|YES) nohup .venv/bin/python app.py >/dev/null 2>&1 & ;;
esac
printf "Press Enter to confirm completion and close. "
read -r _confirmation
exit 0
