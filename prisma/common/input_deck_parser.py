"""Generic input-deck parsing, searching, and safe field editing.

RELAP5 keeps its existing card-aware parser.  The generic layer adds a
``positional_text`` profile for decks that place one or more values in a
fixed-width left column and a human-readable description to the right, as in
``CheckSGInput.txt``.

Positional records use targets of the form ``L{LINE}:{FIELD}@{LINE}``.  An
edit preserves the description column, line width, encoding, newlines, blank
lines, and all non-target fields.
"""

from __future__ import annotations

import difflib
import math
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from common.relap_input_parser import (
    _changed_preview,
    _decode_bytes,
    _encoded_bytes,
    _is_number,
    _newline_style,
    _physical_line_bounds,
    _replace_bytes_atomic,
    _token_spans,
    _unified_text_diff,
    apply_relap_input_edit,
    modify_relap_input_file,
    parse_relap_input_text,
)


@dataclass(frozen=True)
class PositionalTextProfile:
    key: str
    label: str
    description_column: int
    category: str = "位置型参数"


POSITIONAL_TEXT_PROFILE = PositionalTextProfile(
    key="positional_text",
    label="位置型值 + 说明",
    description_column=19,
)

FORMAT_PROFILES = {
    "relap5": {
        "key": "relap5",
        "label": "RELAP5 卡格式",
        "format": "fixed-width/free-format",
    },
    POSITIONAL_TEXT_PROFILE.key: {
        "key": POSITIONAL_TEXT_PROFILE.key,
        "label": POSITIONAL_TEXT_PROFILE.label,
        "format": "positional-text",
        "description_column": POSITIONAL_TEXT_PROFILE.description_column,
    },
}

_FORMAT_ALIASES = {
    "relap": "relap5",
    "relap5": "relap5",
    "relap-5": "relap5",
    "relap5.exe": "relap5",
    "positional": "positional_text",
    "positional-text": "positional_text",
    "positional_text": "positional_text",
    "line-value-description": "positional_text",
    "line_value_description": "positional_text",
    "checksg": "positional_text",
    "check_sg": "positional_text",
    "sg": "positional_text",
    "generic": "positional_text",
    "generic_text": "positional_text",
}

_POSITIONAL_TARGET_RE = re.compile(
    r"^\s*L(?P<line>\d+):(?P<field>\d+)@(?P<source_line>\d+)\s*$",
    re.IGNORECASE,
)
_RELAP_TARGET_RE = re.compile(
    r"^\s*(?P<card>\d{3,8}):(?P<keyword>.+?):"
    r"(?P<field>\d+)@(?P<line>\d+)\s*$",
    re.IGNORECASE,
)
_RELAP_CARD_LINE_RE = re.compile(
    r"^\s*(?:\d{1,5}\s+)?\d{3,8}(?=$|\s|,|['\"A-Za-z])"
)
_RELAP_EXTENSIONS = {".i", ".inp", ".indta", ".relap", ".r5"}
_TEXT_EXTENSIONS = {".txt", ".dat", ".deck", ".input"}


def normalize_input_format(value: str | None) -> str | None:
    """Normalize an explicit format or software profile name."""
    text = str(value or "").strip().lower()
    if not text:
        return None
    normalized = _FORMAT_ALIASES.get(text)
    if normalized is None:
        raise ValueError(
            f"未知输入卡格式: {value}；可用格式为 relap5、positional_text"
        )
    return normalized


def _physical_lines(text: str) -> list[tuple[int, str, int, int]]:
    """Return physical lines, including the empty line after a final newline."""
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


def _positional_score(text: str) -> int:
    score = 0
    for _, raw, _, _ in _physical_lines(text):
        if not raw.strip() or len(raw) <= POSITIONAL_TEXT_PROFILE.description_column:
            continue
        left = raw[:POSITIONAL_TEXT_PROFILE.description_column]
        right = raw[POSITIONAL_TEXT_PROFILE.description_column:].strip()
        if right and _token_spans(left):
            score += 1
    return score


