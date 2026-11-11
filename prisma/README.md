
# Prisma 多软件自动化测试平台

关键词驱动 + 数据驱动的桌面测试框架，用于为不同核能软件建立彼此隔离的测试工作区。用户选择被测软件并创建项目后，剧本、输入卡、结果、日志和运行现场均保存在该项目目录中，不再共用同一套 RELAP5 数据。

## 快速开始

```powershell
cd prisma
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

启动桌面界面：

```powershell
.\tauri-app\src-tauri\target\release\Prisma.exe
```

使用分发压缩包时，解压后双击 `启动Prisma测试平台.bat`。首次启动会在当前目录创建 `.venv` 并安装 `requirements.txt` 中的依赖，后续启动会直接复用该环境。也可以直接运行根目录的 `Prisma.exe`。

也可以直接启动 Python 后端进行接口调试：

```powershell
python -m api.server --port 18080
```

命令行运行示例：

```powershell
.\.venv\Scripts\python.exe -m run_prisma --project projects\NUOSLSYS
```

CLI 在 stdout 只输出一段 JSON 执行摘要，运行日志和报告路径可从摘要中读取。
退出码约定如下：

| 退出码 | 含义 |
|---:|---|
| `0` | 全部用例执行并通过 |
| `1` | 执行完成，但至少一个结果检查不通过 |
| `2` | 步骤执行失败、运行被停止，或未能完成有效执行 |

单线程对照入口仍可使用：`python -m run_prisma.executor_test`。

### 报告与基线

每次运行都会在项目内生成独立制品：

```text
projects/<项目名>/exports/reports/<run_id>/
  run.json          # 本次运行清单
  results.json      # 完整结果、检查项、误差与追溯信息
  results.xlsx      # Excel 结果表
  report.html       # 可离线打开的 HTML 报告
  junit.xml         # CI/JUnit 结果
  charts/           # 热力图、趋势图和误差分布 SVG
```

桌面端任务完成后，可在“运行测试”页点击 **打开报告** 直接打开 HTML 报告。
首次通过且包含结果检查的用例会写入
`projects/<项目名>/baselines/<软件类型>/<用例编号>.json`，并登记到
`baselines/index.json`；后续运行使用该基线计算误差、异常点和统计指标，不会自动覆盖首次基线。

## 多软件项目管理

界面顶部始终显示当前项目及其绝对路径。“项目”页负责新建、打开、保存、切换和删除项目；“软件库”页单独管理可用适配器、被测程序和软件库目录。

- 每个项目关联一个已注册软件，项目创建后仍可修改关联软件。RELAP5 和 NUSOL-SYSLMR 当前可创建可运行项目；预留软件在选择框中显示“尚未实现”并禁止选择。
- 被测程序、运行依赖和输入模板集中放在软件库。新建项目会自动生成起始剧本、结果表和配置，并从对应软件的 `templates/` 复制输入模板；软件库没有模板时才回退到旧版 `inputs/` 示例。
- 打开项目时只能选择项目根目录中的 `project.json`。系统会校验 `project.json`、`config.yaml` 和软件适配器，再把项目复制到当前 `projects/` 项目库并激活；不会直接接管或覆盖原目录，普通文件夹、`data/` 子目录和任意 JSON 文件都不能作为项目打开。
- 保存项目可修改项目名称、说明和关联软件，并更新 `updated_at`。切换软件时同步更新 `project.json` 的 `software_key` 以及 `config.yaml` 的 `softwareType`、`executableName`；项目文件夹和已有测试文件保持不变。
- 项目创建后自动激活。顶部下拉框和项目表都可以切换项目；切换后界面会重新读取该项目的配置、剧本、结果、文件和日志。
- 任务运行期间不能新建、打开、切换、保存或删除当前项目，避免运行现场与激活项目不一致。
- 删除项目必须输入完整项目名称二次确认。删除会永久移除该项目的剧本、输入卡、结果、日志和运行现场。
- 旧版工作区 `__legacy__` 用于兼容原有的 `data/`、`inputs/`、`test_cases/` 和 `log/`。它可以像普通项目一样修改名称、说明和关联软件；首次保存会在根目录创建 `project.json`，但仍不能通过项目管理删除。

项目目录结构：

```text
projects/
  <项目名>/
    project.json          # 项目名称、软件类型、说明和创建时间
    config.yaml           # 该项目独立使用的剧本、结果、目录和运行参数
    data/                 # 起始剧本 test_data.xlsx 与结果表 test_check.xlsx
    inputs/               # 本项目使用的输入卡；可选的本地程序覆盖文件
    test_cases/           # 每次运行的隔离现场
    log/                  # 该项目自己的 prisma_run.log
    exports/reports/      # 每次运行独立的 JSON/Excel/HTML/JUnit/SVG 报告
    baselines/            # 首次通过结果形成的回归基线及索引
