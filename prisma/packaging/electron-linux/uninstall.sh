#!/bin/sh
set -eu

APP_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PURGE=0
if [ "${1:-}" = "--purge" ]; then
  PURGE=1
elif [ -n "${1:-}" ]; then
  echo "Usage: ./uninstall.sh [--purge]" >&2
  exit 2
fi

if [ "$APP_DIR" = "/" ] || [ "$APP_DIR" = "$HOME" ]; then
  echo "Refusing unsafe uninstall directory: $APP_DIR" >&2
  exit 2
fi

BACKUP_DIR=""
if [ "$PURGE" -eq 0 ] && [ -d "$APP_DIR/resources/app" ]; then
  BACKUP_DIR="$HOME/Prisma-user-data-$(date +%Y%m%d-%H%M%S)"
  mkdir -p "$BACKUP_DIR"
  for name in data inputs software-library projects log config; do
    if [ -d "$APP_DIR/resources/app/$name" ]; then
      mv "$APP_DIR/resources/app/$name" "$BACKUP_DIR/$name"
    fi
  done
fi

DESKTOP_FILE="$HOME/.local/share/applications/prisma.desktop"
PATH_LINK="$HOME/.local/bin/prisma"
rm -f "$DESKTOP_FILE"
if [ -L "$PATH_LINK" ]; then
  rm -f "$PATH_LINK"
fi
rm -rf "$APP_DIR"

echo "Prisma program files were removed."
if [ -n "$BACKUP_DIR" ]; then
  echo "User data was preserved at: $BACKUP_DIR"
else
  echo "User data was removed (--purge)."
fi