def detect_input_format(
    text: str,
    *,
    filename: str = "",
    requested_format: str | None = None,
) -> str:
    """Detect the input format, with an explicit profile taking precedence."""
    explicit = normalize_input_format(requested_format)
    if explicit:
        return explicit

    lines = [
        raw for _, raw, _, _ in _physical_lines(text) if raw.strip()
    ]
    nonempty = len(lines)
    if not nonempty:
        return "relap5"

    relap_markers = bool(re.search(r"(?im)^\s*\.\s*relap\s+end\s+card\b", text))
    relap_score = sum(bool(_RELAP_CARD_LINE_RE.match(line)) for line in lines)
    positional_score = _positional_score(text)
    suffix = Path(filename).suffix.lower() if filename else ""

    if relap_markers:
        return "relap5"
    if positional_score >= max(1, math.ceil(nonempty * 0.6)):
        if relap_score == 0 or positional_score > relap_score:
            return "positional_text"
    if suffix in _RELAP_EXTENSIONS and positional_score < nonempty:
        return "relap5"
    if suffix in _TEXT_EXTENSIONS and positional_score > 0:
        return "positional_text"
    if relap_score > 0:
        return "relap5"
    return "positional_text" if positional_score > 0 else "relap5"


def _profile_summary(result: dict[str, Any], input_format: str) -> dict[str, Any]:
    summary = result.setdefault("summary", {})
    profile = FORMAT_PROFILES[input_format]
    summary["profile"] = input_format
    summary["profile_label"] = profile["label"]
    summary["target_format"] = (
        "L{行号}:{字段序号}@{行号}"
        if input_format == POSITIONAL_TEXT_PROFILE.key
        else "卡号:关键字:字段序号@源行号"
    )
    return result


def _positional_entry(
    *,
    line_no: int,
    raw: str,
    line_start: int,
    kind: str,
    fields: list[str] | None = None,
    spans: list[list[int]] | None = None,
    description: str = "",
    description_span: list[int] | None = None,
) -> dict[str, Any]:
    fields = fields or []
    spans = spans or []
    record_id = f"L{line_no}"
    targets = [
        {
            "field": index + 1,
            "value": value,
            "target": f"{record_id}:{index + 1}@{line_no}",
            "source_line_no": line_no,
        }
        for index, value in enumerate(fields)
    ]
    category = POSITIONAL_TEXT_PROFILE.category if kind == "record" else ""
    return {
        "line_no": line_no,
        "source_line_no": line_no,
        "raw": raw,
        "kind": kind,
        "card_id": "",
        "card": "",
        "record_id": record_id,
        "category": category,
        "keyword": description,
        "target_keyword": "-",
        "title": description,
        "fields": fields,
        "tokens": fields,
        "field_spans": spans,
        "field_line_nos": [line_no] * len(fields),
        "field_start": spans[0][0] if spans else line_start + len(raw),
        "field_end": spans[-1][1] if spans else line_start + len(raw),
        "description": description,
        "description_span": description_span or [],
        "comment": "",
        "format": "positional-text",
        "external_line": None,
        "continuation": False,
        "parent_line_no": None,
        "effective": kind == "record",
        "overridden": False,
        "deleted": False,
        "deletion": False,
        "field_targets": targets,
        "target": targets[0]["target"] if targets else "",
    }


