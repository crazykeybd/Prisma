"""RELAP5 input-deck reader and safe field editor.

The parser accepts classic fixed-width decks, RELAP5-3D eight-digit card
numbers, small edit cards, free-format cards, decks with an external listing
line number, and pasted text. Every non-empty source line is retained in the
result so unrecognized formats remain visible instead of being discarded.

For editing, use ``search_relap_input`` to obtain a stable target of the form
``CARD:KEYWORD:FIELD@LINE`` and ``modify_relap_input_file`` to replace that
field while preserving the original text, whitespace, encoding and newlines.
"""

from __future__ import annotations

import difflib
import os
import re
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any


_CARD_RE = re.compile(r"^(?P<card>\d{3,8})(?P<rest>.*)$")
_EXTERNAL_LINE_RE = re.compile(
    r"^\s*(?P<line>\d{1,5})(?!\d)(?:\s+|[:|]\s*)"
    r"(?P<body>(?:\d{3,8})(?=$|[\s,]|[A-Za-z'\"])[^\r\n]*"
    r"|[./][^\r\n]*)$"
)
_COMMENT_MARKERS = ("*", "$")
_LINE_COMMENT_MARKERS = ("*", "$")
_MAX_RECORD_WIDTH = 80
_RELAP_NUMBER_RE = re.compile(
    r"^[+-]?(?:"
    r"(?:\d+(?:\.\d*)?|\.\d+)(?:[EeDd][+-]?\d+|[+-]\d+)?"
    r"|\d+[+-]\d+"
    r")$"
)
_TARGET_RE = re.compile(
    r"^\s*(?P<card>\d{3,8}):(?P<keyword>.+?):"
    r"(?P<field>\d+)@(?P<line>\d+)\s*$",
    re.IGNORECASE,
)


