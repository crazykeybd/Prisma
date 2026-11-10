# Prisma 离线安装包

新电脑首次从源码准备环境、编译和打包的完整步骤见
[`../BUILD-FROM-SOURCE.md`](../BUILD-FROM-SOURCE.md)。

Windows 构建命令：

```powershell
.\prisma\packaging\build_installer.ps1
```

Linux/CentOS 7.9 构建命令：

```powershell
.\prisma\packaging\build_linux.ps1
```

输出文件：

```text
dist\Prisma-Setup-<version>-x64.exe
dist\Prisma-Setup-<version>-x64.exe.sha256
dist\Prisma-Linux-x64-<version>.tar.gz
dist\Prisma-Linux-x64-<version>.tar.gz.sha256
```

安装包包含 Python 3.14 运行时、`openpyxl`、`PyYAML`、Prisma 桌面程序、
软件库资源和 Microsoft Edge WebView2 离线安装程序。安装和运行过程不需要
联网，也不需要目标电脑预装 Python 或执行 `pip install`。

安装位置为当前用户的 `%LOCALAPPDATA%\Programs\Prisma`，因此普通用户即可
创建项目、导入软件和写入日志。升级安装会保留用户的 `projects`、`data`、
`inputs` 和 `software-library` 内容；卸载时这些用户数据同样会保留。

Linux 包在 `centos:7.9.2009` 容器中编译 Python 3.8，并附带 Electron 17，
以兼容 CentOS 7 的 glibc 2.17。目标机的系统依赖和安装步骤见
[`LINUX-INSTALL.md`](LINUX-INSTALL.md)。
