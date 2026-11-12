"""关键词执行层。

RELAP5 仍保留 ``run_relap5`` 兼容入口；新剧本推荐使用统一的
``run_software``，由 ``software`` 包按软件类型分发到适配器。
"""

from __future__ import annotations

import random
import re
import shutil
import subprocess
import time
from pathlib import Path

from common.input_deck_parser import (
    apply_input_edit,
    modify_input_fields,
    parse_field_edit_lines,
)
from common.run_context import ContextPathError, RunContext
from common.results import (
    compare_points,
    environment_summary,
    file_record,
    point_from_key,
    read_jsonl,
    screen_excerpt,
    sha256_file,
    utc_now,
    value_deviation,
    write_check_record,
    write_run_trace,
)
from common.util_prisma import ROOT, init_log, read_config, resolve_path, write_result_to_check
from keywords.keyword_store import BUILTIN_ACTIONS, COMMAND_IMPL, find_custom
from software import (
    AdapterNotImplementedError,
    SoftwareAdapterError,
    SoftwareLibrary,
    SoftwareLibraryError,
    SoftwareRunRequest,
    get_adapter,
    normalize_output_path,
    normalize_support_files,
)
from software.executables import (
    destination_executable as _destination_executable,
    is_windows_executable as _is_windows_executable,
    resolve_executable as _resolve_executable,
)
from software.relap5_output import analyze as _relap_analyze
from software.relap5_output import extract_value as _relap_extract_value

#: 自定义关键词命令模板里的占位符，例如 ``{dir}``、``{p1}``。
_TEMPLATE_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _result_table_cell(records, value_getter):
    """Render one or more check values for a result-table cell."""
    if not records:
        return ""
    if len(records) == 1:
        value = value_getter(records[0])
        return "" if value is None else str(value)

    parts = []
    for record in records:
        key = str(record.get("key") or "结果").strip()
        value = value_getter(record)
        if value is None or str(value).strip() == "":
            value = "未提取"
        parts.append(f"{key}={value}")
    return "；".join(parts)


def _result_table_actual(records):
    return _result_table_cell(records, lambda item: item.get("actual_value"))


def _result_table_expected(records):
    return _result_table_cell(records, lambda item: item.get("expected_value"))


def _result_table_deviation(records):
    return _result_table_cell(
        records,
        lambda item: value_deviation(
            item.get("expected_value"),
            item.get("actual_value"),
        ),
    )


def _software_version(adapter) -> str:
    try:
        value = getattr(adapter, "software_version", "")
        return str(value() if callable(value) else value or "")
    except Exception:
        return ""


def _trace_path(path, base):
    record = file_record(path, base=base)
    return record or {}


def _basic_run_trace(
    *,
    case_no,
    software_type,
    status,
    started_at,
    started_clock,
    mode="",
    software_version="",
    output_path=None,
    input_path=None,
    executable_path=None,
    executable_source=None,
    command=(),
    returncode=None,
    analysis=None,
    run_path=None,
    screen_path=None,
    output_files=(),
    error="",
):
    output_record = _trace_path(output_path, run_path)
    input_record = _trace_path(input_path, run_path)
    executable_record = _trace_path(executable_path, run_path)
    executable_source_hash = sha256_file(executable_source)
    environment = environment_summary()
    return {
        "case_no": str(case_no),
        "status": status,
        "software_type": str(software_type or ""),
        "software_version": str(software_version or ""),
        "mode": str(mode or ""),
        "started_at": started_at,
        "finished_at": utc_now(),
        "duration_seconds": round(max(0.0, time.monotonic() - started_clock), 6),
        "returncode": returncode,
        "command": [str(item) for item in (command or ())],
        "executable_path": executable_record.get("path", ""),
        "executable_sha256": (
            executable_source_hash
            or executable_record.get("sha256", "")
        ),
        "input_path": input_record.get("path", ""),
        "input_sha256": input_record.get("sha256", ""),
        "output_path": output_record.get("path", ""),
        "output_sha256": output_record.get("sha256", ""),
        "output_files": list(output_files or ()),
        "screen_path": (
            _trace_path(screen_path, run_path).get("path", "")
            if screen_path
            else ""
        ),
        "screen_excerpt": screen_excerpt(screen_path) if screen_path else "",
        "analysis": analysis or {},
        "host": environment.get("host", ""),
        "environment": environment,
        "error": str(error or ""),
    }


def is_windows_executable(path):
    """兼容入口：只接受非空 Windows PE 可执行文件。"""
    return _is_windows_executable(path)


def resolve_executable(
    object_name, run_dir, project_root=None, *, software_key="relap5"
):
    """按项目软件类型查找可执行文件，并保留 RELAP5 目录回退。"""
    return _resolve_executable(
        object_name, run_dir, software_key, project_root=project_root
    )


