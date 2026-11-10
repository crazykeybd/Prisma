#!/usr/bin/env bash
set -euo pipefail

APP_VERSION=${APP_VERSION:-0.1.0}
ELECTRON_VERSION="17.4.11"
ELECTRON_SHA256="c40cc41da8f7958b4edbef953e9b0b4e830689467d1f1993c4d298677e6d0047"
PYTHON_VERSION="3.8.18"
PYTHON_SHA256="7c5df68bab1be81a52dea0cc2e2705ea00553b67107a301188383d7b57320b16"

SOURCE_ROOT=${SOURCE_ROOT:-/workspace/prisma}
DIST_ROOT=${DIST_ROOT:-/workspace/dist}
BUILD_ROOT=${BUILD_ROOT:-$SOURCE_ROOT/build/linux}
PACKAGE_NAME="Prisma-Linux-x64-$APP_VERSION"
PACKAGE_ROOT="$BUILD_ROOT/$PACKAGE_NAME"
APP_ROOT="$PACKAGE_ROOT/resources/app"
PYTHON_PREFIX="/tmp/prisma-python-$PYTHON_VERSION"
ELECTRON_URL="https://npmmirror.com/mirrors/electron/$ELECTRON_VERSION/electron-v$ELECTRON_VERSION-linux-x64.zip"
ELECTRON_FALLBACK_URL="https://github.com/electron/electron/releases/download/v$ELECTRON_VERSION/electron-v$ELECTRON_VERSION-linux-x64.zip"
PYTHON_URL="https://mirrors.huaweicloud.com/python/$PYTHON_VERSION/Python-$PYTHON_VERSION.tgz"
PYTHON_FALLBACK_URL="https://www.python.org/ftp/python/$PYTHON_VERSION/Python-$PYTHON_VERSION.tgz"

backend_pid=""

cleanup() {
  if [ -n "$backend_pid" ] && kill -0 "$backend_pid" 2>/dev/null; then
    kill "$backend_pid" 2>/dev/null || true
    wait "$backend_pid" 2>/dev/null || true
  fi
}
trap cleanup EXIT HUP INT TERM

fail() {
  echo "ERROR: $*" >&2
  exit 1
}

case "$APP_VERSION" in
  *[!0-9.]*|.*|*.|*..*) fail "Invalid application version: $APP_VERSION" ;;
esac
[[ "$APP_VERSION" =~ ^[0-9]+(\.[0-9]+){1,3}$ ]] || fail "Invalid application version: $APP_VERSION"
[ "$(uname -m)" = "x86_64" ] || fail "This build must run on x86_64."
[ -f /etc/centos-release ] || fail "This build must run in a CentOS 7.9 container."
grep -q "7.9" /etc/centos-release || fail "CentOS 7.9 is required."
grep -Eq '^NAME="?CentOS Linux"?$' /etc/os-release || fail "CentOS 7.9 is required."