def _parse_positional_text(
    text: str,
    *,
    encoding: str,
    filename: str,
    path: str = "",
) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    categories: dict[str, int] = {}
    numeric_fields = 0
    record_fields = 0
    record_count = 0
    unparsed = 0
    preview: list[str] = []

    for line_no, raw, line_start, _ in _physical_lines(text):
        if not raw.strip():
            entries.append(_positional_entry(
                line_no=line_no, raw=raw, line_start=line_start, kind="blank"
            ))
            continue
        if len(preview) < 8:
            preview.append(raw)

        description_start = min(
            POSITIONAL_TEXT_PROFILE.description_column, len(raw)
        )
        value_text = raw[:description_start]
        description_raw = raw[description_start:]
        token_info = _token_spans(value_text)
        fields = [token for token, _, _ in token_info]
        spans = [
            [line_start + start, line_start + end]
            for _, start, end in token_info
        ]
        description = description_raw.strip()
        description_span = (
            [line_start + description_start, line_start + len(raw)]
            if description
            else []
        )
        if not fields:
            entries.append(_positional_entry(
                line_no=line_no,
                raw=raw,
                line_start=line_start,
                kind="unparsed",
                description=description,
                description_span=description_span,
            ))
            unparsed += 1
            continue

        entry = _positional_entry(
            line_no=line_no,
            raw=raw,
            line_start=line_start,
            kind="record",
            fields=fields,
            spans=spans,
            description=description,
            description_span=description_span,
        )
        entries.append(entry)
        record_count += 1
        record_fields += len(fields)
        numeric_fields += sum(_is_number(value) for value in fields)
        categories[POSITIONAL_TEXT_PROFILE.category] = (
            categories.get(POSITIONAL_TEXT_PROFILE.category, 0) + 1
        )

    blank_lines = sum(entry["kind"] == "blank" for entry in entries)
    warnings: list[str] = []
    if record_count == 0:
        warnings.append("未识别到位置型参数记录，请检查值列与说明列布局。")
    if unparsed:
        warnings.append(f"有 {unparsed} 行非空内容未识别为参数记录。")
    if "混合" in _newline_style(text):
        warnings.append("文件包含混合换行符")

    summary = {
        "filename": filename,
        "path": path,
        "encoding": encoding,
        "newline": _newline_style(text),
        "format": "positional-text",
        "profile": POSITIONAL_TEXT_PROFILE.key,
        "profile_label": POSITIONAL_TEXT_PROFILE.label,
        "target_format": "L{行号}:{字段序号}@{行号}",
        "description_column": POSITIONAL_TEXT_PROFILE.description_column,
        "total_lines": len(entries),
        "nonempty_lines": record_count + unparsed,
        "blank_lines": blank_lines,
        "total_cards": 0,
        "unique_cards": 0,
        "effective_cards": 0,
        "total_records": record_count,
        "effective_records": record_count,
        "record_fields": record_fields,
        "numeric_fields": numeric_fields,
        "unparsed_lines": unparsed,
        "comment_lines": 0,
        "continuation_lines": 0,
        "ignored_columns": 0,
        "has_end_card": False,
        "has_time_control": False,
        "has_hydrodynamic": False,
        "has_heat_structure": False,
        "categories": categories,
        "kinds": {
            "record": record_count,
            "blank": blank_lines,
            **({"unparsed": unparsed} if unparsed else {}),
        },
        "title": "",
        "preview": preview,
        "warnings": warnings,
    }
    return {"summary": summary, "entries": entries}


def parse_input_text(
    text: str,
    *,
    filename: str = "pasted.txt",
    encoding: str = "文本",
    path: str = "",
    input_format: str | None = None,
    profile: str | None = None,
) -> dict[str, Any]:
    """Parse pasted text using an explicit or automatically detected profile."""
    if text.startswith("\ufeff"):
        text = text.lstrip("\ufeff")
        encoding = "UTF-8 BOM"
    detected = detect_input_format(
        text, filename=filename, requested_format=input_format or profile
    )
    if detected == "relap5":
        result = parse_relap_input_text(
            text, filename=filename, encoding=encoding, path=path
        )
        return _profile_summary(result, "relap5")
    return _parse_positional_text(
        text, encoding=encoding, filename=filename, path=path
    )