def destination_executable(
    source, run_dir, project_root=None, *, software_key="relap5",
    copy_runtime=True,
):
    """兼容入口：返回运行目录中的可执行文件，必要时复制。"""
    return _destination_executable(
        source,
        run_dir,
        software_key,
        project_root=project_root,
        copy_runtime=copy_runtime,
    )


def relap_analyze(outdta_path):
    """兼容入口：分析 RELAP5 输出。"""
    return _relap_analyze(outdta_path)


def relap_extract_value(text, key):
    """兼容入口：从 RELAP5 输出中提取变量值。"""
    return _relap_extract_value(text, key)


def _scoped_path(context, value, base, label):
    if context:
        return context.resolve(value, base, label=label)
    return resolve_path(value, base)


def _single_name(value, label):
    name = str(value or "").strip()
    if not name or Path(name).name != name or name in (".", ".."):
        raise ContextPathError(f"{label}必须是文件名，不能包含目录")
    return name


def _declared_input_names(software_key):
    """返回被测软件登记的输入卡列表（内置软件通常只有一项）。"""
    try:
        adapter = get_adapter(software_key)
    except SoftwareAdapterError:
        return []
    names = [
        str(name).strip()
        for name in (getattr(adapter, "input_filenames", ()) or ())
        if str(name).strip()
    ]
    if not names:
        primary = str(getattr(adapter, "input_filename", "") or "").strip()
        if primary:
            names.append(primary)
    return list(dict.fromkeys(names))


def _stage_input_cards(
    logger, case_no, out_dir, root, software_key, input_src, input_name,
    declared_inputs,
):
    """Stage input cards last so they override files copied from ``bin/``."""
    staged = {"input.txt": input_src}
    primary = Path(input_name).name
    if primary.casefold() != "input.txt":
        shutil.copy2(input_src, out_dir / primary)
        staged[primary.casefold()] = input_src

    for name in declared_inputs:
        marker = name.casefold()
        if marker in staged:
            continue
        source = next(
            (
                path for path in (
                    root / "inputs" / software_key / name,
                    root / "inputs" / name,
                )
                if path.is_file()
            ),
            None,
        )
        if source is not None:
            shutil.copy2(source, out_dir / name)
            staged[marker] = source
            continue
        bundled = out_dir / name
        if bundled.is_file():
            staged[marker] = bundled
            continue
        logger.warning(
            "[%s] declared input card not found: %s", case_no, name)

    shutil.copy2(input_src, out_dir / "input.txt")
    logger.info(
        "[%s] folder_prep staged input cards: %s",
        case_no,
        ", ".join(sorted(staged)),
    )


def _software_support_config(software_key):
    """返回软件登记的默认依赖及软件库 bin 目录。"""
    try:
        adapter = get_adapter(software_key)
    except SoftwareAdapterError:
        return [], None
    try:
        defaults = normalize_support_files(
            getattr(adapter, "support_files", ()) or ()
        )
    except SoftwareLibraryError:
        defaults = []
    library = getattr(adapter, "library", None)
    if not isinstance(library, SoftwareLibrary):
        library = SoftwareLibrary()
    try:
        library_bin = library.entry_root(adapter.key) / "bin"
    except SoftwareLibraryError:
        library_bin = None
    return defaults, library_bin


def _support_selection(selection, defaults):
    """解析剧本第 6 列的依赖选择。

    返回 ``(patterns, disabled, explicit, copy_library_bin)``：空值表示
    采用软件默认配置并复制软件库 ``bin/``，``none``/``无`` 禁用依赖复制，
    其他内容作为显式覆盖。
    """
    text = str(selection or "").strip()
    marker = text.casefold()
    if marker in {"none", "无", "不复制"}:
        return [], True, True, False
    if not text:
        return list(defaults), False, False, True
    if marker in {"all", "全部"}:
        return list(defaults), False, True, True
    return normalize_support_files(text), False, True, False


def _inside(root, candidate):
    try:
        Path(candidate).resolve().relative_to(Path(root).resolve())
        return True
    except (OSError, ValueError):
        return False


