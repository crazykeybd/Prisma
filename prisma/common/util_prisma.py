
from pathlib import Path
import logging
import logging.handlers
import sys
import threading
import yaml
from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "config.yaml"
CHECK_LOCK = threading.Lock()


def read_config(path=None):
    p = Path(path) if path else CONFIG_PATH
    if not p.is_absolute():
        p = ROOT / p
    with p.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def resolve_path(value, base=None):
    if value is None:
        return None
    # Configuration files are also shared between Windows and Linux. Accept
    # both separator styles so an existing data\case.xlsx entry keeps working
    # after the workspace is moved to Linux.
    p = Path(str(value).replace("\\", "/"))
    if not p.is_absolute():
        p = (base or ROOT) / p
    return p.resolve()


def read_table(file_path):
    path = resolve_path(file_path)
    wb = load_workbook(path, data_only=True, read_only=False)
    sheet_name = next((name for name in wb.sheetnames if name.lower() == "testcase"), None)
    if sheet_name is None:
        wb.close()
        return []
    ws = wb[sheet_name]
    rows = [[cell.value for cell in row] for row in ws.iter_rows()]
    for merged in ws.merged_cells.ranges:
        top_left = ws.cell(merged.min_row, merged.min_col).value
        for r in range(merged.min_row, merged.max_row + 1):
            for c in range(merged.min_col, merged.max_col + 1):
                if r - 1 < len(rows) and c - 1 < len(rows[r - 1]):
                    rows[r - 1][c - 1] = top_left
    wb.close()
    if not rows:
        return []
    commands = []
    for row in rows[1:]:
        if row and any(v is not None and str(v).strip() != "" for v in row):
            cells = ["" if v is None else str(v).strip() for v in row]
            while cells and cells[-1] == "":
                cells.pop()
            commands.append(cells)
    return commands


def init_log(log_dir=None):
    logger = logging.getLogger("prisma")
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    if not any(getattr(handler, "_prisma_console", False) for handler in logger.handlers):
        console = logging.StreamHandler(sys.stdout)
        console.setFormatter(fmt)
        console._prisma_console = True
        logger.addHandler(console)

    target_dir = Path(log_dir).resolve() if log_dir else (ROOT / "log").resolve()
    target_dir.mkdir(parents=True, exist_ok=True)
    current = next(
        (handler for handler in logger.handlers
         if getattr(handler, "_prisma_project_file", False)),
        None,
    )
    target_file = target_dir / "prisma_run.log"
    if current is None or Path(current.baseFilename).resolve() != target_file:
        if current is not None:
            logger.removeHandler(current)
            current.close()
        file_handler = logging.FileHandler(target_file, encoding="utf-8")
        file_handler.setFormatter(fmt)
        file_handler._prisma_project_file = True
        logger.addHandler(file_handler)
    return logger


def release_log_file():
    """Close the project log handler so Windows can move or delete it."""
    logger = logging.getLogger("prisma")
    for handler in list(logger.handlers):
        if not getattr(handler, "_prisma_project_file", False):
            continue
        logger.removeHandler(handler)
        handler.close()


def _ensure_result_columns(ws):
    """Upgrade a result sheet to expected/actual/deviation columns."""

    def header_columns():
        return {
            str(cell.value).strip(): cell.column
            for cell in ws[1]
            if cell.value is not None and str(cell.value).strip()
        }

    headers = header_columns()
    actual_col = headers.get("实际值")
    if actual_col is None and "实际结果" in headers:
        actual_col = headers["实际结果"]
        ws.cell(1, actual_col).value = "实际值"
    if actual_col is None:
        raise ValueError(
            "check table must contain 实际值 or 实际结果 column"
        )

    if "期望值" in headers:
        expected_col = headers["期望值"]
    elif "期望结果" in headers:
        expected_col = headers["期望结果"]
        ws.cell(1, expected_col).value = "期望值"
    else:
        expected_col = actual_col
        ws.insert_cols(expected_col)
        ws.cell(1, expected_col).value = "期望值"
        actual_col += 1

    headers = header_columns()
    if "偏差" in headers:
        deviation_col = headers["偏差"]
    elif "实际值与期望值的偏差" in headers:
        deviation_col = headers["实际值与期望值的偏差"]
        ws.cell(1, deviation_col).value = "偏差"
    else:
        deviation_col = actual_col + 1
        ws.insert_cols(deviation_col)
        ws.cell(1, deviation_col).value = "偏差"

    return expected_col, actual_col, deviation_col


def write_result_to_check(
    check_file,
    case_no,
    value,
    conclusion="待比对",
    base=None,
    *,
    expected_value=None,
    deviation=None,
):
    path = resolve_path(check_file, base)
    with CHECK_LOCK:
        wb = load_workbook(path)
        ws = wb.active
        expected_col, actual_col, deviation_col = _ensure_result_columns(ws)
        headers = {
            str(cell.value).strip(): cell.column
            for cell in ws[1]
            if cell.value is not None and str(cell.value).strip()
        }
        no_col = headers.get("编号")
        con_col = headers.get("结论")
        if not no_col:
            wb.close()
            raise ValueError("check table must contain 编号 column")
        target_row = None
        for r in range(2, ws.max_row + 1):
            if str(ws.cell(r, no_col).value).strip() == str(case_no).strip():
                target_row = r
                break
        if target_row is None:
            target_row = ws.max_row + 1
            ws.cell(target_row, no_col).value = str(case_no).strip()
        ws.cell(target_row, expected_col).value = expected_value
        ws.cell(target_row, actual_col).value = value
        ws.cell(target_row, deviation_col).value = deviation
        if con_col:
            ws.cell(target_row, con_col).value = conclusion
        wb.save(path)
        wb.close()
