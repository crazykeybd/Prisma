"""测试剧本向导：把「用例 × 参数矩阵」展开成标准剧本行。

剧本每行固定 7 个数据单元格（不含首列编号）：

    0 运行目录 | 1 关键词 | 2 object | 3..6 参数 1..4

一个用例的标准结构是：

    folder_prep(建目录、拷输入卡) → modify_inp × N(可选)
    → run_software(运行被测软件) → check_output × M(可选)

本模块只做纯数据转换，便于单独测试；写文件仍由 API 层的 save_script 完成。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from software import get_adapter, software_catalog
from software.base import SoftwareAdapterError

MAX_CASES = 60
DEFAULT_RUN_DIR_TEMPLATE = "test_cases/{case}"
DEFAULT_TIMEOUT = 600
RUN_KEYWORD = "run_software"

_BAD_NAME_CHARS = re.compile(r'[\\/:*?"<>|]')
_PLACEHOLDER = re.compile(r"\{case\}|\{no\}|\{index\}", re.IGNORECASE)


class ScriptWizardError(ValueError):
    """剧本向导入参不合法。"""


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _as_int(value: Any, label: str, default: int, low: int, high: int) -> int:
    raw = _text(value)
    if not raw:
        return default
    try:
        number = int(float(raw))
    except (TypeError, ValueError) as exc:
        raise ScriptWizardError(f"{label}必须是整数: {raw}") from exc
    if number < low or number > high:
        raise ScriptWizardError(f"{label}必须在 {low} ~ {high} 之间: {number}")
    return number


def case_numbers(
    prefix: str = "TC", count: int = 1, start: int = 1
) -> list[str]:
    """生成用例编号，例如 TC-01、TC-02。"""
    prefix = _text(prefix) or "TC"
    if _BAD_NAME_CHARS.search(prefix):
        raise ScriptWizardError('用例前缀不能包含 \\ / : * ? " < > | 等字符')
    count = _as_int(count, "用例数量", 1, 1, MAX_CASES)
    start = _as_int(start, "起始编号", 1, 1, 9999)
    width = max(2, len(str(start + count - 1)))
    return [f"{prefix}-{index:0{width}d}" for index in range(start, start + count)]


def run_dir_for(template: str, case_no: str) -> str:
    """按模板生成运行目录。"""
    text = _text(template) or DEFAULT_RUN_DIR_TEMPLATE
    directory = text.replace("\\", "/")
    directory = (
        directory.replace("{case}", case_no)
        .replace("{CASE}", case_no)
        .replace("{no}", case_no)
        .replace("{index}", case_no)
    )
    if Path(directory).is_absolute() or directory.startswith("/"):
        raise ScriptWizardError("运行目录必须是项目内的相对路径")
    if ".." in Path(directory).parts:
        raise ScriptWizardError("运行目录不能使用 ..，必须位于项目内")
    return directory.rstrip("/")


def _matrix_rows(items, count: int, label: str) -> list[dict]:
    """把参数矩阵行整理成 {target, values, card}。"""
    rows = []
    for index, item in enumerate(items or [], start=1):
        if not isinstance(item, dict):
            continue
        target = _text(item.get("target") or item.get("keyword"))
        values = [_text(value) for value in (item.get("values") or [])]
        values = values[:count] + [""] * max(0, count - len(values))
        if not target:
            if any(values):
                raise ScriptWizardError(f"第 {index} 个{label}缺少变量定位或结果关键字")
            continue
        if not any(values):
            continue
        rows.append({
            "target": target,
            "card": _text(item.get("card")),
            "values": values,
        })
    return rows


def normalize_spec(spec: Any, project=None, library=None) -> dict:
    """补全默认值并校验，返回可直接生成剧本的规格。"""
    spec = spec if isinstance(spec, dict) else {}
    software_key = _text(spec.get("software_key")) or _text(
        getattr(project, "software_key", "")
    )
    if not software_key:
        software_key = "relap5"
    try:
        adapter = get_adapter(software_key, library)
    except SoftwareAdapterError as exc:
        raise ScriptWizardError(str(exc)) from exc

    executable = _text(spec.get("executable")) or _text(
        getattr(adapter, "executable_name", "")
    )
    if not executable:
        raise ScriptWizardError("可执行程序不能为空")

    declared_inputs = [
        _text(name)
        for name in (getattr(adapter, "input_filenames", ()) or ())
        if _text(name)
    ]
    input_card = _text(spec.get("input_card")) or _text(
        getattr(adapter, "input_filename", "")
    )
    if not input_card:
        input_card = declared_inputs[0] if declared_inputs else "input.txt"

    output = _text(spec.get("output")) or _text(
        getattr(adapter, "output_filename", "")
    )
    if not output:
        output = "output.txt"

    prefix = _text(spec.get("prefix")) or "TC"
    count = _as_int(spec.get("count"), "用例数量", 1, 1, MAX_CASES)
    start = _as_int(spec.get("start"), "起始编号", 1, 1, 9999)
    numbers = case_numbers(prefix, count, start)

    template = _text(spec.get("run_dir_template")) or DEFAULT_RUN_DIR_TEMPLATE
    if len(numbers) > 1 and not _PLACEHOLDER.search(template):
        raise ScriptWizardError(
            "运行目录模板必须包含 {case}，否则所有用例会写入同一目录"
        )

    timeout = _as_int(
        spec.get("timeout"),
        "超时（秒）",
        int(getattr(adapter, "timeout_sec", 0) or DEFAULT_TIMEOUT),
        1,
        86400,
    )

    return {
        "software_key": adapter.key,
        "executable": executable,
        "input_card": input_card,
        "output": output,
        "timeout": timeout,
        "prefix": prefix,
        "count": count,
        "start": start,
        "case_numbers": numbers,
        "run_dir_template": template,
        "variables": _matrix_rows(spec.get("variables"), count, "变量"),
        "checks": _matrix_rows(spec.get("checks"), count, "检查项"),
    }


def build_cases(spec: Any, project=None, library=None) -> list[dict]:
    """按规格生成剧本用例（每个用例一组 7 列数据行）。"""
    data = normalize_spec(spec, project, library)
    input_card = data["input_card"]
    output = data["output"]
    software_key = data["software_key"]
    cases = []
    for index, case_no in enumerate(data["case_numbers"]):
        run_dir = run_dir_for(data["run_dir_template"], case_no)
        rows = [[
            run_dir,
            "folder_prep",
            case_no,
            input_card,
            data["executable"],
            "",
            "",
        ]]
        for variable in data["variables"]:
            value = variable["values"][index]
            if not value:
                continue
            rows.append([
                run_dir,
                "modify_inp",
                variable["card"] or input_card,
                variable["target"],
                value,
                "",
                "",
            ])
        rows.append([
            run_dir,
            RUN_KEYWORD,
            data["executable"],
            software_key,
            input_card,
            output,
            str(data["timeout"]),
        ])
        for check in data["checks"]:
            value = check["values"][index]
            if not value:
                continue
            rows.append([
                run_dir,
                "check_output",
                output,
                check["target"],
                value,
                software_key,
                "",
            ])
        cases.append({"no": case_no, "rows": rows})
    return cases


def _project_inputs(project, adapter) -> list[str]:
    """列出项目 inputs 目录中可用的输入卡（含软件专属子目录）。"""
    names: list[str] = []
    root = getattr(project, "root", None)
    if root:
        base = Path(root) / "inputs"
        candidates = [base, base / adapter.key]
        for directory in candidates:
            if not directory.is_dir():
                continue
            try:
                entries = sorted(directory.iterdir(), key=lambda p: p.name.lower())
            except OSError:
                continue
            for entry in entries:
                if not entry.is_file() or entry.name.startswith("."):
                    continue
                if entry.parent == base:
                    names.append(entry.name)
                else:
                    names.append(f"{entry.parent.name}/{entry.name}")
    for name in (getattr(adapter, "input_filenames", ()) or ()):
        if name and name not in names:
            names.append(name)
    return names


def wizard_defaults(project=None, library=None) -> dict:
    """返回向导表单的默认值（来自当前项目与软件库）。"""
    software_key = _text(getattr(project, "software_key", "")) or "relap5"
    try:
        adapter = get_adapter(software_key, library)
    except SoftwareAdapterError as exc:
        raise ScriptWizardError(str(exc)) from exc

    options = []
    for item in software_catalog(library):
        declared = [
            str(name) for name in (item.get("input_filenames") or [])
            if str(name or "").strip()
        ]
        options.append({
            "key": item.get("key", ""),
            "display_name": item.get("display_name", item.get("key", "")),
            "available": bool(item.get("available")),
            "executable": item.get("executable_name", ""),
            "input_card": item.get("input_filename", "")
            or (declared[0] if declared else ""),
            "output": item.get("output_filename", ""),
            "timeout": int(item.get("timeout_sec") or 0),
        })

    inputs = _project_inputs(project, adapter)
    input_card = _text(getattr(adapter, "input_filename", ""))
    if inputs and input_card not in inputs:
        preferred = next(
            (name for name in inputs if name.casefold() == input_card.casefold()),
            "",
        )
        input_card = preferred or inputs[0]
    if not input_card:
        input_card = inputs[0] if inputs else "input.txt"

    return {
        "software_key": adapter.key,
        "software_name": getattr(adapter, "display_name", adapter.key),
        "software_options": options,
        "executable": _text(getattr(adapter, "executable_name", "")),
        "input_card": input_card,
        "inputs": inputs,
        "output": _text(getattr(adapter, "output_filename", "")) or "output.txt",
        "timeout": int(getattr(adapter, "timeout_sec", 0) or DEFAULT_TIMEOUT),
        "prefix": "TC",
        "count": 3,
        "start": 1,
        "run_dir_template": DEFAULT_RUN_DIR_TEMPLATE,
        "script_path": "data/test_data.xlsx",
        "case_numbers": case_numbers("TC", 3, 1),
    }
