#!/bin/sh
set -eu

cd "$(dirname "$0")"
exec sh packaging/linux/build-packages.sh "$@"
