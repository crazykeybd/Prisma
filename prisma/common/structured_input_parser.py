"""Structured text and JSON input parsing and safe field editing.

The structured-text profile covers SNAP/LOCUST-style files such as
``system1.i``: hierarchical ``section:`` blocks, ``key = value`` assignments,
and brace-delimited arrays whose rows are separated by semicolons.

JSON is parsed with source spans so a selected scalar can be changed in place
without reformatting the rest of the document.
"""

from __future__ import annotations

import bisect
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote

from common.relap_input_parser import (
    _changed_preview,
    _decode_bytes,
    _encoded_bytes,
    _is_number,
    _newline_style,
    _replace_bytes_atomic,
    _unified_text_diff,
)


STRUCTURED_TEXT_PROFILE = "structured_text"
JSON_PROFILE = "json"

_CONTAINER_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_.-]*)\s*:\s*$")
_CLOSE_RE = re.compile(r"^\s*/([A-Za-z_][A-Za-z0-9_.-]*)\s*$")
_ASSIGNMENT_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_.-]*)\s*=\s*")
_STRUCTURED_TARGET_RE = re.compile(
    r"^\s*(?P<prefix>[SJ])(?P<line>\d+):(?P<field>\d+)"
    r"@(?P<source_line>\d+):(?P<path>.+?)\s*$",
    re.IGNORECASE,
)
_JSON_NUMBER_RE = re.compile(
    r"-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?"
)
_TARGET_PATH_SAFE = "._[]$-"


def structured_text_score(text: str) -> int:
    """Estimate whether text uses the hierarchical structured-text syntax."""
    assignments = len(_ASSIGNMENT_RE.findall(text))
    containers = len(_CONTAINER_RE.findall(text))
    close_tags = len(_CLOSE_RE.findall(text))
    braces = len(re.findall(r"=\s*\{", text))
    return assignments + containers + close_tags + braces


def looks_like_json(text: str, filename: str = "") -> bool:
    suffix = Path(filename).suffix.lower() if filename else ""
    stripped = text.lstrip("\ufeff \t\r\n")
    return suffix == ".json" or stripped.startswith("{") or stripped.startswith("[")


def target_profile(target: str) -> str | None:
    match = _STRUCTURED_TARGET_RE.match(str(target or ""))
    if not match:
        return None
    return (
        JSON_PROFILE
        if match.group("prefix").upper() == "J"
        else STRUCTURED_TEXT_PROFILE
    )


def field_target(
    profile: str,
    line_no: int,
    field: int,
    source_line: int,
    path: str,
) -> str:
    prefix = "J" if profile == JSON_PROFILE else "S"
    encoded_path = quote(str(path or "$"), safe=_TARGET_PATH_SAFE)
    return f"{prefix}{line_no}:{field}@{source_line}:{encoded_path}"


def exact_field(target: str, profile: str) -> int | None:
    match = _STRUCTURED_TARGET_RE.match(str(target or ""))
    if not match or target_profile(target) != profile:
        return None
    return int(match.group("field")) - 1


def target_matches(target: str, expected_profile: str, candidate: str) -> bool:
    match = _STRUCTURED_TARGET_RE.match(str(candidate or "").strip())
    if not match or target_profile(candidate) != expected_profile:
        return False
    return str(candidate or "").strip().lower() == str(target or "").strip().lower()


def _line_data(text: str) -> list[tuple[int, str, int, int]]:
    if not text:
        return []
    lines: list[tuple[int, str, int, int]] = []
    line_no = 1
    start = 0
    for match in re.finditer(r"\r\n|\n|\r", text):
        lines.append((line_no, text[start:match.start()], start, match.end()))
        line_no += 1
        start = match.end()
    lines.append((line_no, text[start:], start, len(text)))
    return lines


def _line_for_offset(starts: list[int], offset: int) -> int:
    return bisect.bisect_right(starts, offset)


def _scan_quoted(text: str, start: int) -> int:
    quote_char = text[start]
    index = start + 1
    while index < len(text):
        char = text[index]
        if char == "\\":
            index += 2
            continue
        if char == quote_char:
            return index + 1
        index += 1
    raise ValueError("字符串引号未闭合")