def _copy_support_patterns(
    logger, case_no, patterns, out_dir, *, project_root, software_key,
    executable_source=None, software_bin=None,
):
    """从软件库、程序目录和项目 inputs 复制运行依赖。"""
    roots = [
        software_bin,
        Path(executable_source).parent if executable_source else None,
        project_root / "inputs",
        project_root / "inputs" / software_key,
    ]
    unique_roots = []
    seen_roots: set[str] = set()
    for candidate in roots:
        if candidate is None:
            continue
        path = Path(candidate).resolve()
        marker = str(path).casefold()
        if marker in seen_roots or not path.is_dir():
            continue
        seen_roots.add(marker)
        unique_roots.append(path)

    copied = 0
    for pattern in patterns:
        matched = 0
        for source_root in unique_roots:
            try:
                candidates = sorted(
                    source_root.glob(pattern),
                    key=lambda path: str(path).casefold(),
                )
            except (OSError, ValueError) as exc:
                logger.warning(
                    "[%s] invalid support file pattern %s: %s",
                    case_no, pattern, exc)
                continue
            for source in candidates:
                if not source.is_file() or not _inside(source_root, source):
                    continue
                matched += 1
                relative = source.resolve().relative_to(source_root)
                destination = (out_dir / relative).resolve()
                if not _inside(out_dir, destination):
                    logger.warning(
                        "[%s] support file path escaped run directory: %s",
                        case_no, relative)
                    continue
                if destination == source.resolve():
                    continue
                destination.parent.mkdir(parents=True, exist_ok=True)
                try:
                    shutil.copy2(source, destination)
                    copied += 1
                except PermissionError as exc:
                    if destination.is_file() and destination.stat().st_size > 0:
                        logger.warning(
                            "[%s] support file busy, keeping existing %s: %s",
                            case_no, destination, exc)
                        continue
                    logger.warning(
                        "[%s] cannot copy support file %s: %s",
                        case_no, source, exc)
                except OSError as exc:
                    logger.warning(
                        "[%s] cannot copy support file %s: %s",
                        case_no, source, exc)
        if matched == 0:
            logger.warning(
                "[%s] support file pattern matched no files: %s",
                case_no, pattern)
    return copied


def _copy_software_bin(logger, case_no, software_bin, out_dir):
    """将软件库 bin 目录的全部文件按其相对路径复制到运行目录。"""
    if software_bin is None or not Path(software_bin).is_dir():
        logger.warning(
            "[%s] software library bin not found: %s",
            case_no, software_bin or "")
        return 0

    source_root = Path(software_bin).resolve()
    output_root = Path(out_dir).resolve()
    copied = 0
    for source in sorted(
        (path for path in source_root.rglob("*") if path.is_file()),
        key=lambda path: str(path).casefold(),
    ):
        if not _inside(source_root, source):
            logger.warning(
                "[%s] software library bin path escaped: %s",
                case_no, source)
            continue
        relative = source.resolve().relative_to(source_root)
        destination = (output_root / relative).resolve()
        if not _inside(output_root, destination):
            logger.warning(
                "[%s] software library bin path escaped run directory: %s",
                case_no, relative)
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(source, destination)
            copied += 1
        except PermissionError as exc:
            if destination.is_file() and destination.stat().st_size > 0:
                logger.warning(
                    "[%s] software library file busy, keeping existing %s: %s",
                    case_no, destination, exc)
                continue
            logger.warning(
                "[%s] cannot copy software library file %s: %s",
                case_no, source, exc)
        except OSError as exc:
            logger.warning(
                "[%s] cannot copy software library file %s: %s",
                case_no, source, exc)
    return copied


