
"""Command-line entry point for a Prisma test run."""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import logging
import sys
from pathlib import Path

from .executor import execute_testcase


def _project_root(value: str) -> Path:
    candidate = Path(value).expanduser()
    if candidate.is_file():
        if candidate.name.casefold() != "project.json":
            raise argparse.ArgumentTypeError(
                "--project 文件必须是 project.json"
            )
        candidate = candidate.parent
    candidate = candidate.resolve()
    if not candidate.is_dir():
        raise argparse.ArgumentTypeError(f"项目目录不存在: {candidate}")
    if not (candidate / "project.json").is_file() and not (
        candidate / "config.yaml"
    ).is_file():
        raise argparse.ArgumentTypeError(
            f"目录不是 Prisma 项目: {candidate}"
        )
    return candidate


def _workers(value: str) -> int:
    try:
        workers = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("workers 必须是整数") from exc
    if workers < 0:
        raise argparse.ArgumentTypeError("workers 不能小于 0")
    return workers


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m run_prisma",
        description="运行 Prisma Excel/TXT 测试剧本并输出 JSON 执行摘要。",
    )
    parser.add_argument(
        "--project",
        default=".",
        type=_project_root,
        help="项目目录或 project.json；默认当前目录",
    )
    parser.add_argument("--script", help="测试剧本，默认读取项目配置")
    parser.add_argument("--config", help="项目配置，默认 <project>/config.yaml")
    parser.add_argument(
        "--workers",
        type=_workers,
        default=None,
        help="最大并行用例数；0 表示使用配置或 CPU 数",
    )
    return parser


def _remove_console_handlers() -> None:
    logger = logging.getLogger("prisma")
    for handler in list(logger.handlers):
        if not getattr(handler, "_prisma_console", False):
            continue
        logger.removeHandler(handler)
        handler.close()


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    project_root = args.project
    config_path = args.config
    if config_path is None:
        default_config = project_root / "config.yaml"
        config_path = default_config if default_config.is_file() else None

    # The executor and keyword logger normally mirror logs to stdout. Capture
    # those lines so stdout remains a machine-readable JSON document.
    captured = io.StringIO()
    _remove_console_handlers()
    try:
        with contextlib.redirect_stdout(captured):
            summary = execute_testcase(
                file_path=args.script,
                max_workers=args.workers,
                project_root=project_root,
                config_path=config_path,
                return_summary=True,
            )
    finally:
        _remove_console_handlers()

    print(json.dumps(summary.to_dict(), ensure_ascii=False))
    return summary.exit_code


if __name__ == "__main__":
    sys.exit(main())
