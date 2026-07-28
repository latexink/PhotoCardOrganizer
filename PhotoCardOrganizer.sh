#!/bin/sh
set -u

cd "$(dirname "$0")" || exit 1

if [ ! -x .venv/bin/python ]; then
    printf '%s\n' "Photo Card Organizer is not installed. Run install.sh first."
    exit 1
fi

if ! QT_QPA_PLATFORM=offscreen .venv/bin/python -c "import PIL, exifread, PySide6, photocard; from photocard.gui import PhotoCardApp" >/dev/null 2>&1; then
    printf '%s\n' "The installation is incomplete. Run install.sh to repair and verify it."
    exit 1
fi

exec .venv/bin/python app.py "$@"