def parse_input_data(
    data: bytes,
    *,
    filename: str = "pasted.txt",
    path: str = "",
    input_format: str | None = None,
    profile: str | None = None,
) -> dict[str, Any]:
    """Decode and parse input-deck bytes."""
    try:
        text, encoding = _decode_bytes(data)
    except UnicodeError as exc:
        return {"error": f"解码输入卡失败: {exc}"}
    return parse_input_text(
        text,
        filename=filename,
        encoding=encoding,
        path=path,
        input_format=input_format,
        profile=profile,
    )


def parse_input_file(
    path: str | Path,
    *,
    display_path: str | None = None,
    input_format: str | None = None,
    profile: str | None = None,
) -> dict[str, Any]:
    """Parse an input deck while preserving its detected encoding."""
    p = Path(path)
    if not p.is_file():
        return {"error": f"输入卡不存在: {path}"}
    try:
        data = p.read_bytes()
    except OSError as exc:
        return {"error": f"读取输入卡失败: {exc}"}
    return parse_input_data(
        data,
        filename=p.name,
        path=display_path or str(p),
        input_format=input_format,
        profile=profile,
    )


def _search_terms(query: str) -> list[str]:
    return [part.lower() for part in str(query or "").strip().split() if part]


def _entry_haystack(entry: dict[str, Any]) -> str:
    return " ".join([
        str(entry.get("line_no", "")),
        str(entry.get("record_id") or ""),
        str(entry.get("card_id") or ""),
        str(entry.get("keyword") or ""),
        str(entry.get("description") or ""),
        str(entry.get("raw") or ""),
        *(entry.get("fields") or []),
    ]).lower()


def _entry_matches_query(
    entry: dict[str, Any], terms: list[str], input_format: str
) -> bool:
    haystack = _entry_haystack(entry)
    record_id = str(entry.get("record_id") or "").lower()
    targets = {
        str(item.get("target") or "").lower()
        for item in entry.get("field_targets") or []
    }
    for term in terms:
        exact_line = re.fullmatch(r"l(\d+)", term)
        if exact_line:
            if int(entry.get("line_no") or 0) != int(exact_line.group(1)):
                return False
            continue
        if (
            input_format == POSITIONAL_TEXT_PROFILE.key
            and _POSITIONAL_TARGET_RE.fullmatch(term)
        ):
            if term not in targets:
                return False
            continue
        if term not in haystack:
            return False
    return True


def _exact_positional_field(terms: list[str]) -> int | None:
    for term in terms:
        match = _POSITIONAL_TARGET_RE.fullmatch(term)
        if match:
            return int(match.group("field")) - 1
    return None


def _search_parsed(
    parsed: dict[str, Any],
    query: str,
    *,
    limit: int,
    search_all: bool,
    display_path: str = "",
) -> list[dict[str, Any]]:
    terms = _search_terms(query)
    max_results = max(1, int(limit or 200))
    input_format = str(parsed.get("summary", {}).get("profile") or "relap5")
    exact_field = (
        _exact_positional_field(terms)
        if input_format == POSITIONAL_TEXT_PROFILE.key
        else None
    )
    results: list[dict[str, Any]] = []

    for entry in parsed.get("entries", []):
        kind = entry.get("kind")
        if kind not in {"card", "record"} or not entry.get("effective"):
            continue
        fields = entry.get("fields") or []
        if not fields:
            continue
        if terms and not _entry_matches_query(entry, terms, input_format):
            continue

        all_fields = search_all or input_format == POSITIONAL_TEXT_PROFILE.key
        indexes = list(range(len(fields))) if all_fields else [0]
        if exact_field is not None:
            indexes = [exact_field] if exact_field < len(fields) else []
        spans = entry.get("field_spans") or []
        field_lines = entry.get("field_line_nos") or []
        for index in indexes:
            if input_format == POSITIONAL_TEXT_PROFILE.key:
                target = f"{entry.get('record_id') or 'L' + str(entry['line_no'])}:{index + 1}@{entry['line_no']}"
            else:
                target_keyword = (
                    entry.get("target_keyword") or entry.get("keyword") or "-"
                )
                target = (
                    f"{entry.get('card_id')}:{target_keyword}:{index + 1}"
                    f"@{entry.get('line_no')}"
                )
            results.append({
                "target": target,
                "profile": input_format,
                "line_no": entry.get("line_no"),
                "field_line_no": (
                    field_lines[index] if index < len(field_lines)
                    else entry.get("line_no")
                ),
                "record_id": entry.get("record_id") or "",
                "card_id": entry.get("card_id") or "",
                "keyword": entry.get("keyword") or "",
                "target_keyword": entry.get("target_keyword") or "-",
                "description": entry.get("description") or "",
                "field": index + 1,
                "value": fields[index],
                "raw": entry.get("raw", ""),
                "format": entry.get("format", ""),
                "category": entry.get("category", ""),
                "span": spans[index] if index < len(spans) else None,
                "path": display_path,
                "field_count": len(fields),
                "fields": fields,
            })
            if len(results) >= max_results:
                return results
    return results


