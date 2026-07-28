#!/bin/sh
set -u

cd "$(dirname "$0")" || exit 1

CONFIG_HOME="${XDG_CONFIG_HOME:-$HOME/.config}"
CONFIG_DIR="$CONFIG_HOME/PhotoCardOrganizer"
AUTOSTART_FILE="$CONFIG_HOME/autostart/photo-card-organizer.desktop"

printf '%s\n' "Photo Card Organizer Uninstaller" ""
printf '%s\n' "This removes the project virtual environment, editable-install metadata, and login autostart entry."
printf '%s\n' "Per-user settings, card profiles, and local application logs are preserved by default."
printf '%s\n' "Imported media, destination transfer records, and card identity folders are always preserved."
printf "Continue with uninstall? [y/N] "
read -r answer
case "$answer" in
    y|Y|yes|YES) ;;
    *)
        printf '%s\n' "Uninstall cancelled. No uninstall operations were performed."
        printf "Press Enter to close. "
        read -r _confirmation
        exit 0
        ;;
esac

failed=0
remove_user_data=0
if [ -d .venv ]; then rm -rf -- .venv || failed=1; fi
if [ -d photo_card_organizer.egg-info ]; then rm -rf -- photo_card_organizer.egg-info || failed=1; fi
if [ -f "$AUTOSTART_FILE" ]; then rm -f -- "$AUTOSTART_FILE" || failed=1; fi

if [ -d "$CONFIG_DIR" ]; then
    printf "Also remove per-user settings, card profiles, and local application logs? [y/N] "
    read -r data_answer
    case "$data_answer" in
        y|Y|yes|YES)
            remove_user_data=1
            rm -rf -- "$CONFIG_DIR" || failed=1
            ;;
        *) printf '%s\n' "Preserving per-user application data." ;;
    esac
fi

if [ -e .venv ] || [ -e photo_card_organizer.egg-info ] || [ -e "$AUTOSTART_FILE" ]; then
    failed=1
fi
if [ "$remove_user_data" -eq 1 ] && [ -e "$CONFIG_DIR" ]; then failed=1; fi

if [ "$failed" -ne 0 ]; then
    printf '%s\n' "" "Uninstall finished with one or more items still present. Close the running application and try again."
    printf "Press Enter to confirm the failure and close. "
    read -r _confirmation
    exit 1
fi

printf '%s\n' "" "Uninstall verification passed."
if [ "$remove_user_data" -eq 1 ]; then
    printf '%s\n' "Per-user application data was removed as requested."
else
    printf '%s\n' "Per-user settings, card profiles, and local application logs were preserved."
fi
printf '%s\n' "Imported media, destination transfer records, and card metadata were preserved."
printf "Press Enter to confirm completion and close. "
read -r _confirmation
exit 0
