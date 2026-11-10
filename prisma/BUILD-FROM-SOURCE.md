# 从源码编译 Prisma

本文说明如何在一台新的电脑上从源码创建 Python 运行环境、编译桌面程序并生成安装包。
源码压缩包只包含源代码、测试、示例资源和构建资源，不包含 `.venv`、Rust `target`、
历史运行项目、日志或已有安装包。

## 目录约定

解压压缩包后，目录结构应为：

```text
prisma/
  api/
  tauri-app/
  packaging/
  tests/
  requirements.txt
```

下文命令都在解压后的 `prisma` 目录中执行。安装包输出到 `prisma` 的上一级 `dist`
目录。

## Windows 编译

### 准备工具

在 Windows 10/11 x64 上安装：

1. Python 3.14 x64，并确保 `py -3.14` 可以运行。安装时勾选 pip。
2. Rust stable MSVC 工具链：

   ```powershell
   winget install Rustlang.Rustup
   rustup default stable-msvc
   ```

3. Visual Studio 2022 Build Tools，选择“使用 C++ 的桌面开发”和 Windows SDK。
4. NSIS 3。构建脚本默认查找
   `%LOCALAPPDATA%\tauri\NSIS\makensis.exe`。若 NSIS 安装在别处，可将整个 NSIS
   安装目录复制到该默认位置，或给 `build_installer.ps1` 的 `NsisExe` 参数传入
   `makensis.exe` 的绝对路径。

`packaging\vendor\MicrosoftEdgeWebView2Setup.exe` 已随源码包提供。Windows 构建机
不需要再次下载 WebView2 离线安装程序。

### 首次编译和打包

打开 PowerShell，在 `prisma` 目录中执行：

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Push-Location .\tauri-app\src-tauri
cargo build --release --locked
Pop-Location
.\packaging\build_installer.ps1 -SkipCargoBuild
```

首次执行 `cargo build` 需要访问 crates.io 下载 Rust 依赖。依赖下载完成后，
`build_installer.ps1` 默认使用离线模式复用本机 Cargo 缓存。成功后输出：

```text
..\dist\Prisma-Setup-0.1.0-x64.exe
```

后续修改 Rust 源码时，可以直接运行完整打包命令：

```powershell
.\packaging\build_installer.ps1
```

### Windows 测试

在 `prisma` 目录中执行：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

## Linux / CentOS 7.9 编译

Linux 安装包必须在 CentOS 7.9 x86_64 构建环境中生成，以保持 glibc 2.17 兼容性。
推荐在 Windows 或 Linux 主机上使用 Docker，并拉取 `centos:7.9.2009` 镜像。

### 在 Windows 主机上构建

安装 Docker Desktop 和 PowerShell 后，在源码解压目录的上一级执行：

```powershell
.\prisma\packaging\build_linux.ps1
```

脚本会在 CentOS 7.9 容器中安装编译依赖、编译 Python 3.8.18、下载 Electron
17.4.11，并执行 API 和桌面启动冒烟测试。首次构建需要联网下载系统 RPM、Python
和 Electron。

### 在 Linux 主机上构建

准备 Docker 和 Bash，在源码解压目录的上一级执行：

```bash
docker run --rm --platform linux/amd64 \
  --mount "type=bind,source=$(pwd),target=/workspace" \
  -e APP_VERSION=0.1.0 \
  centos:7.9.2009 \
  /bin/bash -lc "tr -d '\r' < /workspace/prisma/packaging/linux/build_linux.sh > /tmp/prisma-build-linux.sh && bash /tmp/prisma-build-linux.sh"
```

成功后输出：

```text
dist/Prisma-Linux-x64-0.1.0.tar.gz
dist/Prisma-Linux-x64-0.1.0.tar.gz.sha256
```

目标机安装步骤见 `packaging/LINUX-INSTALL.md`。

## 常见问题

- `cargo build --offline` 报缺少 crate：在 `tauri-app/src-tauri` 中先执行一次
  `cargo build --release --locked`，联网补齐依赖后再运行打包脚本。
- `build_installer.ps1` 报找不到 NSIS：确认
  `%LOCALAPPDATA%\tauri\NSIS\makensis.exe` 存在，或传入 `-NsisExe`。
- `build_installer.ps1` 报 Python 文件缺失：确认使用 Python 3.14 x64 创建 `.venv`。
- Linux 构建需要下载软件包：确认 Docker 容器可访问 CentOS Vault、Python 镜像和
  Electron 镜像；受限网络可先在可联网环境完成一次构建并保留 `prisma/build/linux/cache`。