def _skip_comment(text: str, index: int, end: int) -> int:
    if text.startswith("//", index):
        newline = text.find("\n", index, end)
        return end if newline < 0 else newline + 1
    if text.startswith("/*", index):
        close = text.find("*/", index + 2, end)
        if close < 0:
            raise ValueError("块注释未闭合")
        return close + 2
    return index


def _scan_braced(text: str, start: int) -> int:
    depth = 0
    index = start
    while index < len(text):
        char = text[index]
        if char in ("'", '"'):
            index = _scan_quoted(text, index)
            continue
        next_index = _skip_comment(text, index, len(text))
        if next_index != index:
            index = next_index
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index + 1
        index += 1
    raise ValueError("花括号数组未闭合")


def _skip_value_space(text: str, index: int, stop: int) -> int:
    while index < stop:
        if text[index].isspace():
            index += 1
            continue
        next_index = _skip_comment(text, index, stop)
        if next_index != index:
            index = next_index
            continue
        break
    return index


def _scan_scalar(text: str, start: int, stop: int) -> int:
    if start >= stop:
        return start
    if text[start] in ("'", '"'):
        return min(_scan_quoted(text, start), stop)
    index = start
    while index < stop:
        if text[index] in ",;{}" or text[index].isspace():
            break
        if text.startswith("//", index) or text.startswith("/*", index):
            break
        index += 1
    return index


def _decode_quoted(raw: str) -> str:
    if len(raw) < 2 or raw[0] not in ("'", '"') or raw[-1] != raw[0]:
        return raw
    if raw[0] == '"':
        try:
            return str(json.loads(raw))
        except json.JSONDecodeError:
            pass
    body = raw[1:-1]
    return (
        body.replace("\\\\", "\\")
        .replace("\\'", "'")
        .replace('\\"', '"')
    )


def _value_kind(raw: str) -> str:
    stripped = raw.strip()
    if len(stripped) >= 2 and stripped[0] in ("'", '"') and stripped[-1] == stripped[0]:
        return "string"
    if _is_number(stripped):
        return "number"
    if stripped.lower() in {"true", "false", "null"}:
        return "literal"
    return "text"


def _display_value(raw: str) -> str:
    stripped = raw.strip()
    return _decode_quoted(stripped) if _value_kind(stripped) == "string" else stripped


def _tokens_in_braces(
    text: str,
    value_start: int,
    value_end: int,
    starts: list[int],
) -> list[dict[str, Any]]:
    tokens: list[dict[str, Any]] = []
    index = value_start + 1
    stop = max(value_start + 1, value_end - 1)
    while index < stop:
        index = _skip_value_space(text, index, stop)
        while index < stop and text[index] in ",;":
            index += 1
            index = _skip_value_space(text, index, stop)
        if index >= stop:
            break
        token_start = index
        token_end = _scan_scalar(text, token_start, stop)
        if token_end <= token_start:
            token_end = token_start + 1
        raw = text[token_start:token_end]
        tokens.append({
            "raw": raw,
            "value": _display_value(raw),
            "kind": _value_kind(raw),
            "span": [token_start, token_end],
            "line_no": _line_for_offset(starts, token_start),
        })
        index = token_end
    return tokens


def _record_entry(
    *,
    line_no: int,
    raw: str,
    path: str,
    key: str,
    category: str,
    tokens: list[dict[str, Any]],
    format_name: str,
) -> dict[str, Any]:
    fields = [str(token["value"]) for token in tokens]
    spans = [list(token["span"]) for token in tokens]
    field_lines = [int(token["line_no"]) for token in tokens]
    targets = [
        {
            "field": index + 1,
            "value": value,
            "target": field_target(
                STRUCTURED_TEXT_PROFILE if format_name == "structured-text" else JSON_PROFILE,
                line_no,
                index + 1,
                field_lines[index],
                path,
            ),
            "source_line_no": field_lines[index],
        }
        for index, value in enumerate(fields)
    ]
    return {
        "line_no": line_no,
        "source_line_no": line_no,
        "raw": raw,
        "kind": "record",
        "card_id": "",
        "card": "",
        "record_id": path,
        "category": category,
        "keyword": key,
        "target_keyword": path,
        "title": path,
        "fields": fields,
        "tokens": fields,
        "field_spans": spans,
        "field_line_nos": field_lines,
        "field_kinds": [str(token["kind"]) for token in tokens],
        "field_start": spans[0][0] if spans else 0,
        "field_end": spans[-1][1] if spans else 0,
        "description": path,
        "description_span": [],
        "comment": "",
        "format": format_name,
        "external_line": None,
        "continuation": False,
        "parent_line_no": None,
        "effective": bool(tokens),
        "overridden": False,
        "deleted": False,
        "deletion": False,
        "field_targets": targets,
        "target": targets[0]["target"] if targets else "",
    }


