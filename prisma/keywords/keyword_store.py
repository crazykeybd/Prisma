"""关键词登记表：内置关键词 + 项目自定义关键词。

平台面向多款被测软件，各软件的输入输出、运行方式并不相同。内置关键词
覆盖平台已实现的动作；用户可以在“关键词列表”页新增自定义关键词，用来
适配其他软件：

* ``impl`` 指向内置动作时，自定义关键词是该动作的别名，只是换了更贴合
  目标软件的字段标签与说明（执行语义完全复用内置动作）；
* ``impl`` 为 ``command`` 时，按用户填写的命令行模板在运行目录执行，
  模板可使用 ``{dir}``、``{exe}``、``{object}``、``{p1}`` ~ ``{p4}``
  占位符，适合命令行风格与现有适配器完全不同的软件。

自定义关键词保存在项目根目录的 ``keywords.json``，随项目一起保存/打开。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable

KEYWORD_FILE = "keywords.json"
KEYWORD_SCHEMA_VERSION = 1

#: 执行层已实现的动作，自定义关键词可以引用其中之一作为实现。
BUILTIN_ACTIONS: tuple[str, ...] = (
    "folder_prep",
    "modify_inp",
    "run",
    "run_relap5",
    "run_software",
    "check_output",
    "run_code",
    "check",
)

COMMAND_IMPL = "command"
IMPL_CHOICES: tuple[str, ...] = (*BUILTIN_ACTIONS, COMMAND_IMPL)

ROLES = ("dir", "object", "param", "target", "value")
COMMAND_PLACEHOLDERS = ("dir", "run_dir", "exe", "object", "p1", "p2", "p3", "p4")

_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,39}$")
_PLACEHOLDER_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


class KeywordError(ValueError):
    """关键词定义不合法。"""


BUILTIN_KEYWORDS: list[dict] = [
    {"name": "folder_prep", "object": "运行标识", "params": "源输入卡, 可执行程序, 附加依赖",
     "desc": "创建运行目录，默认整体拷贝软件库 bin、输入卡和有效 Windows 程序，并支持附加运行库/物性库",
     "fields": [
         {"index": 0, "role": "dir", "label": "运行目录", "placeholder": "例如：test_cases/case_01", "required": True},
         {"index": 2, "role": "object", "label": "运行标识", "placeholder": "例如：TC-R5-01", "required": True},
         {"index": 3, "role": "param", "label": "源输入卡", "placeholder": "例如：tp2.inp", "required": True},
         {"index": 4, "role": "param", "label": "可执行程序", "placeholder": "例如：relap5.exe", "required": False},
         {"index": 5, "role": "param", "label": "附加依赖（可选）", "placeholder": "全部 / runtime/*.dll / properties/tpfh2o；留空 = 软件库 bin + 默认依赖；无 = 不复制", "required": False},
     ]},
    {"name": "modify_inp", "object": "输入卡文件名", "params": "变量定位, 新值, -, -",
     "desc": "检索输入卡变量并原位修改；支持 RELAP5 卡定位和位置型 L行号:字段序号@行号",
     "fields": [
         {"index": 0, "role": "dir", "label": "运行目录", "placeholder": "先运行 folder_prep，例如 test_cases/case_01", "required": True},
         {"index": 2, "role": "object", "label": "输入卡文件", "placeholder": "例如：input.txt 或 indta", "required": True},
         {"index": 3, "role": "target", "label": "变量定位", "placeholder": "先检索并选择变量", "required": True, "readonly": True},
         {"index": 4, "role": "value", "label": "新值（修改后）", "placeholder": "填写修改后的字段值，例如：stdy-st", "required": True},
     ]},
    {"name": "run", "object": "可执行程序名", "params": "输出文件名, 结果关键字, 模拟值, -",
     "desc": "运行普通被测程序；RELAP5 请改用 run_software 或兼容入口 run_relap5，结果检查用 outdta 变量",
     "fields": [
         {"index": 0, "role": "dir", "label": "运行目录", "placeholder": "运行目录", "required": True},
         {"index": 2, "role": "object", "label": "可执行程序", "placeholder": "例如：solver.exe", "required": True},
         {"index": 3, "role": "param", "label": "输出文件名", "placeholder": "output.txt", "required": True},
         {"index": 4, "role": "param", "label": "结果关键字", "placeholder": "例如：keff", "required": False},
         {"index": 5, "role": "param", "label": "模拟值", "placeholder": "例如：1.00003", "required": False},
     ]},
    {"name": "run_relap5", "object": "relap5.exe", "params": "输入卡名, 输出文件名, 超时秒, -",
     "desc": "运行真实 RELAP5（-i indta -o outdta），自动忽略无效占位 exe 并分析 outdta",
     "fields": [
         {"index": 0, "role": "dir", "label": "运行目录", "placeholder": "运行目录", "required": True},
         {"index": 2, "role": "object", "label": "可执行程序", "placeholder": "relap5.exe", "required": True},
         {"index": 3, "role": "param", "label": "输入卡名", "placeholder": "indta", "required": True},
         {"index": 4, "role": "param", "label": "输出文件名", "placeholder": "outdta", "required": False},
         {"index": 5, "role": "param", "label": "超时（秒）", "placeholder": "1800", "required": False, "type": "number"},
     ]},
    {"name": "run_software", "object": "可执行程序", "params": "软件类型, 输入卡名, 输出文件/相对路径, 超时秒",
     "desc": "通过统一适配器运行软件；RELAP5 与 NUSOL-SYSLMR 可用，中子物理/子通道/燃料性能已预留但尚未实现",
     "fields": [
         {"index": 0, "role": "dir", "label": "运行目录", "placeholder": "运行目录", "required": True},
         {"index": 2, "role": "object", "label": "可执行程序", "placeholder": "例如：relap5.exe / nusolsyslmr.exe / solver.exe", "required": True},
         {"index": 3, "role": "param", "label": "软件类型", "placeholder": "relap5 / nusolsyslmr / neutron_physics / subchannel / fuel_performance", "required": True},
         {"index": 4, "role": "param", "label": "输入卡名", "placeholder": "例如：input.txt 或 indta", "required": True},
         {"index": 5, "role": "param", "label": "输出文件/相对路径", "placeholder": "例如：outdta、results/outdta 或以 results/ 结尾表示目录", "required": False},
         {"index": 6, "role": "param", "label": "超时（秒）", "placeholder": "例如：600", "required": False, "type": "number"},
     ]},
    {"name": "check_output", "object": "输出文件名", "params": "结果关键字, 期望值, 软件类型, -",
     "desc": "从输出中提取结果并回填、自动比对；软件类型可留空以兼容 RELAP5 旧剧本",
     "fields": [
         {"index": 0, "role": "dir", "label": "运行目录", "placeholder": "运行目录", "required": True},
         {"index": 2, "role": "object", "label": "输出文件", "placeholder": "例如：output.txt 或 outdta", "required": True},
         {"index": 3, "role": "param", "label": "结果关键字", "placeholder": "例如：p@100010000", "required": True},
         {"index": 4, "role": "param", "label": "期望值", "placeholder": "例如：15.5", "required": True},
         {"index": 5, "role": "param", "label": "软件类型（可选）", "placeholder": "relap5 / nusolsyslmr / neutron_physics / subchannel / fuel_performance", "required": False},
     ]},
    {"name": "run_code", "object": "可执行程序名", "params": "命令行参数, -, -, -",
     "desc": "直接执行被测程序",
     "fields": [
         {"index": 0, "role": "dir", "label": "运行目录", "placeholder": "运行目录", "required": True},
         {"index": 2, "role": "object", "label": "可执行程序", "placeholder": "例如：checker.exe", "required": True},
         {"index": 3, "role": "param", "label": "命令行参数", "placeholder": "留空或输入参数", "required": False},
     ]},
    {"name": "check", "object": "检查对象", "params": "说明, -, -, -",
     "desc": "占位检查",
     "fields": [
         {"index": 0, "role": "dir", "label": "运行目录", "placeholder": "运行目录", "required": False},
         {"index": 2, "role": "object", "label": "检查对象", "placeholder": "可留空", "required": False},
         {"index": 3, "role": "param", "label": "说明", "placeholder": "检查说明", "required": False},
     ]},
]

BUILTIN_NAMES: tuple[str, ...] = tuple(item["name"] for item in BUILTIN_KEYWORDS)


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def keyword_file(root: Path | str) -> Path:
    """返回项目的关键词文件路径。"""
    return Path(root) / KEYWORD_FILE


def load_custom(root: Path | str, *, strict: bool = False) -> list[dict]:
    """读取项目自定义关键词；文件不存在时返回空列表。"""
    path = keyword_file(root)
    if not path.is_file():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8") or "{}")
    except (OSError, json.JSONDecodeError) as exc:
        if strict:
            raise KeywordError(f"关键词文件无法解析: {exc}") from exc
        return []
    items = payload.get("keywords") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        if strict:
            raise KeywordError("关键词文件格式错误：缺少 keywords 列表")
        return []
    loaded = []
    for item in items:
        if not isinstance(item, dict):
            continue
        name = _text(item.get("name"))
        if not name or not _NAME_RE.match(name) or name in BUILTIN_NAMES:
            continue
        fields = [field for field in (item.get("fields") or []) if isinstance(field, dict)]
        if not fields:
            continue
        entry = dict(item)
        entry["name"] = name
        entry["fields"] = fields
        entry["impl"] = _text(item.get("impl")) or "command"
        loaded.append(entry)
    return loaded


def save_custom_list(root: Path | str, keywords: Iterable[dict]) -> Path:
    """把自定义关键词写回项目文件。"""
    path = keyword_file(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": KEYWORD_SCHEMA_VERSION,
        "keywords": [dict(item) for item in keywords],
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return path


def _normalize_fields(raw_fields: Any) -> list[dict]:
    if not isinstance(raw_fields, (list, tuple)):
        raise KeywordError("字段定义必须是列表")
    fields: list[dict] = []
    seen: set[int] = set()
    for raw in raw_fields:
        if not isinstance(raw, dict):
            raise KeywordError("字段定义的每一行必须是对象")
        index_raw = raw.get("index")
        try:
            index = int(index_raw)
        except (TypeError, ValueError) as exc:
            raise KeywordError(f"字段列号必须是 0~6 的整数: {index_raw}") from exc
        if index < 0 or index > 6:
            raise KeywordError(f"字段列号必须是 0~6 的整数: {index}")
        if index in seen:
            raise KeywordError(f"字段列号重复: {index}")
        seen.add(index)
        label = _text(raw.get("label"))
        if not label:
            raise KeywordError(f"列 {index} 的字段标签不能为空")
        if len(label) > 40:
            raise KeywordError(f"列 {index} 的字段标签过长（最多 40 个字符）")
        role = _text(raw.get("role")) or ("dir" if index == 0 else "object" if index == 2 else "param")
        if role not in ROLES:
            raise KeywordError(f"列 {index} 的字段角色不受支持: {role}")
        field_type = _text(raw.get("type")) or "text"
        if field_type not in ("text", "number"):
            raise KeywordError(f"列 {index} 的字段类型只支持 text 或 number")
        fields.append({
            "index": index,
            "role": role,
            "label": label,
            "placeholder": _text(raw.get("placeholder")),
            "required": bool(raw.get("required")),
            "readonly": bool(raw.get("readonly")),
            "type": field_type,
        })
    indices = {field["index"] for field in fields}
    if 0 not in indices:
        raise KeywordError("字段定义必须包含列 0（运行目录）")
    if 2 not in indices:
        raise KeywordError("字段定义必须包含列 2（对象列）")
    fields.sort(key=lambda field: field["index"])
    return fields


def normalize_keyword(payload: Any, root: Path | str | None = None) -> dict:
    """校验并补全一个自定义关键词定义。"""
    if not isinstance(payload, dict):
        raise KeywordError("关键词定义必须是对象")
    name = _text(payload.get("name"))
    if not name:
        raise KeywordError("关键词名称不能为空")
    if not _NAME_RE.match(name):
        raise KeywordError(
            "关键词名称只能使用英文字母、数字和下划线，且以字母开头（最多 40 个字符）"
        )
    if name in BUILTIN_NAMES:
        raise KeywordError(f"关键词名称与内置关键词冲突: {name}")

    impl = _text(payload.get("impl")) or COMMAND_IMPL
    if impl not in IMPL_CHOICES:
        raise KeywordError(
            f"执行方式不受支持: {impl}；可选值: {', '.join(IMPL_CHOICES)}"
        )

    command = _text(payload.get("command"))
    if impl == COMMAND_IMPL:
        if not command:
            raise KeywordError("执行方式为自定义命令行时必须填写命令模板")
        unknown = sorted({
            token for token in _PLACEHOLDER_RE.findall(command)
            if token not in COMMAND_PLACEHOLDERS
        })
        if unknown:
            raise KeywordError(
                "命令模板包含未知占位符: "
                + ", ".join("{" + token + "}" for token in unknown)
                + "；可用占位符: "
                + ", ".join("{" + token + "}" for token in COMMAND_PLACEHOLDERS)
            )
    else:
        command = ""

    fields = _normalize_fields(payload.get("fields"))
    params = _text(payload.get("params"))
    if not params:
        labels = [
            field["label"] for field in fields if field["index"] not in (0, 2)
        ]
        params = ", ".join(labels) if labels else "-"

    desc = _text(payload.get("desc"))
    if not desc:
        raise KeywordError("说明不能为空，便于其他人在剧本里看懂该关键词的作用")
    object_label = _text(payload.get("object"))
    if not object_label:
        object_field = next(
            (field for field in fields if field["index"] == 2), None
        )
        object_label = object_field["label"] if object_field else "对象"

    keyword = {
        "name": name,
        "object": object_label,
        "params": params,
        "desc": desc,
        "impl": impl,
        "command": command,
        "fields": fields,
        "custom": True,
    }
    if root is not None:
        existing = {item["name"] for item in load_custom(root)}
        if name in existing:
            raise KeywordError(f"自定义关键词已存在: {name}")
    return keyword


def save_custom(
    root: Path | str,
    payload: Any,
    *,
    original_name: str = "",
) -> dict:
    """新增或更新一个自定义关键词（``original_name`` 用于重命名）。"""
    root = Path(root)
    original = _text(original_name)
    existing = load_custom(root)
    names = {item["name"] for item in existing}
    replaced = False
    if original:
        if original not in names:
            raise KeywordError(f"要修改的自定义关键词不存在: {original}")
        names.discard(original)
        replaced = True

    payload = dict(payload or {})
    name = _text(payload.get("name"))
    if name in names:
        raise KeywordError(f"自定义关键词已存在: {name}")
    if name in BUILTIN_NAMES:
        raise KeywordError(f"关键词名称与内置关键词冲突: {name}")
    keyword = normalize_keyword(payload)

    updated = []
    for item in existing:
        if replaced and item["name"] == original:
            updated.append(keyword)
        else:
            updated.append(item)
    if not replaced:
        updated.append(keyword)
    save_custom_list(root, updated)
    return keyword


def delete_custom(root: Path | str, name: str) -> None:
    """删除一个自定义关键词。"""
    name = _text(name)
    if name in BUILTIN_NAMES:
        raise KeywordError("内置关键词不能删除")
    existing = load_custom(root, strict=True)
    remaining = [item for item in existing if item["name"] != name]
    if len(remaining) == len(existing):
        raise KeywordError(f"自定义关键词不存在: {name}")
    save_custom_list(root, remaining)


def all_keywords(root: Path | str | None = None) -> list[dict]:
    """内置关键词在前，项目自定义关键词在后。"""
    items = [
        {**item, "custom": False, "impl": item["name"], "source": "内置"}
        for item in BUILTIN_KEYWORDS
    ]
    if root is not None:
        for item in load_custom(root):
            items.append({**item, "source": "自定义"})
    return items


def find_custom(root: Path | str | None, name: str) -> dict | None:
    """按名称查找自定义关键词（执行层使用）。"""
    if root is None:
        return None
    target = _text(name)
    if not target or target in BUILTIN_NAMES:
        return None
    for item in load_custom(root):
        if item["name"] == target:
            return item
    return None