def search_input_text(
    text: str,
    query: str = "",
    limit: int = 200,
    *,
    filename: str = "pasted.txt",
    search_all: bool = True,
    input_format: str | None = None,
    profile: str | None = None,
) -> list[dict[str, Any]]:
    """Search parsed card or positional fields and return stable targets."""
    parsed = parse_input_text(
        text,
        filename=filename,
        input_format=input_format,
        profile=profile,
    )
    if parsed.get("error"):
        return []
    return _search_parsed(
        parsed, query, limit=limit, search_all=search_all
    )


def search_input_file(
    path: str | Path,
    query: str = "",
    limit: int = 200,
    *,
    search_all: bool = True,
    input_format: str | None = None,
    profile: str | None = None,
    display_path: str | None = None,
) -> dict[str, Any]:
    """Search an input deck and include its format summary."""
    p = Path(path)
    parsed = parse_input_file(
        p,
        display_path=display_path,
        input_format=input_format,
        profile=profile,
    )
    if parsed.get("error"):
        return {"ok": False, "error": parsed["error"]}
    max_results = max(1, min(int(limit or 200), 1000))
    results = _search_parsed(
        parsed,
        query,
        limit=max_results,
        search_all=search_all,
        display_path=display_path or str(p),
    )
    return {
        "ok": True,
        "path": parsed.get("summary", {}).get("path") or display_path or str(p),
        "filename": p.name,
        "count": len(results),
        "results": results,
        "summary": parsed.get("summary") or {},
    }


def _parse_positional_target(target: str) -> tuple[int, int, int]:
    match = _POSITIONAL_TARGET_RE.match(target or "")
    if not match:
        raise ValueError(
            "位置型变量定位格式无效，应为 L行号:字段序号@行号，例如 L11:1@11"
        )
    return (
        int(match.group("line")),
        int(match.group("field")),
        int(match.group("source_line")),
    )


def _locate_positional(
    parsed: dict[str, Any], line_no: int, field: int, source_line: int
) -> dict[str, Any]:
    if line_no != source_line:
        raise ValueError(
            f"位置型定位的行号不一致：记录 L{line_no}，源行 {source_line}"
        )
    matches = [
        entry for entry in parsed.get("entries", [])
        if entry.get("kind") == "record"
        and int(entry.get("line_no") or 0) == line_no
        and entry.get("effective")
    ]
    if not matches:
        raise ValueError(f"未找到位置型参数记录：第 {line_no} 行")
    entry = matches[0]
    fields = entry.get("fields") or []
    spans = entry.get("field_spans") or []
    if field < 1 or field > len(fields) or field > len(spans):
        raise ValueError(
            f"字段序号越界：第 {line_no} 行只有 {len(fields)} 个字段"
        )
    return {
        "entry": entry,
        "field": field,
        "value": fields[field - 1],
        "span": spans[field - 1],
        "field_line_no": line_no,
    }