def _simple_entry(line_no: int, raw: str, kind: str) -> dict[str, Any]:
    return {
        "line_no": line_no,
        "source_line_no": line_no,
        "raw": raw,
        "kind": kind,
        "card_id": "",
        "card": "",
        "record_id": "",
        "category": "",
        "keyword": "",
        "target_keyword": "",
        "title": "",
        "fields": [],
        "tokens": [],
        "field_spans": [],
        "field_line_nos": [],
        "field_kinds": [],
        "field_start": 0,
        "field_end": 0,
        "description": "",
        "description_span": [],
        "comment": "",
        "format": "",
        "external_line": None,
        "continuation": False,
        "parent_line_no": None,
        "effective": False,
        "overridden": False,
        "deleted": False,
        "deletion": False,
        "field_targets": [],
        "target": "",
    }


def parse_structured_text(
    text: str,
    *,
    encoding: str,
    filename: str,
    path: str = "",
) -> dict[str, Any]:
    """Parse hierarchical object blocks and brace-delimited arrays."""
    if text.startswith("\ufeff"):
        text = text.lstrip("\ufeff")
        encoding = "UTF-8 BOM"
    lines = _line_data(text)
    starts = [item[2] for item in lines]
    entries: list[dict[str, Any]] = []
    stack: list[str] = []
    categories: dict[str, int] = {}
    preview: list[str] = []
    total_records = 0
    total_fields = 0
    numeric_fields = 0
    unparsed = 0
    blank_lines = 0
    comment_lines = 0

    index = 0
    while index < len(lines):
        line_no, raw, line_start, _ = lines[index]
        stripped = raw.strip()
        if not stripped:
            entries.append(_simple_entry(line_no, raw, "blank"))
            blank_lines += 1
            index += 1
            continue
        if stripped.startswith("//") or stripped.startswith("#"):
            entries.append(_simple_entry(line_no, raw, "comment"))
            comment_lines += 1
            index += 1
            continue
        if len(preview) < 8:
            preview.append(raw)

        close_match = _CLOSE_RE.match(raw)
        if close_match:
            name = close_match.group(1)
            for stack_index in range(len(stack) - 1, -1, -1):
                if stack[stack_index] == name:
                    del stack[stack_index:]
                    break
            index += 1
            continue

        container_match = _CONTAINER_RE.match(raw)
        if container_match:
            stack.append(container_match.group(1))
            index += 1
            continue

        assignment = _ASSIGNMENT_RE.match(raw)
        if not assignment:
            entries.append(_simple_entry(line_no, raw, "unparsed"))
            unparsed += 1
            index += 1
            continue

        key = assignment.group(1)
        value_start = line_start + assignment.end()
        value_start = _skip_value_space(text, value_start, len(text))
        if value_start >= len(text):
            entries.append(_simple_entry(line_no, raw, "unparsed"))
            unparsed += 1
            index += 1
            continue

        try:
            if text[value_start] == "{":
                value_end = _scan_braced(text, value_start)
                tokens = _tokens_in_braces(text, value_start, value_end, starts)
            elif text[value_start] in ("'", '"'):
                value_end = _scan_quoted(text, value_start)
                token_raw = text[value_start:value_end]
                tokens = [{
                    "raw": token_raw,
                    "value": _display_value(token_raw),
                    "kind": _value_kind(token_raw),
                    "span": [value_start, value_end],
                    "line_no": _line_for_offset(starts, value_start),
                }]
            else:
                value_end = value_start
                while value_end < len(text) and text[value_end] not in "\r\n":
                    if text.startswith("//", value_end):
                        break
                    value_end += 1
                while value_end > value_start and text[value_end - 1].isspace():
                    value_end -= 1
                token_raw = text[value_start:value_end]
                tokens = [{
                    "raw": token_raw,
                    "value": _display_value(token_raw),
                    "kind": _value_kind(token_raw),
                    "span": [value_start, value_end],
                    "line_no": line_no,
                }]
        except ValueError:
            entries.append(_simple_entry(line_no, raw, "unparsed"))
            unparsed += 1
            index += 1
            continue

        record_path = ".".join([*stack, key])
        category = stack[0] if stack else ""
        raw_record = text[line_start:value_end]
        entry = _record_entry(
            line_no=line_no,
            raw=raw_record,
            path=record_path,
            key=key,
            category=category,
            tokens=tokens,
            format_name="structured-text",
        )
        entries.append(entry)
        total_records += 1
        total_fields += len(tokens)
        numeric_fields += sum(token["kind"] == "number" for token in tokens)
        if category:
            categories[category] = categories.get(category, 0) + 1

        index += 1
        while index < len(lines) and lines[index][2] < value_end:
            index += 1

    warnings: list[str] = []
    if total_records == 0:
        warnings.append("未识别到结构化参数记录，请检查 section/key = value 格式。")
    if unparsed:
        warnings.append(f"有 {unparsed} 行非空内容未识别为结构化参数。")
    if "混合" in _newline_style(text):
        warnings.append("文件包含混合换行符")

    return {
        "summary": {
            "filename": filename,
            "path": path,
            "encoding": encoding,
            "newline": _newline_style(text),
            "format": "structured-text",
            "profile": STRUCTURED_TEXT_PROFILE,
            "profile_label": "结构化对象 + 矩阵",
            "target_format": "S{记录行}:{字段序号}@{物理行}:{变量路径}",
            "total_lines": len(lines),
            "nonempty_lines": sum(bool(item[1].strip()) for item in lines),
            "blank_lines": blank_lines,
            "total_cards": 0,
            "unique_cards": 0,
            "effective_cards": 0,
            "total_records": total_records,
            "effective_records": total_records,
            "total_containers": sum(1 for entry in entries if entry.get("kind") == "record"),
            "record_fields": total_fields,
            "numeric_fields": numeric_fields,
            "unparsed_lines": unparsed,
            "comment_lines": comment_lines,
            "continuation_lines": 0,
            "ignored_columns": 0,
            "has_end_card": False,
            "has_time_control": False,
            "has_hydrodynamic": False,
            "has_heat_structure": False,
            "categories": categories,
            "kinds": {
                "record": total_records,
                "blank": blank_lines,
                "comment": comment_lines,
                **({"unparsed": unparsed} if unparsed else {}),
            },
            "title": "",
            "preview": preview,
            "warnings": warnings,
        },
        "entries": entries,
    }


