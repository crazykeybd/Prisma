# Plot Workbench

面向核工程与热工水力数据后处理的本地 Web 工作台。它参考了 APT PlottingTools 的常见绘图流程，但采用独立的 React 实现，不包含或调用原软件代码。

## 功能

- 导入并叠加多个 CSV、TSV、TXT、DAT、JSON、RELAP5 RSTPLT 或 LOCUST PLOT 文件，也支持拖放导入。
- 直接扫描 RSTPLT 二进制变量目录，按需读取所选曲线；支持再启动产生的多段目录合并。
- 直接扫描 LOCUST 1.2 `plot` 二进制变量目录，按变量名和编号选择曲线。
- 自由选择横轴与多条纵轴曲线，提供缩放、平移、悬浮读数和视图复位。
- 调整标题、轴标签、线性/对数尺度、网格、图例、数据点、平滑与线宽。
- 对当前曲线执行移动平均、导数、积分、归一化、FFT 和线性回归。
- 查看数据表与统计摘要，导出 PNG、SVG、CSV、TSV 和项目 JSON。
- 项目自动保存在浏览器本地，也可保存为 `.plotproj.json` 文件后再次打开。

首次打开会自动载入一组稳压器瞬态示例数据。导入文件时，第一行通常作为列名；数值列会被自动识别，缺失值不会参与绘图与分析。

## 本地运行

需要 Node.js 20 或更高版本。

```bash
npm install
npm run dev
```

浏览器访问终端显示的本地地址，默认是 `http://localhost:5173`。

生产构建：

```bash
npm run build
npm run preview
```

## 二进制绘图文件

当前版本可读取 RELAP5 `rstplt`（restart-plot）和 LOCUST 1.2 `plot` 文件：

- 支持 little-endian Fortran 顺序无格式记录。
- 支持 `plotinf`、`plotalf`、`plotnum` 和 `plotrec` 数据块。
- 支持 RESTART 记录产生的多段变量目录，同一变量会按段顺序合并。
- 文件扫描阶段只建立记录索引，读取阶段只加载用户选择的变量。

两种格式均按 `REAL*4` 绘图记录和 8 字节整数对布局实现。MELCOR、TRACE、COBRA-TF、PARCS、CONTAIN 或 FRAPCON 的原生输出仍不直接解析，原 APT PlottingTools 的完整脚本语言也不支持。

数据仅在当前浏览器中处理，不会上传到服务器。