def _decode_bytes(data: bytes) -> tuple[str, str]:
    if data.startswith(b"\xef\xbb\xbf"):
        return data[3:].decode("utf-8"), "UTF-8 BOM"
    if data.startswith((b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff")):
        label = "UTF-32 LE BOM" if data.startswith(b"\xff\xfe") else "UTF-32 BE BOM"
        return data.decode("utf-32"), label
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        label = "UTF-16 LE BOM" if data.startswith(b"\xff\xfe") else "UTF-16 BE BOM"
        return data.decode("utf-16"), label

    if data and data.count(0) / len(data) > 0.15:
        # Detect BOM-less UTF-32/UTF-16 byte order from zero-byte placement.
        # RELAP5 decks are predominantly ASCII, so this stays deterministic
        # without guessing the wrong endian and corrupting later writes.
        groups32 = [data[index:index + 4] for index in range(0, len(data) - 3, 4)]
        if len(groups32) >= 2:
            le32 = sum(chunk[1:] == b"\x00\x00\x00" for chunk in groups32)
            be32 = sum(chunk[:3] == b"\x00\x00\x00" for chunk in groups32)
            if max(le32, be32) / len(groups32) >= 0.75:
                encoding, label = (
                    ("utf-32-le", "UTF-32 LE") if le32 >= be32 else ("utf-32-be", "UTF-32 BE")
                )
                try:
                    decoded = data.decode(encoding)
                    if decoded.strip():
                        return decoded, label
                except UnicodeDecodeError:
                    pass

        even_zeros = sum(byte == 0 for byte in data[0::2])
        odd_zeros = sum(byte == 0 for byte in data[1::2])
        candidates = (
            (("utf-16-le", "UTF-16 LE"), ("utf-16-be", "UTF-16 BE"))
            if odd_zeros >= even_zeros
            else (("utf-16-be", "UTF-16 BE"), ("utf-16-le", "UTF-16 LE"))
        )
        for encoding, label in candidates:
            try:
                decoded = data.decode(encoding)
                if decoded.strip():
                    return decoded, label
            except UnicodeDecodeError:
                pass

    if all(byte < 128 for byte in data):
        return data.decode("ascii"), "ASCII"
    for encoding, label in (("utf-8", "UTF-8"), ("gb18030", "GB18030")):
        try:
            return data.decode(encoding), label
        except UnicodeDecodeError:
            pass
    try:
        return data.decode("cp1252"), "Windows-1252"
    except UnicodeDecodeError:
        return data.decode("latin-1"), "Latin-1"


def _python_encoding(label: str) -> str:
    return {
        "ASCII": "ascii",
        "UTF-8": "utf-8",
        "UTF-8 BOM": "utf-8",
        "GB18030": "gb18030",
        "Windows-1252": "cp1252",
        "Latin-1": "latin-1",
        "UTF-16": "utf-16",
        "UTF-16 LE": "utf-16-le",
        "UTF-16 BE": "utf-16-be",
        "UTF-16 LE BOM": "utf-16-le",
        "UTF-16 BE BOM": "utf-16-be",
        "UTF-32": "utf-32",
        "UTF-32 LE": "utf-32-le",
        "UTF-32 BE": "utf-32-be",
        "UTF-32 LE BOM": "utf-32-le",
        "UTF-32 BE BOM": "utf-32-be",
    }.get(label, label)


def _newline_style(text: str) -> str:
    crlf = text.count("\r\n")
    lf = text.count("\n") - crlf
    cr = text.count("\r") - crlf
    styles = []
    if crlf:
        styles.append("CRLF")
    if lf:
        styles.append("LF")
    if cr:
        styles.append("CR")
    return styles[0] if len(styles) == 1 else "混合 (" + " / ".join(styles) + ")" if styles else "无换行"


def _find_comment(text: str) -> tuple[str, str]:
    """Split at an unquoted RELAP5 comment marker outside quoted names.

    RELAP5 starts a trailing comment with ``*`` or ``$``. A marker is only a
    comment when it is outside a quoted field and is either the first
    non-space character or follows a field separator. This deliberately does
    not treat ``!``, ``#`` or ``//`` as comments: those characters can occur in
    file names, paths or ordinary alphanumeric fields.
    """
    quote = ""
    i = 0
    while i < len(text):
        ch = text[i]
        if quote:
            if ch == quote:
                # RELAP5 represents a doubled delimiter as one literal
                # character inside a quoted field.
                if i + 1 < len(text) and text[i + 1] == quote:
                    i += 2
                    continue
                quote = ""
            i += 1
            continue
        if ch in ("'", '"'):
            quote = ch
            i += 1
            continue
        if ch in _LINE_COMMENT_MARKERS:
            if i == 0 or text[i - 1].isspace() or text[i - 1] == ",":
                return text[:i].rstrip(), text[i:].strip()
        i += 1
    return text.rstrip(), ""


def _is_number(value: str) -> bool:
    """Return whether a token is a RELAP5/Fortran numeric field.

    Besides conventional exponents (``1.0e-6``), RELAP5 accepts the
    Fortran-style implicit exponent forms ``1.0-6`` and ``0.1245+2``.
    """
    return bool(_RELAP_NUMBER_RE.fullmatch(str(value or "").strip()))


def _token_spans(text: str) -> list[tuple[str, int, int]]:
    """Return RELAP5 fields with exact source spans.

    Fields are separated by whitespace or a comma. Quoted fields are returned
    without their delimiters, matching RELAP5's field value, but their span
    includes the delimiters so a replacement can preserve the field type. A
    doubled quote inside a quoted field is decoded as one quote character.
    """
    tokens: list[tuple[str, int, int]] = []
    current: list[str] = []
    start = -1
    quote = ""
    i = 0
    while i < len(text):
        ch = text[i]
        if quote:
            if ch == quote:
                if i + 1 < len(text) and text[i + 1] == quote:
                    current.append(quote)
                    i += 2
                    continue
                quote = ""
                i += 1
                continue
            current.append(ch)
            i += 1
            continue
        if ch in ("'", '"'):
            if start < 0:
                start = i
            quote = ch

            i += 1
            continue
        if ch.isspace() or ch == ",":
            if start >= 0:
                tokens.append(("".join(current), start, i))
                current = []
                start = -1
            i += 1
            continue
        if start < 0:
            start = i
        current.append(ch)
        i += 1
    if start >= 0:
        tokens.append(("".join(current), start, len(text)))
    return tokens

def _target_keyword(card_id: str, keyword: str, fields: list[str] | None = None) -> str:
    """Return a stable card identifier for edit targets.

    Some RELAP5 cards (for example ``1000101`` in ``mini.i``) have no textual
    keyword: every token is a numeric value.  Using the first value as the
    target keyword makes the target change as soon as that value is edited.
    Prefer a textual keyword and otherwise use ``-``; field numbers and source
    line numbers still identify the exact value.
    """
    value = str(keyword or "").strip()
    first = str((fields or [""])[0] or "").strip()
    if value and not _is_number(value):
        return value
    if first and not _is_number(first):
        return first
    return "-"


def _classify(card_id: str, keyword: str) -> str:
    """Classify a card using RELAP5's documented card families.

    Numeric prefixes are more reliable than the first text field for many
    cards.  For example, card 20500000 is a control-system card even though
    its first field can be a component-like identifier.
    """
    key = keyword.lower().strip()
    card = str(card_id or "")
    if len(card) == 3 and card.isdigit():
        if key in {"p", "tempf", "tempg", "voidf", "voidg", "mflowj", "pmpvel", "pmphead"}:
            return "小编辑 / 变量"
        if key.startswith("cntrlvar") or key in {"sum", "mult", "div", "function", "tripunit"}:
            return "控制系统"
        return "小编辑 / 其他"

    if card.startswith("0000"):
        if card.startswith("000020"):
            return "时间步长控制"
        if card[:5] in {"00004", "00005", "00006"}:
            return "TRIP / 保护"
        if card[:5] in {"00008", "00009"} or "cntrlvar" in key:
            return "控制系统"
        return "控制 / 问题设置"

    # 1CCCGXNN is the eight-digit heat-structure family. Test it before
    # hydrodynamic CCCXXNN cards because both can begin with "100".
    if len(card) == 8 and card[0] == "1":
        return "热构件"
    if (
        len(card) in (7, 8)
        and card[:3].isdigit()
        and 100 <= int(card[:3]) <= 147
    ):
        return "水力组件"

    if card.startswith("205"):
        return "控制系统"
    if card.startswith("206"):
        return "TRIP / 保护"
    if card.startswith("203") or card.startswith("204"):
        return "绘图"
    if card.startswith("201"):
        return "热物性"
    if card.startswith("202"):
        return "通用表格"
    if card.startswith("300") and len(card) >= 7:
        return "点堆动力学"
    # Keep STRIP handling after the heat-structure check so a CCC segment
    # beginning with 1001 is not mistaken for a STRIP request.
    if len(card) == 4 and card.isdigit() and 1001 <= int(card) <= 1999:
        return "STRIP"
    if card.startswith("6") and len(card) >= 7:
        return "辐射"
    if len(card) >= 4 and card[:3].isdigit() and 100 <= int(card[:3]) <= 147:
        return "控制 / 问题设置"
    if len(card) >= 3 and card[:3].isdigit() and 200 <= int(card[:3]) <= 299:
        return "时间步长控制"

    component_words = {
        "vol": "水力组件", "volume": "水力组件", "pipe": "水力组件",
        "snglvol": "水力组件", "tmdpvol": "水力组件", "branch": "水力组件",
        "pump": "水力组件", "turbine": "水力组件", "valve": "连接件",
        "sngljun": "连接件", "tmdpjun": "连接件", "junction": "连接件",
        "heat": "热构件", "htstr": "热构件", "heatstr": "热构件",
    }
    for word, category in component_words.items():
        if key == word or key.startswith(word):
            return category

    if len(card) >= 7 and card[:3].isdigit() and 300 <= int(card[:3]) <= 399:
        return "热构件"
    if len(card) >= 7:
        return "水力组件"
    return "其他卡"

def _card_format(card_id: str, had_separator: bool, external_line: bool) -> str:
    if external_line or not had_separator or len(card_id) >= 7:
        return "fixed-width"
    return "free-format"


def _entry(
    line_no: int,
    raw: str,
    *,
    kind: str,
    card_id: str | None = None,
    category: str = "",
    keyword: str = "",
    target_keyword: str = "",
    fields: list[str] | None = None,
    comment: str = "",
    line_format: str = "",
    external_line: str | None = None,
    continuation: bool = False,
    field_spans: list[list[int]] | None = None,
    field_start: int = 0,
    field_end: int = 0,
    source_line_no: int | None = None,
    field_line_nos: list[int] | None = None,
    parent_line_no: int | None = None,
) -> dict[str, Any]:
    fields = fields or []
    source_line_no = line_no if source_line_no is None else source_line_no
    return {
        "line_no": line_no,
        "source_line_no": source_line_no,
        "raw": raw,
        "kind": kind,
        "card_id": card_id,
        "card": card_id or "",
        "category": category,
        "keyword": keyword,
        "target_keyword": target_keyword or keyword,
        "title": keyword,
        "fields": fields,
        "tokens": fields,
        "field_spans": field_spans or [],
        "field_line_nos": field_line_nos or [],
        "field_start": field_start,
        "field_end": field_end,
        "comment": comment,
        "format": line_format,
        "external_line": external_line,
        "continuation": continuation,
        "parent_line_no": parent_line_no,
        "effective": False,
        "overridden": False,
        "deleted": False,
        "deletion": False,
        "field_targets": [],
        "target": "",
    }


def _card_key(card_id: str) -> int:
    """Normalize leading zeroes so 100 and 0000100 are the same card."""
    try:
        return int(card_id)
    except (TypeError, ValueError):
        return -1


def _parse_data_fields(
    raw: str,
    field_offset: int,
    line_start: int,
    line_no: int,
) -> tuple[list[str], list[list[int]], list[int], str, int]:
    """Parse fields from one physical line and return absolute spans.

    The returned cursor is relative to ``raw`` and points to the field area.
    """
    # ``field_offset`` is a character offset into the logical record, while
    # ``line_start`` is an offset in the whole document string. They differ
    # only when the line is shortened to RELAP5's 80-column limit.
    rest = raw[field_offset:]
    rest_start = field_offset + len(rest) - len(rest.lstrip())
    data_text, comment = _find_comment(raw[rest_start:])
    token_info = _token_spans(data_text)
    fields = [token for token, _, _ in token_info]
    spans = [
        [line_start + rest_start + start, line_start + rest_start + end]
        for _, start, end in token_info
    ]
    return fields, spans, [line_no] * len(fields), comment, rest_start


def _parse_rows(text: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    formats: Counter[str] = Counter()
    kinds: Counter[str] = Counter()
    comments = 0
    card_ids: list[str] = []
    title = ""
    preview: list[str] = []
    last_card: dict[str, Any] | None = None
    last_card_id: str | None = None
    last_category = ""
    continuation_blocked = False
    ignored_columns = 0

    source_lines = text.splitlines(keepends=True)
    line_offsets: list[int] = []
    offset = 0
    for source_line in source_lines:
        line_offsets.append(offset)
        offset += len(source_line)

    for line_no, line_with_end in enumerate(source_lines, start=1):
        line_start = line_offsets[line_no - 1]
        split = line_with_end.splitlines()
        full_raw = split[0] if split else ""
        raw = full_raw[:_MAX_RECORD_WIDTH]
        if not raw.strip():
            continuation_blocked = True
            continue
        if len(preview) < 8:
            preview.append(full_raw)

        # RELAP5 reads 96-column records but ignores columns after 80.
        parse_raw = raw
        if len(full_raw) > _MAX_RECORD_WIDTH:
            ignored_columns += 1
        stripped = parse_raw.strip()

        if any(stripped.startswith(marker) for marker in _COMMENT_MARKERS):
            rows.append(_entry(line_no, raw, kind="comment", comment=stripped))
            kinds["comment"] += 1
            comments += 1
            continuation_blocked = True
            continue

        if stripped.startswith(("=", "/")):
            marker = stripped[0]
            if marker == "=":
                keyword = stripped[1:].strip()
                title = keyword
                rows.append(_entry(line_no, raw, kind="title", keyword=keyword))
                kinds["title"] += 1
            else:
                rows.append(_entry(
                    line_no, raw, kind="terminator", keyword=stripped[1:].strip()
                ))
                kinds["terminator"] += 1
            last_card = None
            last_card_id = None
            last_category = ""
            continuation_blocked = True
            continue

        if stripped.startswith("."):
            rows.append(_entry(
                line_no, raw, kind="terminator", keyword=stripped[1:].strip(),
                card_id=None,
            ))
            kinds["terminator"] += 1
            last_card = None
            last_card_id = None
            last_category = ""
            continuation_blocked = True
            continue

        body = stripped
        external_line = None
        external_match = _EXTERNAL_LINE_RE.match(parse_raw)
        if external_match:
            external_line = external_match.group("line")
            body = external_match.group("body").lstrip()

        # A continuation belongs only to the immediately preceding logical
        # card. Any blank/comment/title card between them invalidates it.
        if stripped.startswith("+"):
            plus_index = parse_raw.find("+")
            if external_match:
                plus_index = parse_raw.find("+", parse_raw.find(external_match.group("body")))
            if last_card is not None and not continuation_blocked:
                fields, spans, field_lines, comment, _ = _parse_data_fields(
                    parse_raw, plus_index + 1, line_start, line_no
                )
                last_card["fields"].extend(fields)
                last_card["tokens"] = last_card["fields"]
                last_card["field_spans"].extend(spans)
                last_card["field_line_nos"].extend(field_lines)
                if comment:
                    last_card.setdefault("continuation_comments", []).append(comment)
                    comments += 1
                rows.append(_entry(
                    line_no, raw, kind="continuation", card_id=last_card.get("card_id"),
                    category=last_card.get("category", ""), keyword=fields[0] if fields else "",
                    fields=fields, comment=comment, continuation=True,
                    external_line=external_line, field_spans=spans,
                    source_line_no=line_no, field_line_nos=field_lines,
                    parent_line_no=last_card.get("line_no"),
                ))
                kinds["continuation"] += 1
            else:
                reason = (
                    "续卡前存在空行、注释卡、题目卡或终止卡"
                    if continuation_blocked else "续卡前没有数据卡"
                )
                orphan_fields, orphan_spans, orphan_lines, orphan_comment, _ = (
                    _parse_data_fields(parse_raw, plus_index + 1, line_start, line_no)
                )
                rows.append(_entry(
                    line_no, raw, kind="unparsed", keyword=reason,
                    fields=orphan_fields, comment=orphan_comment,
                    external_line=external_line, field_spans=orphan_spans,
                    field_line_nos=orphan_lines,
                ))
                kinds["unparsed"] += 1
                if orphan_comment:
                    comments += 1
            continue

        data_text_for_unparsed, comment_for_unparsed = _find_comment(body)
        card_match = _CARD_RE.match(body)
        card_id = None
        if card_match:
            candidate = card_match.group("card")
            candidate_rest = card_match.group("rest")
            # Seven/eight-digit identifiers are unambiguous. Shorter card
            # numbers need a field boundary, including comma-separated decks.
            if (
                len(candidate) >= 7
                or not candidate_rest
                or candidate_rest[0].isspace()
                or candidate_rest[0] == ","
                or candidate_rest[0].isalpha()
                or candidate_rest[0] in ("'", '"')
            ):
                card_id = candidate

        if card_id:
            card_start = parse_raw.find(card_id)
            if external_match:
                card_start = parse_raw.find(card_id, parse_raw.find(external_match.group("body")))
            had_separator = bool(card_match.group("rest")[:1].isspace())
            fields, spans, field_lines, comment, rest_start = _parse_data_fields(
                parse_raw, card_start + len(card_id), line_start, line_no
            )
            keyword = fields[0] if fields else ""
            target_keyword = _target_keyword(card_id, keyword, fields)
            category = _classify(card_id, keyword)
            line_format = _card_format(card_id, had_separator, bool(external_line))
            field_end = spans[-1][1] if spans else line_start + rest_start
            entry = _entry(
                line_no, raw, kind="card", card_id=card_id, category=category,
                keyword=keyword, target_keyword=target_keyword, fields=fields,
                comment=comment, line_format=line_format, external_line=external_line,
                field_spans=spans, field_start=line_start + rest_start,
                field_end=field_end, source_line_no=line_no, field_line_nos=field_lines,
            )
            entry["deletion"] = not fields
            rows.append(entry)
            kinds["card"] += 1
            card_ids.append(card_id)
            formats[line_format] += 1
            last_card = entry
            last_card_id = card_id
            last_category = category
            continuation_blocked = False
            if comment:
                comments += 1
            continue

        token_fields = [token for token, _, _ in _token_spans(data_text_for_unparsed)]
        rows.append(_entry(
            line_no, raw, kind="unparsed", keyword=token_fields[0] if token_fields else "",
            fields=token_fields, comment=comment_for_unparsed,
            external_line=external_line,
        ))
        kinds["unparsed"] += 1
        continuation_blocked = True
        if comment_for_unparsed:
            comments += 1

    # Apply RELAP5 replacement semantics in source order. Physical card rows
    # remain in the result for auditing, but only the final effective card can
    # be searched or edited.
    active: dict[int, dict[str, Any]] = {}
    replaced: dict[int, list[dict[str, Any]]] = {}
    for entry in rows:
        if entry.get("kind") != "card" or not entry.get("card_id"):
            continue
        key = _card_key(entry["card_id"])
        previous = active.get(key)
        replaced.setdefault(key, []).append(entry)
        if entry.get("deletion"):
            if previous is not None:
                previous["effective"] = False
                previous["deleted"] = True
                previous["target"] = ""
                previous["field_targets"] = []
            entry["deleted"] = previous is not None
            entry["effective"] = False
            active.pop(key, None)
            continue
        if previous is not None:
            previous["effective"] = False
            previous["overridden"] = True
            previous["target"] = ""
            previous["field_targets"] = []
        entry["effective"] = True
        active[key] = entry

    duplicate_counts = {
        key: len(items) for key, items in replaced.items() if len(items) > 1
    }

    for entry in rows:
        card_id = entry.get("card_id")
        keyword = entry.get("target_keyword") or entry.get("keyword") or ""
        fields = entry.get("fields") or []
        spans = entry.get("field_spans") or []
        lines = entry.get("field_line_nos") or []
        if (
            entry.get("kind") == "card"
            and entry.get("effective")
            and card_id and keyword and len(fields) == len(spans) == len(lines)
        ):
            targets = [
                {
                    "field": index + 1,
                    "value": value,
                    "target": f"{card_id}:{keyword}:{index + 1}@{entry['line_no']}",
                    "source_line_no": lines[index],
                }
                for index, value in enumerate(fields)
            ]
            entry["field_targets"] = targets
            entry["target"] = targets[0]["target"] if targets else ""

    return rows, {
        "formats": formats,
        "kinds": kinds,
        "comments": comments,
        "card_ids": card_ids,
        "duplicate_counts": duplicate_counts,
        "title": title,
        "preview": preview,
        "ignored_columns": ignored_columns,
    }


def has_end_card(text: str) -> bool:
    return bool(re.search(r"(?im)^\s*\.\s*relap\s+end\s+card\b", text))


def _finish(text: str, *, encoding: str, filename: str, path: str = "") -> dict[str, Any]:
    rows, meta = _parse_rows(text)
    card_ids = meta["card_ids"]
    normalized_card_ids = [_card_key(card_id) for card_id in card_ids]
    normalized_counts = Counter(normalized_card_ids)
    duplicate_counts = meta["duplicate_counts"]
    duplicates = sorted(
        ((card_id, count) for card_id, count in duplicate_counts.items()),
        key=lambda item: item[0],
    )
    warnings: list[str] = []
    if not card_ids:
        warnings.append("未识别到 RELAP5 卡号，请检查文件内容或粘贴的数据。")
    for card, count in duplicates[:5]:
        warnings.append(f"卡号 {card} 出现 {count} 次，仅最后一次生效")
    if len(duplicates) > 5:
        warnings.append(f"另有 {len(duplicates) - 5} 个重复卡号未列出")
    deletion_count = sum(1 for row in rows if row.get("kind") == "card" and row.get("deletion"))
    effective_cards = [
        row for row in rows if row.get("kind") == "card" and row.get("effective")
    ]
    if not has_end_card(text):
        warnings.append("未找到 .RELAP END CARD 结束卡")
    if "混合" in _newline_style(text):
        warnings.append("文件包含混合换行符")

    formats = meta["formats"]
    if len(formats) > 1:
        file_format = "mixed"
    elif "fixed-width" in formats:
        file_format = "fixed-width"
    elif "free-format" in formats:
        file_format = "free-format"
    else:
        file_format = "unknown"

    categories = Counter(
        row["category"] for row in rows if row.get("kind") == "card"
    )
    effective_ids = [
        str(row.get("card_id") or "").lower() for row in effective_cards
    ]
    summary: dict[str, Any] = {
        "filename": filename,
        "path": path,
        "encoding": encoding,
        "newline": _newline_style(text),
        "format": file_format,
        "total_lines": len(text.splitlines()),
        "nonempty_lines": sum(bool(line.strip()) for line in text.splitlines()),
        "total_cards": len(card_ids),
        "unique_cards": len(set(normalized_card_ids)),
        "effective_cards": len(effective_cards),
        "overridden_cards": sum(
            1 for row in rows if row.get("kind") == "card" and row.get("overridden")
        ),
        "deleted_cards": sum(
            1 for row in rows if row.get("kind") == "card" and row.get("deleted")
        ),
        "deletion_cards": deletion_count,
        "comment_lines": meta["comments"],
        "continuation_lines": meta["kinds"].get("continuation", 0),
        "unparsed_lines": meta["kinds"].get("unparsed", 0),
        "ignored_columns": meta.get("ignored_columns", 0),
        "has_end_card": has_end_card(text),
        "has_time_control": any(card.startswith("000020") for card in effective_ids),
        "has_hydrodynamic": any(
            row.get("category") in {"水力组件", "连接件"}
            for row in effective_cards
        ),
        "has_heat_structure": any(
            len(card) == 8 and card.startswith("1")
            for card in effective_ids
        ),
        "categories": dict(categories.most_common()),
        "kinds": dict(meta["kinds"]),
        "title": meta["title"],
        "preview": meta["preview"],
        "warnings": warnings,
    }
    return {"summary": summary, "entries": rows}


def parse_relap_input_text(
    text: str, *, filename: str = "pasted.i", encoding: str = "文本", path: str = ""
) -> dict[str, Any]:
    if text.startswith("\ufeff"):
        text = text.lstrip("\ufeff")
        encoding = "UTF-8 BOM"
    return _finish(text, encoding=encoding, filename=filename, path=path)


def parse_relap_input_file(path: str | Path, *, display_path: str | None = None) -> dict[str, Any]:
    p = Path(path)
    if not p.is_file():
        return {"error": f"输入卡不存在: {path}"}
    try:
        text, encoding = _decode_bytes(p.read_bytes())
    except (OSError, UnicodeError) as exc:
        return {"error": f"读取输入卡失败: {exc}"}
    return _finish(text, encoding=encoding, filename=p.name, path=display_path or str(p))


def parse_relap_input_data(data: bytes, *, filename: str = "pasted.i", path: str = "") -> dict[str, Any]:
    try:
        text, encoding = _decode_bytes(data)
    except UnicodeError as exc:
        return {"error": f"解码输入卡失败: {exc}"}
    return _finish(text, encoding=encoding, filename=filename, path=path)


def _parse_target(target: str) -> tuple[str, str, int, int]:
    match = _TARGET_RE.match(target or "")
    if not match:
        raise ValueError(
            "变量定位格式无效，应为 卡号:卡标识:字段序号@源行号，例如 100:new:2@7；"
            "纯数值卡可使用 1000101:-:1@10"
        )
    return (
        str(int(match.group("card"))),
        match.group("keyword"),
        int(match.group("field")),
        int(match.group("line")),
    )


def search_relap_input(text: str, query: str = "", limit: int = 200) -> list[dict[str, Any]]:
    """Search editable RELAP5 card fields and return stable targets."""
    terms = [part.lower() for part in str(query or "").strip().split() if part]
    entries = _parse_rows(text)[0]
    result: list[dict[str, Any]] = []
    for entry in entries:
        if entry.get("kind") != "card" or not entry.get("effective"):
            continue
        fields = entry.get("fields") or []
        spans = entry.get("field_spans") or []
        haystack = " ".join([
            str(entry.get("line_no", "")), str(entry.get("card_id") or ""),
            str(entry.get("keyword") or ""), str(entry.get("raw") or ""), *fields,
        ]).lower()
        if terms and not all(term in haystack for term in terms):
            continue
        for index, value in enumerate(fields):
            keyword = entry.get("target_keyword") or entry.get("keyword") or "-"
            target = f"{entry['card_id']}:{keyword}:{index + 1}@{entry['line_no']}"
            result.append({
                "target": target,
                "line_no": entry["line_no"],
                "field_line_no": entry.get("field_line_nos", [entry["line_no"]] * len(fields))[index],
                "card_id": entry["card_id"],
                "keyword": entry["keyword"],
                "target_keyword": entry.get("target_keyword") or entry["keyword"],
                "field": index + 1,
                "value": value,
                "raw": entry["raw"],
                "format": entry.get("format", ""),
                "category": entry.get("category", ""),
                "span": spans[index] if index < len(spans) else None,
            })
            if len(result) >= max(1, int(limit)):
                return result
    return result


def _locate_target(text: str, card_id: str, keyword: str, field: int, line_no: int) -> dict[str, Any]:
    entries = _parse_rows(text)[0]
    matches = [
        entry for entry in entries
        if entry.get("kind") == "card"
        and entry.get("effective")
        and str(entry.get("line_no")) == str(line_no)
        and _card_key(str(entry.get("card_id") or "")) == _card_key(card_id)
    ]
    if not matches:
        raise ValueError(f"未找到目标卡：第 {line_no} 行，卡号 {card_id}")
    entry = matches[0]
    stable_keyword = str(entry.get("target_keyword") or "-").strip()
    actual_keyword = str(entry.get("keyword") or "").strip()
    requested_keyword = str(keyword or "").strip()
    # Pure numeric cards have no textual keyword. New targets use "-" as a
    # stable card identifier. Accept legacy targets that used the first numeric
    # field value so existing scripts continue to work after that value changes.
    if stable_keyword == "-":
        numeric_legacy = _is_number(requested_keyword)
        if requested_keyword != "-" and not numeric_legacy:
            raise ValueError(
                f"目标卡字段不匹配：第 {line_no} 行卡号 {card_id} 为纯数值卡，"
                f"卡标识应为 -（首字段当前值为 {actual_keyword or '-'}），定位要求 {keyword}"
            )
    elif stable_keyword.lower() != requested_keyword.lower():
        raise ValueError(
            f"目标卡字段不匹配：第 {line_no} 行卡号 {card_id} 的卡标识为 {stable_keyword}，"
            f"定位要求 {keyword}"
        )
    fields = entry.get("fields") or []
    spans = entry.get("field_spans") or []
    if field < 1 or field > len(fields) or field > len(spans):
        raise ValueError(f"字段序号越界：卡 {card_id} 只有 {len(fields)} 个字段")
    if len(spans) != len(fields):
        raise ValueError(f"无法可靠定位卡 {card_id} 的字段字符范围")
    field_line_nos = entry.get("field_line_nos") or []
    return {
        "entry": entry,
        "field": field,
        "value": fields[field - 1],
        "span": spans[field - 1],
        "field_line_no": field_line_nos[field - 1] if field - 1 < len(field_line_nos) else entry["line_no"],
    }


def _encoded_bytes(text: str, encoding_label: str) -> bytes:
    bom_prefixes = {
        "UTF-8 BOM": b"\xef\xbb\xbf",
        "UTF-16 LE BOM": b"\xff\xfe",
        "UTF-16 BE BOM": b"\xfe\xff",
        "UTF-32 LE BOM": b"\xff\xfe\x00\x00",
        "UTF-32 BE BOM": b"\x00\x00\xfe\xff",
    }
    encoding = _python_encoding(encoding_label)
    prefix = bom_prefixes.get(encoding_label, b"")
    return prefix + text.encode(encoding)


def _replace_text_atomic(path: Path, text: str, encoding_label: str) -> None:
    """Compatibility wrapper for callers that provide decoded text."""
    _replace_bytes_atomic(path, _encoded_bytes(text, encoding_label))


def _replace_bytes_atomic(path: Path, data: bytes) -> None:
    """Atomically replace ``path`` with already encoded bytes."""
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _field_location(
    text: str, card_id: str, field: int, line_no: int
) -> dict[str, Any]:
    """Locate a field by card identity, source line and ordinal only.

    Re-parsing after an edit must not depend on the old keyword because the
    keyword is itself the first field on many RELAP5 cards.
    """
    entries = _parse_rows(text)[0]
    matches = [
        entry for entry in entries
        if entry.get("kind") == "card"
        and entry.get("effective")
        and str(entry.get("line_no")) == str(line_no)
        and _card_key(str(entry.get("card_id") or "")) == _card_key(card_id)
    ]
    if not matches:
        raise ValueError(f"修改后未找到生效卡：第 {line_no} 行，卡号 {card_id}")
    entry = matches[0]
    fields = entry.get("fields") or []
    spans = entry.get("field_spans") or []
    lines = entry.get("field_line_nos") or []
    if field < 1 or field > len(fields) or field > len(spans):
        raise ValueError(f"修改后字段序号越界：卡 {card_id} 只有 {len(fields)} 个字段")
    return {
        "entry": entry,
        "field": field,
        "value": fields[field - 1],
        "span": spans[field - 1],
        "field_line_no": lines[field - 1] if field - 1 < len(lines) else entry["line_no"],
    }


def _encoded_replacement(raw_token: str, new_value: str) -> str:
    """Encode a logical field value without changing its quoting style."""
    if (
        len(raw_token) >= 2
        and raw_token[0] in ("'", '"')
        and raw_token[-1] == raw_token[0]
    ):
        quote = raw_token[0]
        return quote + new_value.replace(quote, quote * 2) + quote
    return new_value


def _validate_replacement(located: dict[str, Any], value: str, text: str) -> tuple[str, str]:
    """Validate one logical replacement and return its source encoding."""
    if not isinstance(value, str):
        raise ValueError("新值必须是字符串")
    if not value:
        raise ValueError("新值不能为空")
    if any(ch in value for ch in "\r\n"):
        raise ValueError("新值不能包含换行")

    entry = located["entry"]
    index = int(located["field"]) - 1
    fields = entry.get("fields") or []
    spans = entry.get("field_spans") or []
    start, end = spans[index]
    raw_token = text[start:end]
    if not raw_token:
        raise ValueError("无法读取目标字段的原始文本")

    quoted = (
        len(raw_token) >= 2
        and raw_token[0] in ("'", '"')
        and raw_token[-1] == raw_token[0]
    )
    if not quoted and _is_number(str(located["value"])) and not _is_number(value):
        raise ValueError(f"目标字段是数值域，新值必须为 RELAP5 数值：{value}")
    if not quoted:
        tokens = _token_spans(value)
        if len(tokens) != 1 or tokens[0][0] != value:
            raise ValueError("非引号字段的新值必须是单一字段，不能包含空格、逗号或引号")
    return value, _encoded_replacement(raw_token, value)


def _physical_line_bounds(text: str, start: int, end: int) -> tuple[int, int]:
    """Return document offsets for the physical line containing a span."""
    line_start = max(text.rfind("\n", 0, start), text.rfind("\r", 0, start)) + 1
    next_lf = text.find("\n", end)
    next_cr = text.find("\r", end)
    ends = [pos for pos in (next_lf, next_cr) if pos >= 0]
    line_end = min(ends) if ends else len(text)
    return line_start, line_end


def _changed_preview(text: str, new_text: str, start: int, end: int, new_start: int, new_end: int) -> list[dict[str, Any]]:
    old_line_start, old_line_end = _physical_line_bounds(text, start, end)
    new_line_start, new_line_end = _physical_line_bounds(new_text, new_start, new_end)
    old_line = text[old_line_start:old_line_end]
    new_line = new_text[new_line_start:new_line_end]
    if old_line == new_line:
        return []
    return [{
        "line_no": text.count("\n", 0, old_line_start) + 1,
        "before": old_line,
        "after": new_line,
    }]


def _unified_text_diff(text: str, new_text: str, path: Path) -> str:
    before = text.splitlines(keepends=True)
    after = new_text.splitlines(keepends=True)
    return "".join(difflib.unified_diff(
        before,
        after,
        fromfile=f"{path} (before)",
        tofile=f"{path} (after)",
        n=2,
    ))


def _verify_candidate(
    located: dict[str, Any], new_text: str, new_value: str
) -> dict[str, Any]:
    """Re-parse an edited candidate and verify that only the target changed."""
    entry = located["entry"]
    field = int(located["field"])
    card_id = str(entry.get("card_id") or "")
    line_no = int(entry.get("line_no") or 0)
    check = _field_location(new_text, card_id, field, line_no)
    check_entry = check["entry"]
    old_fields = entry.get("fields") or []
    new_fields = check_entry.get("fields") or []
    if len(new_fields) != len(old_fields):
        raise ValueError(
            f"修改后字段数变化：原 {len(old_fields)} 个，新 {len(new_fields)} 个"
        )
    if check["value"] != new_value:
        raise ValueError(
            f"修改后变量回读不一致：期望 {new_value}，实际 {check['value']}"
        )
    index = field - 1
    old_others = old_fields[:index] + old_fields[index + 1:]
    new_others = new_fields[:index] + new_fields[index + 1:]
    if old_others != new_others:
        raise ValueError("修改后目标卡的其他字段发生变化")
    if check.get("field_line_no") != located.get("field_line_no"):
        raise ValueError("修改后字段所在物理行发生变化")
    return check


def modify_relap_input_file(
    path: str | Path,
    target: str,
    value: str,
    *,
    expected_value: str | None = None,
    apply: bool = True,
) -> dict[str, Any]:
    """Replace one parsed RELAP5 field.

    The target is ``CARD:KEYWORD:FIELD@SOURCE_LINE``. Only the exact source
    span of the selected field is replaced. The candidate deck is reparsed and
    checked before writing; indentation, separators, comments, other fields,
    encoding and newlines are preserved. With ``apply=False`` no file is
    modified and the result contains a preview and unified diff.
    """
    p = Path(path)
    if not p.is_file():
        return {"ok": False, "error": f"输入卡不存在: {path}", "applied": False}
    try:
        card_id, keyword, field, line_no = _parse_target(target)
        data = p.read_bytes()
        text, encoding_label = _decode_bytes(data)
        located = _locate_target(text, card_id, keyword, field, line_no)
        old_value = located["value"]
        if expected_value is not None and str(old_value) != str(expected_value):
            return {
                "ok": False,
                "applied": False,
                "error": f"变量当前值已变化：期望 {expected_value}，实际 {old_value}",
                "old_value": old_value,
                "target": target,
            }
        new_value, encoded_value = _validate_replacement(located, str(value), text)
        start, end = located["span"]
        old_raw = text[start:end]
        if (
            len(old_raw) >= 2
            and old_raw[0] in ("'", '"')
            and old_raw[-1] == old_raw[0]
        ):
            # Use the source token read from the whole document. The normal
            # path should always have the same delimiters as the line token.
            encoded_value = _encoded_replacement(old_raw, new_value)
        new_text = text[:start] + encoded_value + text[end:]
        new_end = start + len(encoded_value)
        line_start, line_end = _physical_line_bounds(new_text, start, new_end)
        candidate_line = new_text[line_start:line_end]
        if len(candidate_line) > _MAX_RECORD_WIDTH:
            raise ValueError(
                f"修改后的第 {text.count(chr(10), 0, line_start) + 1} 行超过 "
                f"{_MAX_RECORD_WIDTH} 列，RELAP5 将忽略超出部分"
            )
        _verify_candidate(located, new_text, new_value)
        candidate_data = _encoded_bytes(new_text, encoding_label)
        decoded_check, _ = _decode_bytes(candidate_data)
        if decoded_check != new_text:
            raise ValueError("修改结果无法按原编码无损回读")
        preview = _changed_preview(text, new_text, start, end, start, new_end)
        diff = _unified_text_diff(text, new_text, p)
        common = {
            "ok": True,
            "path": str(p),
            "target": target,
            "old_value": old_value,
            "new_value": new_value,
            "encoded_value": encoded_value,
            "line_no": line_no,
            "field_line_no": located.get("field_line_no"),
            "card_id": str(located["entry"].get("card_id") or card_id),
            "keyword": str(located["entry"].get("target_keyword") or keyword),
            "field": field,
            "encoding": encoding_label,
            "preview": preview,
            "diff": diff,
        }
        if not apply:
            return {**common, "applied": False, "dry_run": True}

        # Do not overwrite a file changed by another process after it was read.
        if p.read_bytes() != data:
            raise ValueError("输入卡在修改过程中被其他进程更改，已取消写回")
        _replace_bytes_atomic(p, candidate_data)
        written = p.read_bytes()
        if written != candidate_data:
            raise RuntimeError("写回后文件内容与候选内容不一致")
        written_text, written_encoding = _decode_bytes(written)
        _verify_candidate(located, written_text, new_value)
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


def replace_text_key_value(path: str | Path, key: str, value: str) -> dict[str, Any]:
    """Compatibility path for ordinary ``key: value`` text files.

    Unlike the old implementation, this never appends a missing key. That
    prevents invalid pseudo-fields such as ``problem_type: transnt`` from being
    silently added to a RELAP5 deck.
    """
    p = Path(path)
    if not p.is_file():
        return {"ok": False, "error": f"文件不存在: {path}"}
    try:
        data = p.read_bytes()
        text, encoding_label = _decode_bytes(data)
    except (OSError, UnicodeError) as exc:
        return {"ok": False, "error": f"读取文件失败: {exc}"}
    pattern = re.compile(
        rf"(?m)^(?P<prefix>\s*{re.escape(str(key))}\s*:\s*)"
        rf"(?P<value>[^\r\n]*?)(?P<suffix>\s*)$"
    )
    matches = list(pattern.finditer(text))
    if not matches:
        return {"ok": False, "error": f"未找到目标变量：{key}"}
    if len(matches) > 1:
        return {"ok": False, "error": f"目标变量不唯一：{key} 出现 {len(matches)} 次"}
    match = matches[0]
    new_text = text[:match.start("value")] + str(value) + text[match.end("value"):]
    try:
        _replace_text_atomic(p, new_text, encoding_label)
    except (OSError, UnicodeError) as exc:
        return {"ok": False, "error": f"写回文件失败: {exc}"}
    return {
        "ok": True, "path": str(p), "key": str(key),
        "old_value": match.group("value"), "new_value": str(value),
        "encoding": encoding_label,
    }


def apply_relap_input_edit(
    path: str | Path,
    target: str,
    value: str,
    *,
    expected_value: str | None = None,
    apply: bool = True,
) -> dict[str, Any]:
    """Apply a RELAP5 target edit, falling back to legacy key/value text."""
    if _TARGET_RE.match(str(target or "")):
        return modify_relap_input_file(
            path, target, value, expected_value=expected_value, apply=apply
        )
    return replace_text_key_value(path, target, value)
