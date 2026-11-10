# 自动化测试剧本示例

本目录包含 4 套可直接从 Tauri 界面加载的 Excel 示例，共 8 个工作簿。

## 使用方法

1. 双击工作区的“启动测试界面.lnk”，打开桌面界面。
2. 在“运行配置”中保持“模拟运行”，被测程序选择 Almond。
3. 在“测试剧本”中选择 test_data_*.xlsx。
4. 在“测试结果”中选择同名的 test_check_*.xlsx。
5. 点击“开始测试”，可在“运行监控”中查看进度、日志和结论。

每套示例都是独立配套的，请勿把 smoke 的剧本与 parallel 的结果表混用。

## 示例一览

| 场景 | 测试剧本 | 配套结果表 | 验证内容 | 预期 |
|---|---|---|---|---|
| 冒烟 | test_data_smoke.xlsx | test_check_smoke.xlsx | 准备目录、改输入、模拟运行、检查结果 | 1/1 通过 |
| 并行 | test_data_parallel.xlsx | test_check_parallel.xlsx | 3 个用例并行执行且互不干扰 | 3/3 通过 |
| 失败 | test_data_failure.xlsx | test_check_failure.xlsx | 实际值与期望值不一致时的失败提示 | FAIL-001 不通过 |
| 界面 | test_data_ui.xlsx | test_check_ui.xlsx | 长编号、长目录、长参数与窗口布局 | 2/2 通过 |

## 结果解释

- 模拟模式会把 run 行里的模拟值写入 output.txt。
- check_output 会自动把实际值写回结果表的“实际结果”列，并填写“通过/不通过”。
- test_data_failure.xlsx 是故意设计的负向用例，失败属于预期结果，不是软件故障。
- 每次重跑都会覆盖对应 test_cases/EXAMPLE-* 目录，不影响已有正式用例。

## 运行目录

- 冒烟：test_cases/EXAMPLE-SMOKE-001
- 并行：test_cases/EXAMPLE-PAR-001 至 EXAMPLE-PAR-003
- 失败：test_cases/EXAMPLE-FAIL-001
- 界面：test_cases/EXAMPLE-UI-001、test_cases/EXAMPLE-UI-002-*

## 自定义

复制任意一套 .xlsx 后修改用例编号、运行目录和参数，即可作为新的测试剧本。只要保留 testcase 工作表、8 列表头、同一用例首列合并以及连续行格式，就能被当前软件读取。