echo "Configuring CentOS 7.9 Vault repositories..."
rm -f /etc/yum.repos.d/*.repo
cat > /etc/yum.repos.d/CentOS-Vault.repo <<'EOF'
[base]
name=CentOS-7.9.2009 - Base
baseurl=https://mirrors.aliyun.com/centos-vault/7.9.2009/os/$basearch/
gpgcheck=1
gpgkey=file:///etc/pki/rpm-gpg/RPM-GPG-KEY-CentOS-7
enabled=1

[updates]
name=CentOS-7.9.2009 - Updates
baseurl=https://mirrors.aliyun.com/centos-vault/7.9.2009/updates/$basearch/
gpgcheck=1
gpgkey=file:///etc/pki/rpm-gpg/RPM-GPG-KEY-CentOS-7
enabled=1

[extras]
name=CentOS-7.9.2009 - Extras
baseurl=https://mirrors.aliyun.com/centos-vault/7.9.2009/extras/$basearch/
gpgcheck=1
gpgkey=file:///etc/pki/rpm-gpg/RPM-GPG-KEY-CentOS-7
enabled=1
EOF

yum clean all
yum -y install \
  alsa-lib \
  at-spi2-atk \
  atk \
  bzip2 \
  bzip2-devel \
  ca-certificates \
  cups-libs \
  curl \
  dbus-x11 \
  dejavu-sans-fonts \
  findutils \
  gcc \
  gcc-c++ \
  gdbm-devel \
  gtk3 \
  libX11 \
  libXScrnSaver \
  libXcomposite \
  libXcursor \
  libXdamage \
  libXi \
  libXrandr \
  libXtst \
  libdrm \
  libffi-devel \
  libnotify \
  libsecret \
  libxkbcommon \
  libxkbcommon-x11 \
  libxshmfence \
  make \
  mesa-libgbm \
  ncurses-devel \
  nss \
  openssl-devel \
  pango \
  patch \
  readline-devel \
  sqlite-devel \
  tar \
  tk-devel \
  unzip \
  uuid-devel \
  wqy-zenhei-fonts \
  xdg-utils \
  xorg-x11-server-Xvfb \
  xorg-x11-xauth \
  xz \
  xz-devel \
  zlib-devel

download_checked() {
  local destination=$1
  local expected=$2
  shift 2
  if [ -f "$destination" ] && \
      echo "$expected  $destination" | sha256sum -c - >/dev/null 2>&1; then
    return
  fi
  rm -f "$destination"
  local url
  for url in "$@"; do
    echo "Downloading $url"
    if curl -fL --retry 4 --retry-delay 2 \
        --connect-timeout 30 --output "$destination" "$url"; then
      if echo "$expected  $destination" | sha256sum -c -; then
        return
      fi
    fi
    rm -f "$destination"
  done
  fail "Could not download or verify $(basename "$destination")"
}

echo "Building Python $PYTHON_VERSION on CentOS 7.9..."
rm -rf "$BUILD_ROOT"
CACHE_ROOT="$BUILD_ROOT/cache"
mkdir -p "$CACHE_ROOT" "$DIST_ROOT" "$PYTHON_PREFIX"
download_checked "$CACHE_ROOT/Python-$PYTHON_VERSION.tgz" "$PYTHON_SHA256" \
  "$PYTHON_URL" "$PYTHON_FALLBACK_URL"
download_checked "$CACHE_ROOT/electron-v$ELECTRON_VERSION-linux-x64.zip" \
  "$ELECTRON_SHA256" "$ELECTRON_URL" "$ELECTRON_FALLBACK_URL"

tar -C "$CACHE_ROOT" -xzf "$CACHE_ROOT/Python-$PYTHON_VERSION.tgz"
pushd "$CACHE_ROOT/Python-$PYTHON_VERSION" >/dev/null
./configure \
  --prefix="$PYTHON_PREFIX" \
  --with-ensurepip=install \
  --with-system-ffi \
  --enable-unicode=ucs4
make -j"$(nproc)"
make install
popd >/dev/null

PYTHON_BIN="$PYTHON_PREFIX/bin/python3"
PIP_INDEXES=(
  "https://pypi.tuna.tsinghua.edu.cn/simple"
  "https://pypi.org/simple"
)
pip_installed=0
for index in "${PIP_INDEXES[@]}"; do
  if "$PYTHON_BIN" -m pip install \
      --no-cache-dir \
      --disable-pip-version-check \
      --only-binary=:all: \
      --index-url "$index" \
      openpyxl==3.1.5 PyYAML==6.0.2; then
    pip_installed=1
    break
  fi
done
[ "$pip_installed" -eq 1 ] || fail "Could not install Python runtime dependencies."

echo "Assembling the Prisma Linux package..."
mkdir -p "$PACKAGE_ROOT"
unzip -q "$CACHE_ROOT/electron-v$ELECTRON_VERSION-linux-x64.zip" -d "$PACKAGE_ROOT"
[ -f "$PACKAGE_ROOT/electron" ] || fail "Electron archive has an unexpected layout."
mkdir -p "$APP_ROOT"

copy_tree() {
  local source=$1
  local destination=$2
  [ -d "$source" ] || fail "Required application directory is missing: $source"
  mkdir -p "$destination"
  (
    cd "$source"
    tar \
      --exclude='__pycache__' \
      --exclude='*.pyc' \
      --exclude='*.pyo' \
      -cf - .
  ) | tar -C "$destination" -xf -
}

copy_tree "$SOURCE_ROOT/api" "$APP_ROOT/api"
copy_tree "$SOURCE_ROOT/common" "$APP_ROOT/common"
copy_tree "$SOURCE_ROOT/gui" "$APP_ROOT/gui"
copy_tree "$SOURCE_ROOT/keywords" "$APP_ROOT/keywords"
copy_tree "$SOURCE_ROOT/run_prisma" "$APP_ROOT/run_prisma"
copy_tree "$SOURCE_ROOT/software" "$APP_ROOT/software"
copy_tree "$SOURCE_ROOT/config" "$APP_ROOT/config"
copy_tree "$SOURCE_ROOT/data" "$APP_ROOT/data"
copy_tree "$SOURCE_ROOT/inputs" "$APP_ROOT/inputs"
copy_tree "$SOURCE_ROOT/software-library" "$APP_ROOT/software-library"
copy_tree "$SOURCE_ROOT/tauri-app/ui" "$APP_ROOT/ui"

mkdir -p "$APP_ROOT/projects"
if [ -d "$SOURCE_ROOT/projects" ]; then
  (
    cd "$SOURCE_ROOT/projects"
    tar \
      --exclude='__pycache__' \
      --exclude='test_cases' \
      --exclude='exports' \
      --exclude='log' \
      --exclude='*.pyc' \
      --exclude='*.pyo' \
      -cf - .
  ) | tar -C "$APP_ROOT/projects" -xf -
fi

for name in project_store.py requirements.txt README.md; do
  [ -f "$SOURCE_ROOT/$name" ] || fail "Required application file is missing: $name"
  cp "$SOURCE_ROOT/$name" "$APP_ROOT/$name"
done
find "$SOURCE_ROOT" -maxdepth 1 -type f -name "*.md" \
  -exec cp {} "$APP_ROOT/" \;

cp "$SOURCE_ROOT/packaging/electron-linux/main.js" "$APP_ROOT/main.js"
cp "$SOURCE_ROOT/packaging/electron-linux/preload.js" "$APP_ROOT/preload.js"
sed "s/\"version\": \"[^\"]*\"/\"version\": \"$APP_VERSION\"/" \
  "$SOURCE_ROOT/packaging/electron-linux/package.json" > "$APP_ROOT/package.json"

sed \
  -e 's/\r$//' \
  -e "s/__APP_VERSION__/$APP_VERSION/g" \
  "$SOURCE_ROOT/packaging/electron-linux/install.sh" > "$PACKAGE_ROOT/install.sh"
sed 's/\r$//' "$SOURCE_ROOT/packaging/electron-linux/uninstall.sh" \
  > "$PACKAGE_ROOT/uninstall.sh"
sed 's/\r$//' "$SOURCE_ROOT/packaging/electron-linux/prisma" \
  > "$PACKAGE_ROOT/prisma"
cp "$SOURCE_ROOT/packaging/LINUX-INSTALL.md" "$PACKAGE_ROOT/LINUX-INSTALL.md"

mkdir -p "$APP_ROOT/log" "$APP_ROOT/test_cases" "$APP_ROOT/exports"
mkdir -p "$PACKAGE_ROOT/python-runtime"
(
  cd "$PYTHON_PREFIX"
  tar \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    --exclude='*.pyo' \
    -cf - .
) | tar -C "$PACKAGE_ROOT/python-runtime" -xf -

chmod 755 \
  "$PACKAGE_ROOT/electron" \
  "$PACKAGE_ROOT/prisma" \
  "$PACKAGE_ROOT/install.sh" \
  "$PACKAGE_ROOT/uninstall.sh" \
  "$PACKAGE_ROOT/python-runtime/bin/python3" \
  "$PACKAGE_ROOT/python-runtime/bin/python3.8"
if [ -f "$PACKAGE_ROOT/chrome-sandbox" ]; then
  chmod 755 "$PACKAGE_ROOT/chrome-sandbox"
fi
if [ -f "$PACKAGE_ROOT/chrome_crashpad_handler" ]; then
  chmod 755 "$PACKAGE_ROOT/chrome_crashpad_handler"
fi

echo "Validating shared libraries..."
if ldd "$PACKAGE_ROOT/electron" | grep -q "not found"; then
  ldd "$PACKAGE_ROOT/electron" | grep "not found" >&2
  fail "Electron has unresolved shared-library dependencies."
fi

echo "Validating bundled Python..."
(
  cd "$APP_ROOT"
  PYTHONHOME="$PACKAGE_ROOT/python-runtime" \
  PYTHONNOUSERSITE=1 \
  PYTHONUTF8=1 \
    "$PACKAGE_ROOT/python-runtime/bin/python3" -s -c \
      "import sys, yaml, openpyxl, api.server; assert sys.version_info[:2] == (3, 8); print(sys.version)"
)

echo "Validating Python API..."
API_PORT=18765
(
  cd "$APP_ROOT"
  PYTHONHOME="$PACKAGE_ROOT/python-runtime" \
  PYTHONNOUSERSITE=1 \
  PYTHONUTF8=1 \
    "$PACKAGE_ROOT/python-runtime/bin/python3" -s -m api.server --port "$API_PORT" \
    > /tmp/prisma-api-smoke.log 2>&1
) &
backend_pid=$!
api_ready=0
for _ in $(seq 1 60); do
  if curl -fsS "http://127.0.0.1:$API_PORT/api/config" > /tmp/prisma-api-response.json; then
    api_ready=1
    break
  fi
  if ! kill -0 "$backend_pid" 2>/dev/null; then
    break
  fi
  sleep 0.5
done
if [ "$api_ready" -ne 1 ]; then
  cat /tmp/prisma-api-smoke.log >&2 || true
  fail "The bundled Python API failed its smoke test."
fi
kill "$backend_pid" 2>/dev/null || true
wait "$backend_pid" 2>/dev/null || true
backend_pid=""

echo "Validating Electron under Xvfb..."
mkdir -p /tmp/prisma-smoke-home
set +e
HOME=/tmp/prisma-smoke-home \
ELECTRON_DISABLE_SECURITY_WARNINGS=1 \
  timeout --kill-after=5s 15s xvfb-run -a "$PACKAGE_ROOT/prisma" \
  > /tmp/prisma-electron-smoke.log 2>&1
electron_status=$?
set -e
pkill -f "$PACKAGE_ROOT/electron" 2>/dev/null || true
if [ "$electron_status" -ne 124 ]; then
  cat /tmp/prisma-electron-smoke.log >&2 || true
  fail "Electron exited unexpectedly during the Xvfb smoke test (status $electron_status)."
fi
if [ ! -s "$APP_ROOT/log/backend.log" ]; then
  cat /tmp/prisma-electron-smoke.log >&2 || true
  fail "Electron did not start the bundled Python backend."
fi

rm -rf "$APP_ROOT/log" "$APP_ROOT/test_cases" "$APP_ROOT/exports"
mkdir -p "$APP_ROOT/log" "$APP_ROOT/test_cases" "$APP_ROOT/exports"
find "$APP_ROOT" -type d -name "__pycache__" -prune -exec rm -rf {} +
find "$APP_ROOT" -type f \( -name "*.pyc" -o -name "*.pyo" \) -delete

echo "Creating the installation archive..."
ARCHIVE="$DIST_ROOT/$PACKAGE_NAME.tar.gz"
rm -f "$ARCHIVE" "$ARCHIVE.sha256"
tar -C "$BUILD_ROOT" -czf "$ARCHIVE" "$PACKAGE_NAME"
checksum=$(sha256sum "$ARCHIVE" | awk '{print $1}')
printf '%s  %s\n' "$checksum" "$PACKAGE_NAME.tar.gz" > "$ARCHIVE.sha256"

echo ""
echo "Linux package ready:"
echo "  $ARCHIVE"
echo "  SHA256: $checksum"
echo "  Target: CentOS 7.9 x86_64"

