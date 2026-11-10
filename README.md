# Prisma 多软件自动化测试平台

Prisma 是一个关键词驱动、数据驱动的桌面自动化测试框架，用于为不同核能软件建立彼此隔离的测试工作区。项目支持项目管理、软件库、输入卡解析、测试剧本、模拟/真实运行、结果查看和日志追踪。

## 目录

- `prisma/`：应用源码、测试、配置、软件模板和桌面界面
- `skills/`：RELAP5 输入卡处理技能
- `.cursor/`：项目开发规则

完整的功能说明、运行方式和项目结构见 [prisma/README.md](prisma/README.md)。

## 快速开始

```powershell
cd prisma
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m api.server --port 18080
```

也可以双击 `prisma\启动Prisma测试平台.bat` 启动桌面版本。

## 仓库说明

构建产物、虚拟环境、运行日志、用户项目现场和第三方软件可执行文件不纳入版本控制。请从各软件的合法来源自行准备运行程序。