def _validate_positional_replacement(
    text: str, located: dict[str, Any], value: str
) -> tuple[str, int]:
    if not isinstance(value, str):
        raise ValueError("新值必须是字符串")
    if not value:
        raise ValueError("新值不能为空")
    if any(ch in value for ch in "\r\n") or any(ch.isspace() for ch in value):
        raise ValueError("新值必须是单个字段，不能包含空白或换行")
    if _is_number(str(located["value"])) and not _is_number(value):
        raise ValueError(f"目标字段是数值域，新值必须为数值：{value}")

    entry = located["entry"]
    index = int(located["field"]) - 1
    start, end = located["span"]
    fields = entry.get("fields") or []
    spans = entry.get("field_spans") or []
    if index + 1 < len(spans):
        slot_end = int(spans[index + 1][0])
    else:
        slot_end = (
            entry.get("description_span")[0]
            if entry.get("description_span")
            else int(end)
        )
    gap = text[end:slot_end]
    old_width = end - start
    new_width = len(value)
    if new_width <= old_width:
        replacement = value + (" " * (old_width - new_width))
        replace_end = end
    else:
        needed = new_width - old_width
        if not gap or not gap.isspace() or len(gap) < needed:
            raise ValueError(
                f"新值过长，无法在不移动说明列的情况下写入第 {entry['line_no']} 行"
            )
        replacement = value
        replace_end = end + needed
    return replacement, replace_end


def _verify_positional_candidate(
    before: dict[str, Any], after: dict[str, Any], new_value: str
) -> None:
    before_entry = before.get("entry") or {}
    after_entry = after.get("entry") or {}
    old_fields = before_entry.get("fields") or []
    new_fields = after_entry.get("fields") or []
    if len(old_fields) != len(new_fields):
        raise ValueError(
            f"修改后字段数变化：原 {len(old_fields)} 个，新 {len(new_fields)} 个"
        )
    field = int(before["field"]) - 1
    if new_fields[field] != new_value:
        raise ValueError(
            f"修改后变量回读不一致：期望 {new_value}，实际 {new_fields[field]}"
        )
    if (
        old_fields[:field] + old_fields[field + 1:]
        != new_fields[:field] + new_fields[field + 1:]
    ):
        raise ValueError("修改后同一行的其他字段发生变化")
    if before_entry.get("description", "") != after_entry.get("description", ""):
        raise ValueError("修改后参数说明发生变化")
    if len(before_entry.get("raw", "")) != len(after_entry.get("raw", "")):
        raise ValueError("修改后行宽发生变化")