```

当前激活项目记录在 `projects/.active`。执行器和 API 会把所有读写路径限制在当前项目内；绝对路径或 `..` 不能越出项目目录。项目运行日志写入项目内的 `log/prisma_run.log`，不再写入全局日志。

## 目录说明

- `api`：桌面界面使用的本地 HTTP API
- `common`：公共方法、运行目录边界和日志上下文
- `config`：旧版工作区配置
- `data`、`inputs`、`test_cases`、`log`：旧版工作区数据
- `keywords`：关键词动作实现，以及自定义关键词登记表（项目内 `keywords.json`）
- `projects`：用户创建的独立项目
- `run_prisma`：执行框架
- `software`：被测软件适配器、别名、注册表和软件库实现
- `software-library`：集中保存被测程序、运行依赖和输入模板
- `plot-studio`：数据绘图子功能的 Vite/React 源码
- `tauri-app`：Tauri 桌面壳和静态前端

## Tauri 桌面界面

双击工作区的 `启动测试界面.lnk`，运行 `tauri-app/src-tauri/target/release/Prisma.exe`，或使用 `启动Prisma测试平台.bat` 自动准备运行环境。

- 架构：Tauri v2 窗口 + 静态前端（无需 Node 构建）+ Rust 拉起 `python -m api.server`（随机空闲端口）。
- 功能：项目新建/打开/保存、软件库新增/导入/更新/删除、输入卡解析与检索、剧本编辑、模拟/真实运行、实时进度、测试结果、数据绘图、文件浏览、日志和配置编辑。
- 运行模式可切换：`模拟运行` 不要求被测程序；`真实运行` 执行所选可执行程序，0 字节或非 PE 占位程序不会被使用。
- 真实运行参数由项目 `config.yaml` 的 `runArgs` 控制，支持 `{input}`、`{output}` 和 `{exe}` 占位符。
- 故障排查：启动问题见 `prisma/gui_debug.log`；WebView2 运行数据默认位于 exe 旁的 `webview2-data/`。

## 数据绘图（Plot）

桌面界面顶部“数据绘图”页集成了 Plot Workbench，可导入 CSV、TSV、JSON 和 RSTPLOT 数据，进行多曲线绘图、坐标设置、统计查看和结果导出。该页面以静态子应用方式嵌入主界面，不依赖 Python 后端；首次打开页面时才加载，避免影响启动速度。

Plot 源码位于 `plot-studio/`，构建产物部署到 `tauri-app/ui/plot/`。Tauri 打包和 CentOS 7.9 打包都会整体携带 `tauri-app/ui/`，因此发布包无需单独安装 Node.js。修改源码后按以下命令重新构建：

```bash
cd plot-studio
npm ci
npm run build
rm -rf ../tauri-app/ui/plot
cp -R dist ../tauri-app/ui/plot
```

`plot-studio/node_modules/` 和 `plot-studio/dist/` 是本地构建产物，不属于交付源码；打包前应保留已部署的 `tauri-app/ui/plot/`。

## 多软件适配接口

当前可直接执行 **RELAP5** 和 **NUSOL-SYSLMR**；NUSOL-SYSLMR 使用与 RELAP5 一致的输入/输出及命令行格式，复用同一套运行与结果解析逻辑。中子物理软件、子通道软件、燃料性能分析软件已登记为预留适配器。预留接口不会启动程序、生成输出或伪造结果，误用时步骤会明确失败并提示“适配器已预留但尚未实现”。

| 软件类型（推荐键） | 状态 | 可用别名示例 |
|---|---|---|
| `relap5` | 可用 | `RELAP5`、`relap5.exe`、`relap` |
| `nusolsyslmr` | 可用 | `NUSOL-SYSLMR`、`nusolsyslmr.exe`、`nusol_syslmr` |
| `neutron_physics` | 预留 / 未实现 | `neutronics`、`中子物理软件` |
| `subchannel` | 预留 / 未实现 | `sub_channel`、`子通道软件` |
| `fuel_performance` | 预留 / 未实现 | `fuel`、`燃料性能分析软件` |

### 软件库

“软件库”页会显示每类软件的类型（内置/用户新增）、程序状态、版本、模板数量、占用空间和库目录。可用软件支持导入或更新程序；更新程序时保留 `templates/`，不会覆盖已有输入模板。

默认目录结构：

```text
software-library/
  <software-key>/
    software.json       # 版本、程序相对路径、模板清单和导入时间
    bin/                # 程序及同目录运行依赖
    templates/          # 新建项目时复制的输入卡模板