class _JsonStructureParser:
    def __init__(self, text: str) -> None:
        self.text = text.lstrip("\ufeff")
        self.bom_length = len(text) - len(self.text)
        self.length = len(self.text)
        self.pos = 0
        self.starts = [item[2] for item in _line_data(self.text)]

    def parse(self) -> dict[str, Any]:
        node = self._value("$")
        self._space()
        if self.pos != self.length:
            raise ValueError(f"JSON 第 {self._line()} 行存在多余内容")
        return node

    def _line(self, offset: int | None = None) -> int:
        return _line_for_offset(self.starts, self.pos if offset is None else offset)

    def _space(self) -> None:
        while self.pos < self.length and self.text[self.pos].isspace():
            self.pos += 1

    def _expect(self, char: str) -> None:
        self._space()
        if self.pos >= self.length or self.text[self.pos] != char:
            raise ValueError(f"JSON 第 {self._line()} 行缺少 {char}")
        self.pos += 1

    def _string(self) -> tuple[str, int, int]:
        self._space()
        start = self.pos
        if start >= self.length or self.text[start] != '"':
            raise ValueError(f"JSON 第 {self._line()} 行应为字符串")
        end = _scan_quoted(self.text, start)
        raw = self.text[start:end]
        try:
            value = str(json.loads(raw))
        except json.JSONDecodeError as exc:
            raise ValueError(f"JSON 第 {self._line(start)} 行字符串无效") from exc
        self.pos = end
        return value, start, end

    def _scalar(self, path: str, line_no: int) -> dict[str, Any]:
        self._space()
        start = self.pos
        if start >= self.length:
            raise ValueError("JSON 内容意外结束")
        char = self.text[start]
        if char == '"':
            value, start, end = self._string()
            kind = "string"
        elif char == "t" and self.text.startswith("true", start):
            end = start + 4
            value = True
            kind = "literal"
            self.pos = end
        elif char == "f" and self.text.startswith("false", start):
            end = start + 5
            value = False
            kind = "literal"
            self.pos = end
        elif char == "n" and self.text.startswith("null", start):
            end = start + 4
            value = None
            kind = "literal"
            self.pos = end
        else:
            match = _JSON_NUMBER_RE.match(self.text, start)
            if not match:
                raise ValueError(f"JSON 第 {self._line(start)} 行值无效")
            end = match.end()
            raw = self.text[start:end]
            value = float(raw) if any(ch in raw for ch in ".eE") else int(raw)
            kind = "number"
            self.pos = end
        return {
            "kind": "scalar",
            "value": value,
            "raw": self.text[start:end],
            "start": start,
            "end": end,
            "line": line_no,
            "value_kind": kind,
            "path": path,
        }

    def _array(self, path: str, line_no: int) -> dict[str, Any]:
        self._expect("[")
        children: list[dict[str, Any]] = []
        self._space()
        if self.pos < self.length and self.text[self.pos] == "]":
            self.pos += 1
            return {
                "kind": "array",
                "children": children,
                "start": self.pos - 1,
                "end": self.pos,
                "line": line_no,
                "path": path,
            }
        index = 0
        while True:
            self._space()
            child_line = self._line()
            children.append(self._value(f"{path}[{index}]", child_line))
            index += 1
            self._space()
            if self.pos < self.length and self.text[self.pos] == ",":
                self.pos += 1
                continue
            self._expect("]")
            break
        return {
            "kind": "array",
            "children": children,
            "start": children[0]["start"] - 1 if children else self.pos - 1,
            "end": self.pos,
            "line": line_no,
            "path": path,
        }

    def _object(self, path: str, line_no: int) -> dict[str, Any]:
        start = self.pos
        self._expect("{")
        children: list[tuple[str, dict[str, Any]]] = []
        self._space()
        if self.pos < self.length and self.text[self.pos] == "}":
            self.pos += 1
            return {
                "kind": "object",
                "children": children,
                "start": start,
                "end": self.pos,
                "line": line_no,
                "path": path,
            }
        while True:
            self._space()
            key_line = self._line()
            key, key_start, _ = self._string()
            self._expect(":")
            child_path = f"{path}.{key}" if path != "$" else f"$.{key}"
            self._space()
            value_line = self._line()
            child = self._value(child_path, value_line)
            child["key"] = key
            child["key_start"] = key_start
            children.append((key, child))
            self._space()
            if self.pos < self.length and self.text[self.pos] == ",":
                self.pos += 1
                continue
            self._expect("}")
            break
        return {
            "kind": "object",
            "children": children,
            "start": start,
            "end": self.pos,
            "line": line_no,
            "path": path,
        }

    def _value(self, path: str, line_no: int | None = None) -> dict[str, Any]:
        self._space()
        current_line = self._line() if line_no is None else line_no
        if self.pos >= self.length:
            raise ValueError("JSON 内容意外结束")
        if self.text[self.pos] == "{":
            return self._object(path, current_line)
        if self.text[self.pos] == "[":
            return self._array(path, current_line)
        return self._scalar(path, current_line)