class KeyPrisma:
    @staticmethod
    def _project_root(context=None):
        return Path(context.root).resolve() if context else ROOT

    @staticmethod
    def _project_config(context=None):
        return read_config(context.config_path) if context else read_config()

    @staticmethod
    def _project_log(context=None):
        return KeyPrisma._project_root(context) / "log"

    @staticmethod
    def _software_key(context=None):
        config = KeyPrisma._project_config(context)
        try:
            return get_adapter(
                str(config.get("softwareType") or "relap5")
            ).key
        except SoftwareAdapterError:
            return "relap5"

    @staticmethod
    def folder_prep(
        case_no, run_dir, input_name, executable_name, context=None,
        *, support_files=None, software_key=None,
    ):
        """创建运行目录，复制输入卡、程序及该软件专用的运行依赖。"""
        root = KeyPrisma._project_root(context)
        project_root = Path(context.root).resolve() if context else None
        software_key = software_key or KeyPrisma._software_key(context)
        logger = init_log(root / "log")
        default_support_files, software_bin = _software_support_config(
            software_key
        )
        try:
            (
                support_patterns,
                support_disabled,
                explicit_support,
                copy_library_bin,
            ) = (
                _support_selection(support_files, default_support_files)
            )
        except SoftwareLibraryError as exc:
            logger.error(
                "[%s] folder_prep support files rejected: %s",
                case_no, exc)
            return False
        managed_support = (
            support_disabled
            or explicit_support
            or bool(default_support_files)
            or copy_library_bin
        )
        try:
            src_input = _scoped_path(
                context, input_name, root / "inputs", "源输入卡"
            )
            out_dir = _scoped_path(
                context, run_dir, root / "test_cases", "运行目录"
            )
        except (ContextPathError, ValueError) as exc:
            logger.error("[%s] folder_prep path rejected: %s", case_no, exc)
            return False
        out_dir.mkdir(parents=True, exist_ok=True)

        input_candidates = [
            src_input,
            root / "inputs" / software_key / input_name,
            root / "inputs" / "relap5" / input_name,
            root / "inputs" / input_name,
        ]
        input_src = next((path for path in input_candidates if path.is_file()), None)
        if input_src is None:
            logger.error("[%s] input deck not found: %s", case_no, input_name)
            return False
        declared_inputs = _declared_input_names(software_key)

        exe_src = None
        if executable_name:
            exe_src = resolve_executable(
                executable_name,
                out_dir,
                project_root=project_root,
                software_key=software_key,
            )
            if exe_src is None:
                logger.warning(
                    "[%s] no valid Windows executable found for %s; "
                    "zero-byte and non-PE placeholders are ignored",
                    case_no, executable_name)
            else:
                destination = destination_executable(
                    exe_src,
                    out_dir,
                    project_root=project_root,
                    software_key=software_key,
                    copy_runtime=not managed_support,
                )
                logger.info("[%s] executable -> %s", case_no, destination)

        if support_disabled:
            logger.info("[%s] folder_prep support files disabled", case_no)
        else:
            if copy_library_bin:
                copied_bin = _copy_software_bin(
                    logger, case_no, software_bin, out_dir)
                logger.info(
                    "[%s] folder_prep software-library bin copied=%d source=%s",
                    case_no, copied_bin, software_bin or "")
            if support_patterns:
                copied = _copy_support_patterns(
                    logger,
                    case_no,
                    support_patterns,
                    out_dir,
                    project_root=root,
                    software_key=software_key,
                    executable_source=exe_src,
                    software_bin=software_bin,
                )
                logger.info(
                    "[%s] folder_prep support files copied=%d patterns=%s",
                    case_no, copied, ", ".join(support_patterns))
            elif not explicit_support and (
                exe_src is not None or copy_library_bin
            ):
                # 兼容旧剧本：软件未登记依赖时，仍自动复制 tpf* 物性文件。
                # Generic files first, software-specific files last.
                support_dirs = [
                    root / "inputs",
                    root / "inputs" / software_key,
                    exe_src.parent if exe_src is not None else None,
                ]
                for support_dir in dict.fromkeys(
                    path for path in support_dirs if path is not None
                ):
                    if not support_dir.is_dir():
                        continue
                    for support_file in support_dir.glob("tpf*"):
                        if not support_file.is_file():
                            continue
                        destination = out_dir / support_file.name
                        if destination.resolve() == support_file.resolve():
                            continue
                        try:
                            shutil.copy2(support_file, destination)
                        except PermissionError as exc:
                            if (
                                destination.is_file()
                                and destination.stat().st_size > 0
                            ):
                                logger.warning(
                                    "[%s] support file busy, keeping existing "
                                    "%s: %s",
                                    case_no, destination, exc)
                                continue
                            raise
        # Input cards must be copied after software-library bin files and
        # support files. A bundled indta.i or input.txt must never replace the
        # card selected by the test case.
        _stage_input_cards(
            logger,
            case_no,
            out_dir,
            root,
            software_key,
            input_src,
            input_name,
            declared_inputs,
        )
        logger.info("[%s] folder_prep -> %s", case_no, out_dir)
        return True

    @staticmethod
    def modify_inp(case_no, run_dir, object_name, key, value, context=None):
        """按格式自动定位并原位修改一个或多个输入卡字段。"""
        root = KeyPrisma._project_root(context)
        logger = init_log(root / "log")
        try:
            run_path = _scoped_path(
                context, run_dir, root / "test_cases", "运行目录"
            )
            path = _scoped_path(context, object_name, run_path, "输入卡文件")
        except (ContextPathError, ValueError) as exc:
            logger.error("[%s] modify_inp path rejected: %s", case_no, exc)
            return False
        try:
            edits = parse_field_edit_lines(key, value)
        except ValueError as exc:
            logger.error("[%s] modify_inp mapping invalid: %s", case_no, exc)
            return False
        if len(edits) == 1:
            target, new_value = edits[0]
            result = apply_input_edit(path, target, new_value)
        else:
            result = modify_input_fields(path, edits)
        if not result.get("ok"):
            logger.error("[%s] modify_inp failed for %s targets=%s: %s",
                         case_no, path, result.get("target") or key,
                         result.get("error"))
            return False
        if len(edits) > 1:
            for item in result.get("results") or []:
                logger.info(
                    "[%s] modify_inp %s -> %s (%s -> %s)",
                    case_no,
                    item.get("target", ""),
                    item.get("new_value", ""),
                    item.get("old_value", ""),
                    item.get("new_value", ""),
                )
            logger.info(
                "[%s] modify_inp batch applied %s fields to %s",
                case_no,
                result.get("count", len(edits)),
                path,
            )
            return True
        logger.info("[%s] modify_inp %s -> %s (%s -> %s)",
                    case_no, edits[0][0], edits[0][1],
                    result.get("old_value", ""),
                    result.get("new_value", edits[0][1]))
        return True

    @staticmethod
    def run(
        case_no, run_dir, object_name, output_name="output.txt",
        key="keff", value="1.00003", context=None,
    ):
        """运行普通被测程序；RELAP5 必须使用 run_relap5 或 run_software。"""
        root = KeyPrisma._project_root(context)
        logger = init_log(root / "log")
        config = KeyPrisma._project_config(context)
        try:
            run_dir_path = _scoped_path(
                context, run_dir, root / "test_cases", "运行目录"
            )
            out_path = _scoped_path(
                context, output_name, run_dir_path, "输出文件名"
            )
        except (ContextPathError, ValueError) as exc:
            logger.error("[%s] run path rejected: %s", case_no, exc)
            return False
        mode = str(config.get("runMode", "simulate")).lower()
        if mode != "real":
            time.sleep(random.uniform(1, 5))
            out_path.write_text(f"{key}: {value}\n", encoding="utf-8")
            logger.info("[%s] run(simulate) -> %s", case_no, out_path)
            return True

        software_key = KeyPrisma._software_key(context)
        exe_path = resolve_executable(
            object_name,
            run_dir_path,
            project_root=root,
            software_key=software_key,
        )
        if exe_path is None:
            logger.error(
                "[%s] no valid Windows executable found for %s; "
                "zero-byte and non-PE placeholders are ignored",
                case_no, object_name)
            return False
        if exe_path.name.lower().startswith("relap5"):
            logger.error(
                "[%s] RELAP5 must use run_relap5 or run_software, not run; "
                "configure check_output with an outdta variable "
                "(for example p@100010000), not a keff placeholder",
                case_no)
            return False

        input_path = run_dir_path / "input.txt"
        args_spec = config.get("runArgs") or ["{input}"]
        rendered = [
            str(arg).format(input=str(input_path), output=str(out_path), exe=str(exe_path))
            for arg in args_spec
        ]
        try:
            process = subprocess.run(
                [str(exe_path), *rendered], cwd=run_dir_path,
                capture_output=True, text=True, errors="replace", check=False)
            out_path.write_text((process.stdout or "") + (process.stderr or ""), encoding="utf-8")
            logger.info("[%s] run(real) returncode=%s -> %s",
                        case_no, process.returncode, out_path)
            return process.returncode == 0
        except OSError as exc:
            logger.error("[%s] run(real) failed: %s (%s)", case_no, exc, exe_path)
            return False
        except Exception as exc:
            logger.exception("[%s] run(real) failed: %s", case_no, exc)
            return False

    @staticmethod
    def check_output(
        case_no, run_dir, object_name, key, expected_value,
        software_type=None, context=None,
    ):
        """提取输出值并回填结果表。

        ``key: value`` 通用格式优先；未命中时按可选软件类型选择解析器。
        未指定软件类型时保留旧 RELAP5 行为，可继续读取 outdta 和
        ``变量@九位控制体编号``。
        """
        root = KeyPrisma._project_root(context)
        logger = init_log(root / "log")
        run_path = None
        output_record = {}
        comparison = None
        actual = None
        resolved_software = str(software_type or "relap5").strip() or "relap5"

        def record(status, error="", *, actual_value=None):
            if run_path is None:
                return
            actual_point = point_from_key(actual_value, key)
            expected_point = point_from_key(expected_value, key)
            payload = {
                "case_no": str(case_no),
                "status": status,
                "passed": status == "passed",
                "software_type": resolved_software,
                "key": str(key or ""),
                "expected_value": expected_value,
                "actual_value": actual_value,
                "output_file": output_record.get("path", ""),
                "output_sha256": output_record.get("sha256", ""),
                "output": output_record,
                "point": actual_point,
                "expected_point": expected_point,
                "comparison": comparison,
                "rtol": 1e-4,
                "atol": 1e-6,
                "error": str(error or ""),
                "host": environment_summary().get("host", ""),
            }
            try:
                write_check_record(run_path, payload)
            except Exception as trace_error:
                logger.exception(
                    "[%s] cannot write check trace: %s",
                    case_no,
                    trace_error,
                )

        try:
            run_path = _scoped_path(
                context, run_dir, root / "test_cases", "运行目录"
            )
            path = _scoped_path(context, object_name, run_path, "输出文件")
        except (ContextPathError, ValueError) as exc:
            logger.error("[%s] check_output path rejected: %s", case_no, exc)
            record("error", exc)
            return False
        if not path.exists():
            logger.error("[%s] output not found: %s", case_no, path)
            record("error", f"输出文件不存在: {path}")
            return False
        output_record = file_record(path, base=run_path, role="output") or {}
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            logger.error("[%s] cannot read output %s: %s", case_no, path, exc)
            record("error", f"输出文件读取失败: {exc}")
            return False
        pattern = re.compile(rf"^\s*{re.escape(key)}\s*:\s*(.+?)\s*$", re.I)
        for line in text.splitlines():
            match = pattern.match(line)
            if match:
                actual = match.group(1)
                break

        if actual is None:
            requested_type = str(software_type or "").strip()
            try:
                adapter = get_adapter(requested_type or "relap5")
                resolved_software = adapter.key
                actual = adapter.extract_value(text, key)
            except AdapterNotImplementedError as exc:
                logger.error("[%s] check_output: %s", case_no, exc)
                record("error", exc)
                return False
            except SoftwareAdapterError as exc:
                logger.error("[%s] check_output software type error: %s", case_no, exc)
                record("error", exc)
                return False
            except Exception as exc:
                logger.exception("[%s] check_output output parser failed: %s", case_no, exc)
                record("error", exc)
                return False

        if actual is None:
            logger.error("[%s] key not found in output: %s", case_no, key)
            record("error", f"输出中未找到检查键: {key}")
            return False

        comparison = compare_points(
            [point_from_key(actual, key)],
            [point_from_key(expected_value, key)],
            rtol=1e-4,
            atol=1e-6,
        )
        passed = bool(comparison.get("passed"))
        conclusion = "通过" if passed else "不通过"

        config = KeyPrisma._project_config(context)
        try:
            configured_check_file = config.get("checkConfigPath")
            check_file = (
                _scoped_path(
                    context,
                    configured_check_file,
                    root,
                    "结果文件",
                )
                if configured_check_file
                else None
            )
        except (ContextPathError, ValueError) as exc:
            logger.error("[%s] check_output result path rejected: %s", case_no, exc)
            record("error", exc, actual_value=actual)
            return False

        if check_file is not None:
            try:
                existing_checks = [
                    item
                    for item in read_jsonl(
                        run_path / ".prisma" / "checks.jsonl"
                    )
                    if str(item.get("case_no") or "").strip()
                    == str(case_no).strip()
                ]
                result_records = [
                    *existing_checks,
                    {
                        "case_no": str(case_no),
                        "key": str(key or ""),
                        "expected_value": expected_value,
                        "actual_value": actual,
                        "passed": passed,
                        "status": "passed" if passed else "failed",
                        "comparison": comparison,
                    },
                ]
                table_conclusion = (
                    "通过"
                    if all(item.get("passed") for item in result_records)
                    else "不通过"
                )
                write_result_to_check(
                    check_file,
                    case_no,
                    _result_table_actual(result_records),
                    table_conclusion,
                    base=root,
                    expected_value=_result_table_expected(result_records),
                    deviation=_result_table_deviation(result_records),
                )
            except Exception as exc:
                logger.exception("[%s] cannot update result table: %s", case_no, exc)
                record("error", f"结果表写入失败: {exc}", actual_value=actual)
                return False

        record("passed" if passed else "failed", actual_value=actual)
        logger.info("[%s] check_output %s=%s (expected=%s) -> %s",
                    case_no, key, actual, expected_value, conclusion)
        return passed

    @staticmethod
    def run_software(case_no, run_dir, executable, software_type,
                     input_name="", output_name="", timeout=None, context=None):
        """通过软件适配器运行一个求解器。"""
        root = KeyPrisma._project_root(context)
        project_root = Path(context.root).resolve() if context else None
        logger = init_log(root / "log")
        run_path = None
        adapter = None
        output_path = None
        input_leaf = ""
        started_at = utc_now()
        started_clock = time.monotonic()

        def output_candidates():
            if run_path is None:
                return []
            candidates = []
            if output_path:
                candidates.append(run_path / output_path)
            candidates.extend((run_path / "outdta", run_path / "screen.txt"))
            records = []
            seen = set()
            for candidate in candidates:
                item = file_record(candidate, base=run_path)
                if not item or item["absolute_path"] in seen:
                    continue
                seen.add(item["absolute_path"])
                records.append(item)
            return records

        def record_failure(error):
            if run_path is None:
                return
            try:
                trace = _basic_run_trace(
                    case_no=case_no,
                    software_type=(
                        getattr(adapter, "key", "") if adapter else software_type
                    ),
                    software_version=_software_version(adapter),
                    status="failed",
                    mode="",
                    started_at=started_at,
                    started_clock=started_clock,
                    output_path=(run_path / output_path) if output_path else None,
                    input_path=(run_path / input_leaf) if input_leaf else None,
                    run_path=run_path,
                    screen_path=run_path / "screen.txt",
                    output_files=output_candidates(),
                    error=error,
                )
                write_run_trace(run_path, trace)
            except Exception as trace_error:
                logger.exception(
                    "[%s] cannot write failed run trace: %s",
                    case_no,
                    trace_error,
                )

        try:
            run_path = _scoped_path(
                context, run_dir, root / "test_cases", "运行目录"
            )
            input_leaf = _single_name(input_name, "输入卡名")
            adapter = get_adapter(software_type)
            default_output = (
                str(getattr(adapter, "output_filename", "") or "").strip()
                or "outdta"
            )
            output_path = normalize_output_path(
                output_name, default_output, label="输出文件名"
            )
            timeout_sec = None
            if str(timeout or "").strip():
                try:
                    timeout_sec = int(str(timeout).strip())
                except ValueError as exc:
                    raise SoftwareAdapterError(f"超时必须是整数秒，当前值为: {timeout}") from exc
                if timeout_sec <= 0:
                    raise SoftwareAdapterError("超时必须大于 0 秒")

            request = SoftwareRunRequest(
                case_no=case_no,
                run_dir=run_path,
                executable=str(executable or "").strip(),
                software_type=adapter.key,
                input_name=input_leaf,
                output_name=output_path,
                timeout=timeout_sec,
                project_root=project_root,
                config_path=context.config_path if context else None,
            )
            result = adapter.execute(request)
            if getattr(adapter, "requires_input_ok", False):
                analysis = result.analysis or {}
                if not analysis.get("input_ok"):
                    logger.error("[%s] run_software(%s) 输入处理未成功",
                                 case_no, adapter.key)
                    record_failure("输入处理未成功")
                    return False
            trace = _basic_run_trace(
                case_no=case_no,
                software_type=result.software_type or adapter.key,
                software_version=result.software_version
                or _software_version(adapter),
                status="passed",
                mode=result.mode,
                started_at=result.started_at or started_at,
                started_clock=started_clock,
                output_path=result.output_path,
                input_path=result.input_path,
                executable_path=result.executable_path,
                executable_source=result.executable_source,
                command=result.command,
                returncode=result.returncode,
                analysis=result.analysis,
                run_path=run_path,
                screen_path=result.screen_path,
                output_files=result.output_files,
            )
            trace["duration_seconds"] = round(
                float(result.duration_seconds), 6
            )
            trace["finished_at"] = result.finished_at
            if result.environment:
                trace["environment"] = result.environment
                trace["host"] = result.environment.get("host", trace["host"])
            try:
                write_run_trace(run_path, trace)
            except Exception as trace_error:
                logger.exception(
                    "[%s] cannot write run trace: %s", case_no, trace_error
                )
                return False
            logger.info("[%s] run_software(%s) -> %s",
                        case_no, adapter.key, result.output_path)
            return True
        except AdapterNotImplementedError as exc:
            logger.error("[%s] run_software(%s) 未实现: %s",
                         case_no, software_type, exc)
            record_failure(exc)
            return False
        except ContextPathError as exc:
            logger.error("[%s] run_software path rejected: %s", case_no, exc)
            record_failure(exc)
            return False
        except SoftwareAdapterError as exc:
            logger.error("[%s] run_software(%s) failed: %s",
                         case_no, software_type, exc)
            record_failure(exc)
            return False
        except Exception as exc:
            logger.exception("[%s] run_software(%s) failed: %s",
                             case_no, software_type, exc)
            record_failure(exc)
            return False

    @staticmethod
    def run_relap5(case_no, run_dir, object_name, input_name="input.txt",
                   output_name="outdta", timeout=None, context=None):
        """兼容入口：委托给 RELAP5 适配器执行。"""
        return KeyPrisma.run_software(
            case_no, run_dir, object_name, "relap5",
            input_name or "input.txt", output_name or "outdta", timeout,
            context=context)

    @staticmethod
    def run_code(case_no, run_dir, object_name, *args, context=None):
        root = KeyPrisma._project_root(context)
        logger = init_log(root / "log")
        try:
            run_path = _scoped_path(
                context, run_dir, root / "test_cases", "运行目录"
            )
            path = _scoped_path(context, object_name, run_path, "可执行程序")
        except (ContextPathError, ValueError) as exc:
            logger.error("[%s] run_code path rejected: %s", case_no, exc)
            return False
        if not path.exists():
            logger.error("[%s] executable not found: %s", case_no, path)
            return False
        try:
            process = subprocess.run(
                [str(path), *args], capture_output=True, text=True, check=False)
            logger.info("[%s] run_code returncode=%s", case_no, process.returncode)
            return process.returncode == 0
        except Exception as exc:
            logger.exception("[%s] run_code failed: %s", case_no, exc)
            return False

    @staticmethod
    def check(case_no, run_dir, object_name, *args, context=None):
        root = KeyPrisma._project_root(context)
        logger = init_log(root / "log")
        logger.info("[%s] check args=%s", case_no, args)
        return True

    @staticmethod
    def run_template(case_no, run_dir, template, object_name="", params=(),
                     context=None, keyword_name=""):
        """按自定义关键词的命令模板在运行目录执行命令。"""
        root = KeyPrisma._project_root(context)
        logger = init_log(root / "log")
        label = keyword_name or "custom"
        try:
            if context is None:
                run_path = Path(run_dir).resolve()
            else:
                run_path = context.resolve(
                    run_dir, context.root / "test_cases", label="运行目录"
                )
        except (ContextPathError, ValueError) as exc:
            logger.error("[%s] %s run directory rejected: %s", case_no, label, exc)
            return False
        values = {
            "dir": str(run_path),
            "run_dir": str(run_path),
            "exe": str(object_name or "").strip(),
            "object": str(object_name or "").strip(),
        }
        for position, value in enumerate(params or (), start=1):
            values[f"p{position}"] = "" if value is None else str(value).strip()
        rendered = _TEMPLATE_RE.sub(
            lambda match: values.get(match.group(1), match.group(0)),
            str(template or ""),
        ).strip()
        if not rendered:
            logger.error("[%s] %s command template is empty", case_no, label)
            return False
        if not run_path.is_dir():
            logger.error("[%s] %s run directory not found: %s",
                         case_no, label, run_path)
            return False
        try:
            process = subprocess.run(
                rendered, shell=True, cwd=str(run_path),
                capture_output=True, text=True, check=False,
            )
        except Exception as exc:
            logger.exception("[%s] %s command failed: %s", case_no, label, exc)
            return False
        logger.info("[%s] %s command=%s returncode=%s",
                    case_no, label, rendered, process.returncode)
        tail = (process.stdout or "").strip()
        if tail:
            logger.info("[%s] %s stdout: %s", case_no, label, tail[-1000:])
        errors = (process.stderr or "").strip()
        if errors:
            logger.info("[%s] %s stderr: %s", case_no, label, errors[-1000:])
        return process.returncode == 0


