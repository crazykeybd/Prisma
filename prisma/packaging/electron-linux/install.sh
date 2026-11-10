#!/bin/sh
set -eu

SOURCE_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
VERSION="__APP_VERSION__"
DESTINATION="${PRISMA_INSTALL_DIR:-$HOME/.local/share/Prisma}"

if [ "${1:-}" = "--prefix" ]; then
  if [ -z "${2:-}" ]; then
    echo "Usage: ./install.sh [--prefix DIRECTORY]" >&2
    exit 2
  fi
  DESTINATION=$2
elif [ -n "${1:-}" ]; then
  echo "Usage: ./install.sh [--prefix DIRECTORY]" >&2
  exit 2
fi

case "$DESTINATION" in
  /*) ;;
  *)
    echo "Install directory must be an absolute path: $DESTINATION" >&2
    exit 2
    ;;
esac

if [ "$DESTINATION" = "/" ] || [ "$DESTINATION" = "$HOME" ]; then
  echo "Refusing unsafe install directory: $DESTINATION" >&2
  exit 2
fi
if [ "$(uname -s)" != "Linux" ] || [ "$(uname -m)" != "x86_64" ]; then
  echo "This package requires Linux x86_64." >&2
  exit 2
fi
if [ ! -x "$SOURCE_DIR/electron" ] || [ ! -x "$SOURCE_DIR/python-runtime/bin/python3" ]; then
  echo "The installation package is incomplete." >&2
  exit 2
fi

PARENT_DIR=$(dirname -- "$DESTINATION")
mkdir -p "$PARENT_DIR"
STAGING="$PARENT_DIR/.Prisma-install-$$"
cleanup() {
  rm -rf "$STAGING"
}
trap cleanup EXIT HUP INT TERM

if [ -d "$DESTINATION" ]; then
  # Program files are updated while user projects, data, inputs, imported
  # software, logs and custom configuration remain in place.
  mkdir -p "$STAGING"
  tar \
    --exclude="./resources/app/data" \
    --exclude="./resources/app/inputs" \
    --exclude="./resources/app/software-library" \
    --exclude="./resources/app/projects" \
    --exclude="./resources/app/log" \
    --exclude="./resources/app/config" \
    -C "$SOURCE_DIR" -cf - . | tar -C "$STAGING" -xf -
  cp -a "$STAGING/." "$DESTINATION/"
else
  mkdir -p "$DESTINATION"
  cp -a "$SOURCE_DIR/." "$DESTINATION/"
fi

mkdir -p \
  "$DESTINATION/resources/app/data" \
  "$DESTINATION/resources/app/inputs" \
  "$DESTINATION/resources/app/software-library" \
  "$DESTINATION/resources/app/projects" \
  "$DESTINATION/resources/app/log" \
  "$DESTINATION/resources/app/config"
chmod 755 "$DESTINATION/electron" "$DESTINATION/prisma" \
  "$DESTINATION/install.sh" "$DESTINATION/uninstall.sh"
printf '%s\n' "$VERSION" > "$DESTINATION/.prisma-version"

APPLICATIONS_DIR="$HOME/.local/share/applications"
BIN_DIR="$HOME/.local/bin"
mkdir -p "$APPLICATIONS_DIR" "$BIN_DIR"
cat > "$APPLICATIONS_DIR/prisma.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Prisma
Comment=Multi-software automated testing platform
Exec="$DESTINATION/prisma"
Icon=applications-engineering
Terminal=false
Categories=Development;Utility;
StartupNotify=true
EOF
chmod 644 "$APPLICATIONS_DIR/prisma.desktop"

if [ ! -e "$BIN_DIR/prisma" ] || [ -L "$BIN_DIR/prisma" ]; then
  ln -sfn "$DESTINATION/prisma" "$BIN_DIR/prisma"
fi

echo "Prisma $VERSION installed successfully."
echo "Install directory: $DESTINATION"
echo "Launch command: $DESTINATION/prisma"
if [ -x "$BIN_DIR/prisma" ]; then
  echo "PATH launcher: $BIN_DIR/prisma"
fi
