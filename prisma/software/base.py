"""软件适配器公共协议与运行结果类型。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any


class SoftwareAdapterError(RuntimeError):
    """软件适配器无法完成请求时抛出的可诊断错误。"""


class AdapterNotImplementedError(SoftwareAdapterError):
    """适配器已注册但具体求解器尚未实现。"""


_INVALID_PATH_CHARS = re.compile(r'[<>:"|?*\x00-\x1f]')


def normalize_output_path(
    value: str | Path | None,
    default: str = "",
    *,
    label: str = "输出文件名",
) -> str:
    """把输出值规范为运行目录内的 POSIX 相对路径。

    空值使用适配器默认输出名。以 ``/`` 或 ``\\`` 结尾的值视为目录，
    并在目录后追加默认输出文件名。
    """
    raw = str(value or "").strip()
    if not raw:
        raw = str(default or "").strip()
        if not raw:
            return ""

    is_directory = raw.endswith(("/", "\\"))
    normalized = raw.replace("\\", "/")
    if is_directory:
        default_name = str(default or "").strip().replace("\\", "/")
        if not default_name:
            raise SoftwareAdapterError(f"{label}是目录时必须提供默认输出文件名")
        normalized = normalized.rstrip("/") + "/" + default_name

    if normalized.startswith("/") or PurePosixPath(normalized).is_absolute():
        raise SoftwareAdapterError(f"{label}必须是运行目录内的相对路径")
    if _INVALID_PATH_CHARS.search(normalized):
        raise SoftwareAdapterError(f"{label}包含无效字符")

    parts = [part for part in PurePosixPath(normalized).parts if part not in ("", ".")]
    if not parts or any(part == ".." for part in parts):
        raise SoftwareAdapterError(f"{label}不能包含 ..，且必须指向运行目录内的文件")
    return "/".join(parts)


def resolve_output_path(
    run_dir: str | Path,
    value: str | Path | None,
    default: str,
    *,
    label: str = "输出文件名",
) -> Path:
    """解析输出路径、创建父目录，并确保路径不越出运行目录。"""
    base = Path(run_dir).resolve()
    raw = str(value or "").strip()
    if raw:
        probe = raw.replace("\\", "/")
        if raw.endswith(("/", "\\")) or (base / probe).is_dir():
            raw = raw.rstrip("/\\") + "/" + str(default or "").strip()
    relative = normalize_output_path(raw or default, default, label=label)
    output_path = base.joinpath(*PurePosixPath(relative).parts).resolve()
    try:
        output_path.relative_to(base)
    except ValueError as exc:
        raise SoftwareAdapterError(f"{label}必须位于运行目录内") from exc
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise SoftwareAdapterError(
            f"{label}的上级目录无法创建: {output_path.parent}"
        ) from exc
    return output_path


@dataclass(frozen=True)
class SoftwareRunRequest:
    """一次软件运行的统一输入。"""

    case_no: str
    run_dir: Path
    executable: str
    software_type: str
    input_name: str = "input.txt"
    output_name: str = ""
    timeout: int | None = None
    params: dict[str, Any] | None = None
    project_root: Path | None = None
    config_path: Path | None = None


@dataclass(frozen=True)
class SoftwareRunResult:
    """运行成功时返回的标准结果。

    除了业务输出路径外，结果还携带一次运行的可追溯信息，供关键词层
    写入 ``.prisma/run.json`` 和项目级报告使用。
    """

    output_path: Path
    returncode: int | None = None
    analysis: dict[str, Any] | None = None
    software_type: str = ""
    software_version: str = ""
    mode: str = ""
    executable_source: Path | None = None
    executable_path: Path | None = None
    input_path: Path | None = None
    command: tuple[str, ...] = ()
    started_at: str = ""
    finished_at: str = ""
    duration_seconds: float = 0.0
    environment: dict[str, Any] = field(default_factory=dict)
    output_files: tuple[dict[str, Any], ...] = ()
    screen_path: Path | None = None
    screen_excerpt: str = ""