def modify_positional_input_file(
    path: str | Path,
    target: str,
    value: str,
    *,
    expected_value: str | None = None,
    apply: bool = True,
) -> dict[str, Any]:
    """Safely replace one field in a positional-text input deck."""
    p = Path(path)
    if not p.is_file():
        return {"ok": False, "error": f"输入卡不存在: {path}", "applied": False}
    try:
        line_no, field, source_line = _parse_positional_target(target)
        data = p.read_bytes()
        text, encoding_label = _decode_bytes(data)
        parsed = _parse_positional_text(
            text,
            encoding=encoding_label,
            filename=p.name,
            path=str(p),
        )
        located = _locate_positional(parsed, line_no, field, source_line)
        old_value = located["value"]
        if expected_value is not None and str(old_value) != str(expected_value):
            return {
                "ok": False,
                "applied": False,
                "error": f"变量当前值已变化：期望 {expected_value}，实际 {old_value}",
                "old_value": old_value,
                "target": target,
            }

        replacement, replace_end = _validate_positional_replacement(
            text, located, str(value)
        )
        start, end = located["span"]
        new_text = text[:start] + replacement + text[replace_end:]
        new_parsed = _parse_positional_text(
            new_text,
            encoding=encoding_label,
            filename=p.name,
            path=str(p),
        )
        new_located = _locate_positional(
            new_parsed, line_no, field, source_line
        )
        _verify_positional_candidate(located, new_located, str(value))
        if len(new_text.splitlines()) != len(text.splitlines()):
            raise ValueError("修改后物理行数发生变化")
        if _newline_style(new_text) != _newline_style(text):
            raise ValueError("修改后换行风格发生变化")

        candidate_data = _encoded_bytes(new_text, encoding_label)
        decoded_check, _ = _decode_bytes(candidate_data)
        if decoded_check != new_text:
            raise ValueError("修改结果无法按原编码无损回读")
        preview = _changed_preview(
            text, new_text, start, replace_end, start, start + len(replacement)
        )
        diff = _unified_text_diff(text, new_text, p)
        entry = located["entry"]
        common = {
            "ok": True,
            "path": str(p),
            "profile": POSITIONAL_TEXT_PROFILE.key,
            "format": "positional-text",
            "target": target,
            "record_id": entry.get("record_id"),
            "old_value": old_value,
            "new_value": str(value),
            "encoded_value": replacement,
            "line_no": line_no,
            "field_line_no": line_no,
            "card_id": "",
            "keyword": entry.get("keyword") or "",
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
        written_parsed = _parse_positional_text(
            written_text,
            encoding=written_encoding,
            filename=p.name,
            path=str(p),
        )
        written_located = _locate_positional(
            written_parsed, line_no, field, source_line
        )
        _verify_positional_candidate(located, written_located, str(value))
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


def _detect_file_format(
    path: Path, requested_format: str | None = None
) -> tuple[str, bytes, str]:
    data = path.read_bytes()
    text, _ = _decode_bytes(data)
    detected = detect_input_format(
        text,
        filename=path.name,
        requested_format=requested_format,
    )
    return detected, data, text


def modify_input_file(
    path: str | Path,
    target: str,
    value: str,
    *,
    expected_value: str | None = None,
    apply: bool = True,
    input_format: str | None = None,
    profile: str | None = None,
) -> dict[str, Any]:
    """Modify a RELAP5 or positional-text field through one dispatcher."""
    p = Path(path)
    if not p.is_file():
        return {"ok": False, "error": f"输入卡不存在: {path}", "applied": False}
    try:
        detected, _, _ = _detect_file_format(p, input_format or profile)
    except (OSError, UnicodeError, ValueError) as exc:
        return {
            "ok": False,
            "applied": False,
            "error": str(exc),
            "target": target,
        }

    if detected == POSITIONAL_TEXT_PROFILE.key:
        result = modify_positional_input_file(
            p,
            target,
            value,
            expected_value=expected_value,
            apply=apply,
        )
    elif _RELAP_TARGET_RE.match(str(target or "")):
        result = modify_relap_input_file(
            p,
            target,
            value,
            expected_value=expected_value,
            apply=apply,
        )
    else:
        result = apply_relap_input_edit(
            p,
            target,
            value,
            expected_value=expected_value,
            apply=apply,
        )
    result.setdefault("profile", detected)
    result.setdefault(
        "format",
        "positional-text" if detected == POSITIONAL_TEXT_PROFILE.key else "relap5",
    )
    return result


def apply_input_edit(
    path: str | Path,
    target: str,
    value: str,
    *,
    expected_value: str | None = None,
    apply: bool = True,
    input_format: str | None = None,
    profile: str | None = None,
) -> dict[str, Any]:
    """Compatibility-friendly name for the generic input editor."""
    return modify_input_file(
        path,
        target,
        value,
        expected_value=expected_value,
        apply=apply,
        input_format=input_format,
        profile=profile,
    )


__all__ = [
    "FORMAT_PROFILES",
    "POSITIONAL_TEXT_PROFILE",
    "apply_input_edit",
    "detect_input_format",
    "modify_input_file",
    "modify_positional_input_file",
    "normalize_input_format",
    "parse_input_data",
    "parse_input_file",
    "parse_input_text",
    "search_input_file",
    "search_input_text",
]