def _collect_scalar_nodes(node: dict[str, Any]) -> list[dict[str, Any]]:
    if node["kind"] == "scalar":
        return [node]
    if node["kind"] == "array":
        values: list[dict[str, Any]] = []
        for child in node.get("children") or []:
            if child["kind"] == "object":
                continue
            values.extend(_collect_scalar_nodes(child))
        return values
    return []


def _array_is_scalar_matrix(node: dict[str, Any]) -> bool:
    if node["kind"] == "scalar":
        return True
    if node["kind"] != "array":
        return False
    return all(
        child["kind"] != "object" and _array_is_scalar_matrix(child)
        for child in node.get("children") or []
    )


def _flatten_json_entries(
    node: dict[str, Any],
    entries: list[dict[str, Any]],
    category: str,
) -> None:
    if node["kind"] == "object":
        for _, child in node.get("children") or []:
            _flatten_json_entries(child, entries, category)
        return
    if node["kind"] == "array" and not _array_is_scalar_matrix(node):
        for child in node.get("children") or []:
            _flatten_json_entries(child, entries, category)
        return

    scalar_nodes = _collect_scalar_nodes(node)
    if not scalar_nodes:
        return
    def display(value: Any) -> str:
        if value is None:
            return "null"
        if value is True:
            return "true"
        if value is False:
            return "false"
        return str(value)

    tokens = [{
        "raw": item["raw"],
        "value": display(item["value"]),
        "kind": item["value_kind"],
        "span": [item["start"], item["end"]],
        "line_no": item["line"],
    } for item in scalar_nodes]
    key = node.get("key") or node["path"].rsplit(".", 1)[-1]
    record_start = int(node.get("key_start") or node["start"])
    record_end = int(node["end"])
    entries.append(_record_entry(
        line_no=int(node["line"]),
        raw="",
        path=str(node["path"]),
        key=str(key),
        category=category,
        tokens=tokens,
        format_name="json",
    ))
    entries[-1]["raw"] = ""
    entries[-1]["field_start"] = record_start
    entries[-1]["field_end"] = record_end