def execute_command(command, context=None):
    case_no, run_dir, action, object_name, *params = [str(value).strip() for value in command]
    root = KeyPrisma._project_root(context)
    logger = init_log(root / "log")
    if context:
        try:
            run_dir = str(context.resolve(
                run_dir, context.root / "test_cases", label="运行目录"
            ))
        except ContextPathError as exc:
            logger.error("[%s] run directory rejected: %s", case_no, exc)
            return False
    if action not in BUILTIN_ACTIONS:
        custom = find_custom(KeyPrisma._project_root(context), action)
        if custom is not None:
            impl = str(custom.get("impl") or COMMAND_IMPL).strip()
            if impl == COMMAND_IMPL:
                return KeyPrisma.run_template(
                    case_no,
                    run_dir,
                    custom.get("command", ""),
                    object_name,
                    params,
                    context=context,
                    keyword_name=action,
                )
            if impl not in BUILTIN_ACTIONS:
                logger.error("[%s] keyword %s has unsupported impl: %s",
                             case_no, action, impl)
                return False
            action = impl
    if action == "folder_prep":
        if len(params) < 2:
            logger.error("[%s] folder_prep needs 2 params", case_no)
            return False
        return KeyPrisma.folder_prep(
            case_no,
            run_dir,
            params[0],
            params[1],
            context=context,
            support_files=params[2] if len(params) > 2 else "",
            software_key=KeyPrisma._software_key(context),
        )
    if action == "modify_inp":
        if len(params) < 2:
            logger.error("[%s] modify_inp needs 2 params", case_no)
            return False
        return KeyPrisma.modify_inp(
            case_no, run_dir, object_name, *params, context=context
        )
    if action == "run":
        if len(params) < 1:
            logger.error("[%s] run needs at least 1 param", case_no)
            return False
        return KeyPrisma.run(case_no, run_dir, object_name, *params, context=context)
    if action == "run_software":
        if len(params) < 2:
            logger.error(
                "[%s] run_software needs software type and input name", case_no)
            return False
        return KeyPrisma.run_software(
            case_no, run_dir, object_name, *params, context=context
        )
    if action == "run_relap5":
        return KeyPrisma.run_relap5(
            case_no, run_dir, object_name, *params, context=context
        )
    if action == "check_output":
        if len(params) < 2:
            logger.error("[%s] check_output needs 2 params", case_no)
            return False
        return KeyPrisma.check_output(
            case_no, run_dir, object_name, *params, context=context
        )
    if action == "run_code":
        if len(params) < 1:
            logger.error("[%s] run_code needs at least 1 param", case_no)
            return False
        return KeyPrisma.run_code(
            case_no, run_dir, object_name, *params, context=context
        )
    if action == "check":
        return KeyPrisma.check(
            case_no, run_dir, object_name, *params, context=context
        )
    logger.error("[%s] unsupported action: %s", case_no, action)
    return False
