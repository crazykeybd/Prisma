# Prisma Linux 安装说明

## 适用范围

- 操作系统：CentOS 7.9 x86_64
- 运行环境：X11 图形桌面；远程服务器请使用 X11 转发或 VNC
- 应用包：`Prisma-Linux-x64-0.1.0.tar.gz`
- 内置运行时：Python 3.8.18、Electron 17.4.11
- 目标机不需要预装 Python，也不需要执行 `pip install`

该包在 CentOS 7.9 容器内构建并使用 glibc 2.17，不适用于较旧的非
x86_64 系统。应用默认安装到当前用户目录，不要使用 `root` 启动图形界面。

## 安装图形依赖

第一次安装时，使用具有 `sudo` 权限的普通账号安装系统库：

```bash
sudo yum -y install \
  gtk3 nss libnotify libsecret at-spi2-atk \
  libX11 libXcomposite libXdamage libXrandr libXcursor libXi libXtst \
  libXScrnSaver xdg-utils cups-libs alsa-lib pango libdrm mesa-libgbm \
  libxkbcommon libxkbcommon-x11 libxshmfence \
  dejavu-sans-fonts wqy-zenhei-fonts
```

CentOS 7 已停止官方常规镜像更新。如果 `yum` 报告镜像不可用，请先将仓库
指向可用的 7.9 Vault 源，或由管理员提供这些 RPM 的离线安装源。

完全离线安装时，需要在另一台相同 CentOS 7.9 x86_64 机器上使用
`yumdownloader --resolve` 准备上述软件包及其依赖，再复制到目标机执行
`sudo yum -y localinstall ./*.rpm`。

## 解压并校验

将安装包上传到目标机器后执行：

```bash
sha256sum -c Prisma-Linux-x64-0.1.0.tar.gz.sha256
tar -xzf Prisma-Linux-x64-0.1.0.tar.gz
cd Prisma-Linux-x64-0.1.0
```

同时可人工核对 SHA256：

```bash
sha256sum Prisma-Linux-x64-0.1.0.tar.gz
```

## 安装

使用准备运行 Prisma 的普通账号执行：

```bash
./install.sh
```

默认程序目录：

```text
$HOME/.local/share/Prisma
```

安装脚本会创建桌面菜单项和 `$HOME/.local/bin/prisma` 启动链接。若
`$HOME/.local/bin` 不在 `PATH` 中，可直接使用完整路径启动：

```bash
$HOME/.local/share/Prisma/prisma
```

安装到其他可写目录：

```bash
./install.sh --prefix "$HOME/Applications/Prisma"
```

安装目录必须绝对路径，并且必须由当前用户可写。不要安装到 `/opt` 后再用
普通用户运行，否则项目、日志和软件库无法写入。

## 启动与首次配置

从桌面菜单打开 Prisma，或执行：

```bash
prisma
```

Linux 包内附带的 RELAP5/NUSOL 示例程序可能是 Windows `.exe` 文件，不能在
Linux 上执行。请在“软件库”中导入对应程序的 Linux 原生 ELF 版本；ELF
文件必须具有执行权限。也可以在项目自己的 `inputs/<软件标识>/` 目录中放入
原生程序，程序名可使用 `relap5`、`nusolsyslmr`，或对应的 `.exe` 名称。

建议先创建一个新项目并保持 `runMode: simulate`，确认剧本和数据表流程正常，
再切换到 `runMode: real` 调用 Linux 求解器。

## 升级

备份用户项目后，用新版本安装包重新执行：

```bash
tar -xzf Prisma-Linux-x64-<新版本>.tar.gz
cd Prisma-Linux-x64-<新版本>
./install.sh
```

默认安装位置不变时，升级会保留 `resources/app` 下的 `data`、`inputs`、
`software-library`、`projects`、`log` 和 `config`。仍建议在升级前备份整个
`$HOME/.local/share/Prisma/resources/app` 目录。

## 卸载

在安装包的原始解压目录中执行：

```bash
./uninstall.sh
```

默认会删除程序文件，并把用户数据移动到：

```text
$HOME/Prisma-user-data-YYYYMMDD-HHMMSS
```

确认不再需要用户数据后，可彻底删除：

```bash
./uninstall.sh --purge
```

也可以直接进入安装目录执行 `./uninstall.sh`。不要手工对根目录或 `$HOME`
执行递归删除。

## 日志与故障排查

后端日志：

```text
$HOME/.local/share/Prisma/resources/app/log/backend.log
```

项目运行日志：

```text
$HOME/.local/share/Prisma/resources/app/projects/<项目>/log/prisma_run.log
```

检查 Electron 动态库：

```bash
ldd "$HOME/.local/share/Prisma/electron" | grep "not found"
```

没有输出表示动态库完整。若有缺失项，请根据库名补充对应的 CentOS 7 RPM。

若窗口无法打开：

1. 确认使用普通用户登录图形桌面，而不是 `root`。
2. 确认 `DISPLAY` 已设置，X11 或 VNC 会话可用。
3. 使用 `ulimit -n 4096` 提高文件句柄上限。
4. 查看 `backend.log` 和终端输出；启动脚本已默认附加 `--no-sandbox`，
   以兼容 CentOS 7 常见的内核用户命名空间限制。

若求解器无法启动：

1. 执行 `file /path/to/program`，确认是 x86-64 ELF。
2. 执行 `chmod 755 /path/to/program`。
3. 执行 `ldd /path/to/program | grep "not found"` 检查求解器依赖。
4. 确认程序的动态库和物性文件已和可执行程序一起导入软件库。