def parse_json_text(
    text: str,
    *,
    encoding: str,
    filename: str,
    path: str = "",
) -> dict[str, Any]:
    """Parse JSON while retaining exact scalar source spans."""
    try:
        parser = _JsonStructureParser(text)
        root = parser.parse()
    except ValueError as exc:
        return {"error": f"JSON 解析失败: {exc}"}

    entries: list[dict[str, Any]] = []
    _flatten_json_entries(root, entries, "JSON")
    total_fields = sum(len(entry.get("fields") or []) for entry in entries)
    numeric_fields = sum(
        kind == "number"
        for entry in entries
        for kind in entry.get("field_kinds") or []
    )
    warnings: list[str] = []
    if not entries:
        warnings.append("JSON 中没有可检索的标量字段。")
    if "混合" in _newline_style(parser.text):
        warnings.append("文件包含混合换行符")

    return {
        "summary": {
            "filename": filename,
            "path": path,
            "encoding": encoding,
            "newline": _newline_style(parser.text),
            "format": "json",
            "profile": JSON_PROFILE,
            "profile_label": "JSON 对象 + 数组",
            "target_format": "J{记录行}:{字段序号}@{物理行}:{变量路径}",
            "total_lines": len(_line_data(parser.text)),
            "nonempty_lines": sum(
                bool(item[1].strip()) for item in _line_data(parser.text)
            ),
            "blank_lines": sum(
                not item[1].strip() for item in _line_data(parser.text)
            ),
            "total_cards": 0,
            "unique_cards": 0,
            "effective_cards": 0,
            "total_records": len(entries),
            "effective_records": len(entries),
            "record_fields": total_fields,
            "numeric_fields": numeric_fields,
            "unparsed_lines": 0,
            "comment_lines": 0,
            "continuation_lines": 0,
            "ignored_columns": 0,
            "has_end_card": False,
            "has_time_control": False,
            "has_hydrodynamic": False,
            "has_heat_structure": False,
            "categories": {"JSON": len(entries)} if entries else {},
            "kinds": {"record": len(entries)},
            "title": "",
            "preview": parser.text.splitlines()[:8],
            "warnings": warnings,
        },
        "entries": entries,
    }