```

导入时选择 `.exe` 文件即可，系统会把该 exe 所在目录中的 DLL、物性文件和其他运行依赖一并纳入 `bin/`，同时过滤日志、表格、文档、缓存、构建目录、项目目录和测试目录。也可通过后端导入一个专用程序文件夹；包含多个无法判定的 exe 时会要求直接选择目标 exe。软件库采用暂存后再替换的方式更新，单个软件包限制为 5,000 个文件和 4 GB。

运行程序按以下顺序解析：

1. 当前运行目录中的程序；
2. 当前项目 `inputs/<software-key>/` 中的本地覆盖程序；
3. 软件库 `software-library/<software-key>/bin/` 中的共享程序；
4. 兼容旧工作区的 `inputs/` 查找路径。

项目列表和顶部项目上下文会显示“程序就绪”或“缺少程序”。缺少程序时仍可管理项目、编辑输入卡和剧本，但真实运行前需要先在软件库导入对应程序。软件库目录在 API 启动后自动创建，即使尚未导入任何软件也可直接打开。

### 用户新增被测软件

“软件库”页右上角的“＋ 新增软件”可以登记一类新的被测软件，无需修改任何代码。登记项保存在 `software-library/<software-key>/software.json`，使用通用命令行适配器 `generic_cli`：

| 字段 | 说明 |
|---|---|
| 显示名称 | 界面和项目列表中显示的名称，可用中文，最多 60 字符 |
| 软件标识 | 目录名与 `software_type`，只能使用小写字母、数字、`_`、`-`，不能与内置标识重复 |
| 软件说明 / 版本 | 可选，用于软件库表格与项目卡片展示 |
| 输入卡文件名 | 每行一个，最多 12 个；第一个是**主输入卡**，其余为辅助输入卡（数据表、物性文件等），均不能包含目录 |
| 默认运行依赖 | 可选，最多 64 项，每行一个相对路径或 glob，例如 `runtime/*.dll`、`properties/tpfh2o`；拒绝绝对路径、盘符和 `..` |
| 输出文件名 | 默认 `output.txt`，不能与任一输入卡同名 |
| 命令行参数模板 | 逐项传给程序，支持 `{input}`、`{input1}`～`{input12}`、`{inputs}`、`{output}`、`{exe}` 占位符，例如 `-i {input1} -t {input2} -o {output}` |
| 运行超时 | 默认 1800 秒，可在剧本 `run_software` 的第 4 参数覆盖 |

登记后的流程：

1. 在软件库表格中该行点击“导入”，选择它的 `.exe`；程序及同目录运行文件会一并进入 `bin/`。
2. 新建项目时选择该软件，项目会生成起始剧本（`run_software` 已填好软件标识、输入卡名和输出文件名）。软件库没有输入模板时，项目 `inputs/` 会为**每一张**登记过的输入卡生成同名空白文件供填写。
3. 运行前框架会把 `inputs/` 中的全部输入卡按**原文件名**放进算例运行目录，因此多输入卡软件不必改名，也不会因为缺少辅助输入卡而失败（`folder_prep` 同样会一次复制齐全）。
4. `folder_prep` 默认整体复制软件库中该软件的 `bin/`，并继续复制登记的“默认运行依赖”。参数 3（B-H 字段中的第 6 个单元格，Excel G 列）可覆盖本用例的附加依赖：留空或填“全部”会复制 `bin/` 与默认依赖，填写显式模式时只复制匹配文件。项目 `inputs/`、`inputs/<software-key>/` 中的同名文件会覆盖软件库文件，并保留 `runtime/core.dll` 等相对子目录。
5. 真实运行时按参数模板直接调用程序（不经过 shell 拼接），工作目录为算例运行目录，标准输出写入该目录的 `screen.txt`；程序没有生成声明的输出文件时步骤明确失败。
6. `check_output` 可用通用 `key: value` 或 `key = value` 格式提取结果；需要专用解析时再实现 Python 适配器。

软件库内的所有软件（包括内置 RELAP5、NUSOL-SYSLMR、预留软件和用户新增软件）都可在表格中点击“编辑”修改显示名称、说明、版本、输入卡、默认依赖、输出文件名、命令行参数和超时。软件标识是目录名和项目关联键，创建后不能修改；更新配置不会覆盖已导入的程序、运行依赖清单和输入模板。内置及预留软件不能删除，预留软件也不能导入程序或创建可运行项目。用户新增软件可以在表格中“删除”；仍有项目引用该软件时会拒绝删除并提示先删除项目。

### 统一运行关键词 `run_software`

B-H 七列布局保持不变，字段映射如下：

| 运行目录 | `object` | 参数 1 | 参数 2 | 参数 3 | 参数 4 |
|---|---|---|---|---|---|
| 运行目录 | 可执行程序 | 软件类型 | 输入卡名 | 输出文件/相对路径 | 超时（秒） |

输出字段支持运行目录内的相对路径，例如 `results/outdta`。如果填写以
`results/` 结尾的目录，框架会追加该软件的默认输出文件名。父目录会自动创建；
绝对路径、包含 `..` 的路径以及解析后越出运行目录的路径会被拒绝。后续
`check_output` 应填写同一个相对路径。

RELAP5 示例：

```text
编号            运行目录        action        object       参数1    参数2       参数3   参数4
TC-RELAP-MINI   RELAP-MINI-001  folder_prep   TC-RELAP-MINI relap5\mini.i relap5\relap5.exe
                RELAP-MINI-001  run_software  relap5.exe   relap5   input.txt   outdta  600
                RELAP-MINI-001  check_output  outdta       p@100010000 1.00000E+05 relap5
```

NUSOL-SYSLMR 示例（程序、`tpfbipb` 和 `tpfh2o` 优先从软件库取得，`indta.i` 可来自项目输入模板）：

```text
编号              运行目录          action        object                参数1                 参数2                       参数3          参数4
TC-NUSOL-LMR      NUSOL-LMR-001     folder_prep   TC-NUSOL-LMR          nusolsyslmr\indta.i   nusolsyslmr\nusolsyslmr.exe
                  NUSOL-LMR-001     run_software  nusolsyslmr.exe       nusolsyslmr           indta.i                     outdta         1800
                  NUSOL-LMR-001     check_output  outdta                p@304010000           8.93893E+05                 nusolsyslmr
                  NUSOL-LMR-001     check_output  outdta                tempf@304010000       753.142                     nusolsyslmr
                  NUSOL-LMR-001     check_output  outdta                p@364010000           7.64535E+06                 nusolsyslmr
                  NUSOL-LMR-001     check_output  outdta                tempf@364010000       466.913                     nusolsyslmr
```

结果检查使用 `p@控制体编号`、`tempf@控制体编号` 语法，
也可使用 `check_output` 的正则形式；期望值必须取自当前算例的实际输出，不能直接套用其他输入卡的值。
NUSOL-SYSLMR 使用随程序提供的 `tpfbipb` 和 `tpfh2o`；复制时会先放公共物性文件，再由软件专用目录覆盖。

### NUSOL-SYSLMR 实机验证

已验证版本为 `NUSOLSYS-LMVer:1.1.1`，调用方式为：

```powershell
nusolsyslmr.exe -i indta -o outdta
```

该程序是 32 位 Intel Fortran 2013 Debug 构建，软件库
`software-library/nusolsyslmr/bin/` 中必须同时包含以下运行库：

| 文件 | 字节数 |
|---|---:|
| `libifcoremdd.dll` | 1,170,360 |
| `libifportmd.dll` | 271,288 |
| `libmmd.dll` | 4,091,832 |
| `msvcr120d.dll` | 1,824,424 |

软件库页会把缺少依赖的程序显示为“缺少运行依赖”，真实运行前也会再次检查，
避免只报笼统的启动失败。项目 `inputs/nusolsyslmr/` 可用于单项目覆盖，但发布或
共享软件库时必须保留这些 DLL。

仓库内的 `templates/indta.i` 本身就是 10 秒短算例，第 52 行为：

```text
201   10.0  1.0e-10   0.005      3     200   2000   2000
```

该算例约 13 秒完成，应看到退出码 `0`、2 次 major edit、无诊断行，并包含：

```text
Final time=    10.0000
Transient terminated by end of time step cards.
```

该算例的代表值包括 `p@304010000 = 8.93893E+05`、
`tempf@304010000 = 753.142`、`p@364010000 = 7.64535E+06` 和
`tempf@364010000 = 466.913`，可用于核对结果解析链路。设置
`PRISMA_REAL_NUSOL=1` 可启用对应的可选实机回归测试。

500 秒版本仅用于发布前完整验证，不放入仓库模板。它在同一机器上约 699 秒完成，
退出码 `0`、52 次 major edit、无诊断行；`outdta` 约 8.34 MB，
`rstplt` 约 41.56 MB，不适合作为每次提交都运行的快速测试。

旧的 `run_relap5` 仍可使用，内部已委托给同一 RELAP5 适配器，既有剧本无需迁移。`check_output` 的参数 3 可填写软件类型；留空时保持旧的 RELAP5 解析行为。输出同时包含 `key: value` 通用格式时优先按该格式提取。

预留软件示例（当前预期失败，不会执行求解器）：

```text
TC-NP-001  NP-001  run_software  solver.exe  neutron_physics  input.inp  output.txt  600
```

### 扩展新软件

**方式一（推荐，零代码）：** 在“软件库”页点击“＋ 新增软件”，填写显示名称、软件标识、输入卡文件名（支持多张）、输出文件名和命令行参数模板，再导入 `.exe` 即可运行，见上文“用户新增被测软件”。

**方式二（专用适配器）：** 需要定制输入卡准备、输出解析或结果关键词时：

1. 在 `software/` 下实现适配器的 `execute`、`analyze_output` 和 `extract_value`。
2. 在 `software/registry.py` 注册标准键、显示名、状态和别名。
3. 先在软件库中建立 `software-library/<software-key>/templates/` 并放入输入模板，再通过界面导入程序；运行依赖会集中进入该软件的 `bin/`。
4. 使用 `run_software` 编写至少一个模拟/真实运行回归用例，同时覆盖 `check_output` 的值提取和明确失败路径。

如果新软件的输入卡与 `inputs/CheckSGInput.txt` 一样，是“左侧固定宽度数值列 + 右侧参数说明”的格式，可直接复用现有位置型解析器和 `L行号:字段序号@行号` 定位语法，不需要为每个软件复制一套解析代码。只有卡号、固定列或语义完全不同的格式才需要新增解析配置或适配逻辑。

执行层、项目存储、`/api/software` 和界面软件清单都读取同一注册表与软件库。尚未完成的软件应使用预留适配器并显式报错，不能创建项目或导入程序，也不能返回模拟成功结果。

### 自定义关键词（关键词列表页）

“关键词列表”页列出全部内置关键词，并提供「＋ 新增关键词」；自定义关键词只属于当前项目，保存在项目根目录的 `keywords.json`，随项目一起保存、切换和打开。要改某个自定义关键词，点它右侧的「编辑」；删除用「删除」。内置关键词不能修改或删除，避免既有剧本失效。

新增时有两种执行方式：

| 执行方式 | 含义 | 适用场景 |
|---|---|---|
| 复用内置动作（`folder_prep`、`modify_inp`、`run`、`run_relap5`、`run_software`、`check_output`、`run_code`、`check`） | 语义与内置动作完全一致，只是换成贴合目标软件的字段标签和说明 | 新软件沿用相同的准备/运行/检查流程，只是叫法不同 |
| 自定义命令行（`command`） | 按填写的命令模板在运行目录执行，返回码 0 视为成功 | 命令行风格与现有适配器不同、或只需一条命令即可完成的小工具 |

命令模板可用占位符：`{dir}` / `{run_dir}` 运行目录、`{exe}` / `{object}` 对象列内容、`{p1}`~`{p4}` 参数 1~4。例如：

```text
{exe} -i {p1} -o {p2}
```

字段定义决定剧本编辑器的列：列 0 固定为运行目录，列 2 固定为对象列，其余列按顺序对应参数 1~4，每列可以自定义标签、占位提示和是否必填。保存后该关键词立刻出现在剧本编辑器的动作下拉里，`action` 列填关键词名称即可执行。

保存时后端会校验名称（英文字母开头，仅字母数字下划线）、与内置关键词重名、字段列号重复或缺少列 0/2、未知占位符和空说明，错误会直接显示在界面提示条上。

## 输入卡与测试剧本编辑

“输入卡”页支持选择文件或直接粘贴内容，并可自动识别或显式选择以下两类格式：

- **RELAP5 卡格式**：固定列、自由格式、8 位/7 位卡号、小编辑卡、外部列表行号、续行和注释。
- **位置型值 + 说明**：左侧固定宽度值列，第 19 列起为参数说明；同一行可按空白或逗号包含多个值，说明中的 `!`、`#` 按普通文本保留。

解析结果会显示源行号、记录/卡号、字段、参数说明和原始行，便于定位需要修改的变量。

### 剧本向导：5 步生成一整套用例

“测试剧本”页工具栏的 **＋ 新建剧本** 打开向导，适合新项目从零开始建剧本，不必逐格填写：

1. **选择被测软件**：下拉框来自软件库（含用户新增的软件），会自动带出可执行程序、默认输入卡、输出文件名与超时。
2. **填写用例数量与编号前缀**：如 `TC` + 3 个用例 → `TC-01 ~ TC-03`；可指定起始编号，单个剧本上限 60 个用例。
3. **确认运行目录模板**：默认 `test_cases/{case}`，多用例时模板必须包含 `{case}`（或 `{no}` / `{index}`），避免互相覆盖。
4. **填写参数矩阵（可选）**：每行一个变量或检查项，每列对应一个用例；留空表示该用例不做这一步。变量行支持“检索”按钮，直接点选输入卡字段回填定位。
5. **预览并生成**：预览区按用例列出即将写入的步骤，确认后一键生成 `.xlsx`，并自动在编辑器中打开。

每个用例固定生成：`folder_prep`（建目录、拷输入卡与依赖）→ `modify_inp × N`（可选）→ `run_software`（运行被测软件）→ `check_output × M`（可选）。生成后仍可用编辑器逐项微调。

“测试剧本”页支持灵活的步骤编辑：

- 点击任意步骤上方的“＋ 在此步骤前插入”，可在首步骤之前或任意两个步骤之间插入空白步骤；列表末尾仍可通过“＋ 步骤”追加。
- 点击步骤右侧的“复制”，会完整复制该步骤的运行目录、关键词及其余五个参数列（B-H 七列），并将副本紧跟在原步骤之后；副本可独立修改，不会影响原步骤。
- 步骤右侧的 ↑ / ↓ 可直接调整顺序（运行目录随之同步），用例头部的“复制用例”“同步运行目录”“删除用例”用于整组复制或批量对齐目录。
- 新建步骤会按上下文自动预填运行目录、可执行程序、输入卡和上游输出文件名；还能一键追加 `modify_inp` / `run_software` / `check_output` 步骤。
- “检查剧本”会扫描缺少必填项、目录不连续、用例缺运行步骤等问题并在提示栏列出；剧本路径旁的下拉框可直接切换当前项目 `data/` 下的其他剧本。

“测试剧本”页会按当前关键词动态展示可用字段：

- `folder_prep`：运行目录、运行标识、源输入卡、可执行程序、附加依赖（可选）
- `modify_inp`：运行目录、输入卡文件、变量定位、新值（修改后）
- `run`：运行目录、可执行程序、输出文件名、结果关键字、模拟值（仅用于普通程序；RELAP5 请用 `run_software` 或兼容入口 `run_relap5`）
- `run_relap5`：运行目录、可执行程序、输入卡名、输出文件名、超时秒数；会自动忽略无效占位 exe（兼容入口）
- `run_software`：运行目录、可执行程序、软件类型、输入卡名、输出文件/相对路径、超时秒数；统一适配器入口
- `check_output`：运行目录、输出文件、结果关键字、期望值、可选软件类型
- `run_code`：运行目录、可执行程序、命令行参数
- `check`：运行目录、检查对象、说明

`folder_prep` 参数 3（第 6 个数据单元格）的“附加依赖”规则：

- 留空：整体复制软件库中该软件的 `bin/`，再复制软件登记的默认运行依赖；未登记默认依赖时，仍执行旧版 `tpf*` 物性文件复制。
- `全部` / `all`：整体复制软件库 `bin/` 和软件登记的全部默认运行依赖。
- `无` / `none` / `不复制`：不复制默认依赖，也不执行旧版 `tpf*` 自动复制。
- 其他内容：按相对路径或 glob 精确覆盖默认值，可填写多项，例如 `runtime/*.dll, properties/tpfh2o`。绝对路径、盘符和 `..` 会被拒绝。

依赖文件缺失只会在日志中记录 warning，不会让 `folder_prep` 失败；可执行程序本身仍按原有规则校验。

`modify_inp` 推荐先通过界面“检索输入卡变量”查找字段，再点击结果回填变量定位。RELAP5 定位格式为：

```text
卡号:卡标识:字段序号@源行号
```

例如：

```text
100:new:2@5
1000101:-:1@10
```

第二条表示纯数值卡 `1000101` 的第 1 个字段。纯数值卡没有文本关键字，定位中的卡标识固定为 `-`，不要填写字段当前值。`1000101` 的字段顺序为 **流通面积、长度、体积**，依次对应第 1、2、3 个字段；因此第 1 字段修改为 `2.0` 时属于流通面积变化，若要保持 RELAP5 几何一致性，长度和体积需按模型同步核对。

位置型格式使用 `L行号:字段序号@行号`，例如参考卡 `inputs/CheckSGInput.txt`：

```text
L2:2@2
L3:1@3
```

第一条表示第 2 行的第 2 个字段，第二条表示第 3 行的第 1 个字段。两个行号必须一致，字段序号从 1 开始。

选择变量只会填写定位，不会覆盖“新值”；新值需单独填写。执行时系统会重新解析输入卡，只原位替换目标字段，并保持物理行数、行宽、说明列、非目标字段、编码和换行方式不变。数值字段只能写入数值；新值过长、无法在说明列之前写入时步骤失败。目标不存在、定位不一致或文件在修改期间被其他进程改变时也会失败，不会追加变量或覆盖未知修改。

示例剧本 `data/test_data_relap5.xlsx` 的 `modify_inp` 行：

```text
运行目录      action       object       参数1             参数2
TEST-R501     modify_inp   input.txt    100:new:2@5       stdy-st
```

更完整的列含义和各关键词字段说明见 `测试用例表格填写规范.md`。

## 真实 RELAP5 接入

在“软件库”页的 RELAP5 行点击“导入”或“更新”，选择有效的 `relap5.exe`。系统会把同目录的 `tpfh2o` 等运行依赖导入软件库；输入模板放在 `software-library/relap5/templates/`。项目 `inputs/relap5/` 仍可作为单项目程序覆盖，但不再是必需项。0 字节或非 PE 占位文件不会被复制或执行：

```
编号            运行目录        action        object       参数1            参数2            参数3
TC-RELAP-MINI   RELAP-MINI-001  folder_prep   运行目录     relap5\mini.i   relap5\relap5.exe
                RELAP-MINI-001  run_relap5    relap5.exe   input.txt        outdta           600
                RELAP-MINI-001  check_output  outdta       p@100010000      1.00000E+05
```

- `run_relap5` 会以 `relap5.exe -i indta -o outdta` 方式运行（输入自动暂存为 indta，物性文件 tpf* 自动拷入运行目录，运行前自动删除旧 outdta/rstplt——该 exe 拒绝覆盖已有 outdta）；它只接受非空 PE 程序，0 字节/非 PE 占位文件不会被使用
- 运行后自动分析 outdta：`Input processing completed successfully` 视为成功；`0********` 开头的诊断行（如 junction 高程闭合错误）会写入日志
- `check_output` 支持 RELAP 变量写法：`p@控制体编号`、`tempf@控制体编号` 等（按 Vol.no. 表头取列，取最后一次大编辑的值），也支持带一个捕获组的正则
- 数值比对容差为 max(1e-6, 1e-4*|期望值|)
- 注意：该 exe（RBIC/3.2）用老版卡片格式——部件卡为 7 位 `CCC0000 名称 类型`，几何卡 0101/0102 分两行，初始条件在 0200 卡
- 可运行示例：`data/examples/test_data_relap5_mini.xlsx`（配套 `test_check_relap5_mini.xlsx`、输入卡 `inputs/relap5/mini.i`），真实模式下约 1 秒跑完并通过
- 完整示例：`inputs/relap5/indta`（LSTF SGTR 基准卡，MOD3.1）当前在该 exe 下报 4 个 junction 高程闭合错误（402010000 / 406010000 / 136030000 / 136050000）；执行后会生成 `test_cases/<运行目录>/outdta`，历史运行目录不随项目打包保留

