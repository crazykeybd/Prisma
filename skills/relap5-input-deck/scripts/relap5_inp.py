#!/usr/bin/env python3
"""Command-line interface for RELAP5 input-deck parsing and safe edits.

Examples:
    python relap5_inp.py parse input.i --json
    python relap5_inp.py search input.i "100 new" --limit 20
    python relap5_inp.py modify input.i --target 100:new:2@5 --value transnt
    python relap5_inp.py modify input.i --target 100:new:2@5 --value transnt --apply
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from pathlib import Path
from types import ModuleType
from typing import Any


def _load_parser() -> ModuleType:
    """Load prisma/common/relap_input_parser.py without requiring PYTHONPATH."""
    candidates: list[Path] = []
    prisma_root = os.environ.get("PRISMA_ROOT")
    if prisma_root:
        candidates.append(Path(prisma_root).expanduser().resolve() / "common" / "relap_input_parser.py")

    for parent in Path(__file__).resolve().parents:
        candidates.append(parent / "prisma" / "common" / "relap_input_parser.py")
        candidates.append(parent / "common" / "relap_input_parser.py")

    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate).casefold()
        if key in seen or not candidate.is_file():
            continue
        seen.add(key)
        spec = importlib.util.spec_from_file_location("_relap_input_parser", candidate)
        if spec is None or spec.loader is None:
            continue
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    raise RuntimeError(
        "未找到 relap_input_parser.py；请设置 PRISMA_ROOT 为含 common/ 的 Prisma 根目录"
    )


def _read_text(path: Path, parser: ModuleType) -> tuple[str, str]:
    """Decode with the parser's own encoding detection to keep search aligned."""
    raw = path.read_bytes()
    text, encoding = parser._decode_bytes(raw)  # Internal by necessity: CLI and parser share one file.
    return text, encoding


def _emit(payload: Any, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        return
    if isinstance(payload, dict) and "summary" in payload:
        summary = payload["summary"]
        fields = (
            ("文件", "filename"), ("编码", "encoding"), ("换行", "newline"),
            ("格式", "format"), ("总卡数", "total_cards"),
            ("生效卡", "effective_cards"), ("覆盖卡", "overridden_cards"),
            ("删除卡", "deleted_cards"), ("续卡行", "continuation_lines"),
            ("未解析行", "unparsed_lines"), ("结束卡", "has_end_card"),
        )
        for label, key in fields:
            value = summary.get(key, "")
            if isinstance(value, bool):
                value = "是" if value else "否"
            print(f"{label}: {value}")
        if summary.get("title"):
            print(f"题目: {summary['title']}")
        for warning in summary.get("warnings", []):
            print(f"警告: {warning}")
        return
    if isinstance(payload, list):
        if not payload:
            print("没有匹配结果。")
            return
        for item in payload:
            print(
                f"{item.get('target', '-')}"
                f"\t当前值={item.get('value', '')}"
                f"\t行={item.get('field_line_no', item.get('line_no', ''))}"
                f"\t类别={item.get('category', '')}"
            )
        return
    if isinstance(payload, dict):
        if payload.get("ok"):
            state = "已写回" if payload.get("applied") else "预览（未写回）"
            print(f"状态: {state}")
            print(f"目标: {payload.get('target', '')}")
            print(f"旧值: {payload.get('old_value', '')}")
            print(f"新值: {payload.get('new_value', '')}")
            for line in payload.get("preview", []):
                print(f"第 {line.get('line_no')} 行:")
                print(f"- {line.get('before', '')}")
                print(f"+ {line.get('after', '')}")
            if payload.get("diff"):
                print(payload["diff"], end="" if payload["diff"].endswith("\n") else "\n")
        else:
            print(f"错误: {payload.get('error', '未知错误')}", file=sys.stderr)
        return
    print(payload)


def _positive_int(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("必须为正整数")
    return number


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="解析、检索和安全修改 RELAP5 输入卡",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    parse_cmd = subparsers.add_parser("parse", help="解析输入卡")
    parse_cmd.add_argument("file", type=Path, help="RELAP5 输入卡路径")
    parse_cmd.add_argument("--json", action="store_true", help="输出完整 JSON")

    search_cmd = subparsers.add_parser("search", help="检索最终生效卡的字段")
    search_cmd.add_argument("file", type=Path, help="RELAP5 输入卡路径")
    search_cmd.add_argument("query", nargs="?", default="", help="空格分隔的 AND 检索词")
    search_cmd.add_argument("--limit", type=_positive_int, default=50, help="最多返回字段数")
    search_cmd.add_argument("--json", action="store_true", help="输出 JSON")

    modify_cmd = subparsers.add_parser("modify", help="修改一个已定位字段")
    modify_cmd.add_argument("file", type=Path, help="RELAP5 输入卡路径")
    modify_cmd.add_argument("--target", required=True, help="卡号:卡标识:字段序号@源行号")
    modify_cmd.add_argument("--value", required=True, help="新字段值")
    modify_cmd.add_argument("--expected", help="写前核对旧值；不匹配则不修改")
    modify_cmd.add_argument(
        "--apply", action="store_true",
        help="实际写回；省略时只预览（dry-run）",
    )
    modify_cmd.add_argument("--json", action="store_true", help="输出 JSON")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        parser = _load_parser()
        path = args.file.expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"输入卡不存在: {args.file}")

        if args.command == "parse":
            result = parser.parse_relap_input_file(path)
            if "error" in result:
                raise ValueError(result["error"])
            _emit(result, args.json)
            return 0

        if args.command == "search":
            text, _ = _read_text(path, parser)
            result = parser.search_relap_input(text, args.query, args.limit)
            _emit(result, args.json)
            return 0

        if args.command == "modify":
            result = parser.modify_relap_input_file(
                path,
                args.target,
                args.value,
                expected_value=args.expected,
                apply=bool(args.apply),
            )
            _emit(result, args.json)
            return 0 if result.get("ok") else 1
    except (OSError, UnicodeError, ValueError, RuntimeError) as exc:
        if getattr(args, "json", False):
            print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        else:
            print(f"错误: {exc}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())