def _parse_target(target: str, profile: str) -> tuple[int, int, int, str]:
    match = _STRUCTURED_TARGET_RE.match(str(target or ""))
    if not match or target_profile(target) != profile:
        prefix = "J" if profile == JSON_PROFILE else "S"
        raise ValueError(
            f"变量定位格式无效，应为 {prefix}记录行:字段序号@物理行:变量路径，"
            f"例如 {prefix}8:1@9:hydro.p004.vinit"
        )
    return (
        int(match.group("line")),
        int(match.group("field")),
        int(match.group("source_line")),
        unquote(match.group("path")),
    )


def _locate(
    parsed: dict[str, Any],
    line_no: int,
    field: int,
    source_line: int,
    path: str,
) -> dict[str, Any]:
    matches = [
        entry for entry in parsed.get("entries", [])
        if entry.get("kind") == "record"
        and int(entry.get("line_no") or 0) == line_no
        and str(entry.get("record_id") or "") == path
    ]
    if not matches:
        raise ValueError(f"未找到结构化变量：第 {line_no} 行，路径 {path}")
    entry = matches[0]
    fields = entry.get("fields") or []
    spans = entry.get("field_spans") or []
    field_lines = entry.get("field_line_nos") or []
    if field < 1 or field > len(fields) or field > len(spans):
        raise ValueError(f"字段序号越界：{path} 只有 {len(fields)} 个字段")
    actual_line = (
        int(field_lines[field - 1])
        if field - 1 < len(field_lines)
        else int(entry.get("line_no") or 0)
    )
    if actual_line != source_line:
        raise ValueError(
            f"字段所在物理行不一致：定位为 {source_line}，实际为 {actual_line}"
        )
    return {
        "entry": entry,
        "field": field,
        "value": fields[field - 1],
        "span": spans[field - 1],
        "field_line_no": actual_line,
        "kind": (entry.get("field_kinds") or [])[field - 1],
    }


def _quoted_value(value: str, quote_char: str) -> str:
    if quote_char == '"':
        return json.dumps(value, ensure_ascii=False)
    escaped = value.replace("\\", "\\\\").replace("'", "\\'")
    return f"'{escaped}'"


def _replacement(
    old_raw: str,
    old_kind: str,
    value: str,
    profile: str,
) -> str:
    if "\r" in value or "\n" in value:
        raise ValueError("新值不能包含换行")
    if old_kind == "string":
        quote_char = old_raw[0] if old_raw and old_raw[0] in ("'", '"') else '"'
        if profile == JSON_PROFILE:
            quote_char = '"'
        return _quoted_value(value, quote_char)
    if old_kind == "number":
        if profile == JSON_PROFILE:
            if not _JSON_NUMBER_RE.fullmatch(value):
                raise ValueError(f"目标字段是数值域，新值必须为 JSON 数值：{value}")
        elif not _is_number(value):
            raise ValueError(f"目标字段是数值域，新值必须为数值：{value}")
        return value
    if old_kind == "literal":
        lowered = value.lower()
        if lowered not in {"true", "false", "null"}:
            raise ValueError("目标字段是布尔值或 null，新值必须为 true、false 或 null")
        return lowered
    if not value or any(char.isspace() for char in value):
        raise ValueError("普通字段的新值不能为空或包含空白")
    return value


def _verify(
    before: dict[str, Any],
    after: dict[str, Any],
    value: str,
) -> None:
    index = int(before["field"]) - 1
    old_fields = before["entry"].get("fields") or []
    new_fields = after["entry"].get("fields") or []
    if len(old_fields) != len(new_fields):
        raise ValueError("修改后字段数发生变化")
    if new_fields[index] != value:
        raise ValueError(
            f"修改后变量回读不一致：期望 {value}，实际 {new_fields[index]}"
        )
    if old_fields[:index] + old_fields[index + 1:] != new_fields[:index] + new_fields[index + 1:]:
        raise ValueError("修改后同一变量的其他字段发生变化")


def modify_structured_input_file(
    path: str | Path,
    target: str,
    value: str,
    *,
    profile: str,
    expected_value: str | None = None,
    apply: bool = True,
) -> dict[str, Any]:
    """Replace one scalar in a structured-text or JSON file."""
    p = Path(path)
    if not p.is_file():
        return {"ok": False, "error": f"输入卡不存在: {path}", "applied": False}
    try:
        line_no, field, source_line, target_path = _parse_target(target, profile)
        data = p.read_bytes()
        text, encoding_label = _decode_bytes(data)
        parser = parse_json_text if profile == JSON_PROFILE else parse_structured_text
        parsed = parser(text, encoding=encoding_label, filename=p.name, path=str(p))
        if parsed.get("error"):
            raise ValueError(parsed["error"])
        located = _locate(parsed, line_no, field, source_line, target_path)
        old_value = located["value"]
        if expected_value is not None and str(old_value) != str(expected_value):
            return {
                "ok": False,
                "applied": False,
                "error": f"变量当前值已变化：期望 {expected_value}，实际 {old_value}",
                "old_value": old_value,
                "target": target,
            }

        start, end = located["span"]
        old_raw = text[start:end]
        encoded_value = _replacement(old_raw, str(located["kind"]), str(value), profile)
        new_text = text[:start] + encoded_value + text[end:]
        new_parsed = parser(
            new_text,
            encoding=encoding_label,
            filename=p.name,
            path=str(p),
        )
        if new_parsed.get("error"):
            raise ValueError(new_parsed["error"])
        new_located = _locate(new_parsed, line_no, field, source_line, target_path)
        _verify(located, new_located, str(value))
        if len(new_text.splitlines()) != len(text.splitlines()):
            raise ValueError("修改后物理行数发生变化")
        if _newline_style(new_text) != _newline_style(text):
            raise ValueError("修改后换行风格发生变化")

        candidate_data = _encoded_bytes(new_text, encoding_label)
        decoded_check, _ = _decode_bytes(candidate_data)
        if decoded_check != new_text:
            raise ValueError("修改结果无法按原编码无损回读")
        preview = _changed_preview(
            text,
            new_text,
            start,
            end,
            start,
            start + len(encoded_value),
        )
        diff = _unified_text_diff(text, new_text, p)
        entry = located["entry"]
        common = {
            "ok": True,
            "path": str(p),
            "profile": profile,
            "format": "json" if profile == JSON_PROFILE else "structured-text",
            "target": target,
            "record_id": entry.get("record_id"),
            "old_value": old_value,
            "new_value": str(value),
            "encoded_value": encoded_value,
            "line_no": line_no,
            "field_line_no": source_line,
            "card_id": "",
            "keyword": entry.get("keyword") or "",
            "target_keyword": entry.get("target_keyword") or "",
            "description": entry.get("description") or "",
            "field": field,
            "encoding": encoding_label,
            "preview": preview,
            "diff": diff,
        }
        if not apply:
            return {**common, "applied": False, "dry_run": True}

        if p.read_bytes() != data:
            raise ValueError("输入卡在修改过程中被其他进程更改，已取消写回")
        _replace_bytes_atomic(p, candidate_data)
        written = p.read_bytes()
        if written != candidate_data:
            raise RuntimeError("写回后文件内容与候选内容不一致")
        written_text, written_encoding = _decode_bytes(written)
        written_parsed = parser(
            written_text,
            encoding=written_encoding,
            filename=p.name,
            path=str(p),
        )
        if written_parsed.get("error"):
            raise RuntimeError(written_parsed["error"])
        written_located = _locate(
            written_parsed, line_no, field, source_line, target_path
        )
        _verify(located, written_located, str(value))
        if written_encoding != encoding_label:
            raise RuntimeError("写回后文件编码发生变化")
        return {**common, "applied": True, "dry_run": False}
    except (OSError, UnicodeError, ValueError, RuntimeError) as exc:
        return {
            "ok": False,
            "applied": False,
            "error": str(exc),
            "target": target,
        }


__all__ = [
    "JSON_PROFILE",
    "STRUCTURED_TEXT_PROFILE",
    "exact_field",
    "field_target",
    "looks_like_json",
    "modify_structured_input_file",
    "parse_json_text",
    "parse_structured_text",
    "structured_text_score",
    "target_matches",
    "target_profile",
]
