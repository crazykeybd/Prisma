"""Central asset library for executables and software-specific templates."""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from common.util_prisma import ROOT


LIBRARY_DIR_NAME = "software-library"
MANIFEST_NAME = "software.json"
MANIFEST_VERSION = 1
_SAFE_KEY = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_PLACEHOLDER = re.compile(r"\{([^{}]*)\}")

#: 内置适配器占用的软件标识，用户新增软件不能覆盖这些键。
BUILTIN_SOFTWARE_KEYS = (
    "relap5",
    "nusolsyslmr",
    "neutron_physics",
    "subchannel",
    "fuel_performance",
)

#: 用户新增软件使用通用命令行适配器。
CUSTOM_ADAPTER_NAME = "generic_cli"
CUSTOM_ARGUMENT_PLACEHOLDERS = ("input", "inputs", "output", "exe")
#: 单个软件最多登记的输入卡数量。
MAX_CUSTOM_INPUTS = 12
MAX_CUSTOM_ARGUMENTS = 40
MAX_CUSTOM_SUPPORT_FILES = 64

#: 输入卡名称分隔符：中英文逗号/分号、顿号、换行。
_INPUT_NAME_SPLIT = re.compile(r"[,;、，；\r\n]+")
#: 运行依赖分隔符。
_SUPPORT_FILE_SPLIT = re.compile(r"[,;、，；\r\n]+")
#: ``{inputN}`` 索引占位符。
_INDEXED_INPUT = re.compile(r"input(\d{1,2})")
_IGNORED_BUNDLE_DIRECTORIES = {
    ".git",
    ".hg",
    ".idea",
    ".svn",
    ".venv",
    ".vscode",
    "__pycache__",
    "log",
    "logs",
    "node_modules",
    "output",
    "outputs",
    "projects",
    "target",
    "test_cases",
    "tests",
    "venv",
}
_IGNORED_BUNDLE_SUFFIXES = {
    ".7z",
    ".bak",
    ".bmp",
    ".gif",
    ".jpeg",
    ".jpg",
    ".log",
    ".md",
    ".mp3",
    ".mp4",
    ".py",
    ".pyc",
    ".pyo",
    ".rar",
    ".tmp",
    ".wav",
    ".xls",
    ".xlsm",
    ".xlsx",
    ".zip",
}
_MAX_BUNDLE_FILES = 5000
_MAX_BUNDLE_BYTES = 4 * 1024 * 1024 * 1024


class SoftwareLibraryError(ValueError):
    """Raised when software assets cannot be imported or resolved safely."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _inside(root: Path, candidate: Path) -> bool:
    try:
        candidate.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _text_field(value, label: str, max_length: int, *, required: bool = False) -> str:
    text = str(value or "").strip()
    if required and not text:
        raise SoftwareLibraryError(f"{label}不能为空")
    if len(text) > max_length:
        raise SoftwareLibraryError(f"{label}不能超过 {max_length} 个字符")
    return text


def _file_name_field(value, label: str, default: str) -> str:
    text = str(value or "").strip() or default
    if len(text) > 120:
        raise SoftwareLibraryError(f"{label}不能超过 120 个字符")
    if text in (".", "..") or Path(text).name != text:
        raise SoftwareLibraryError(f"{label}必须是文件名，不能包含目录")
    if _INVALID_FILENAME_CHARS.search(text):
        raise SoftwareLibraryError(
            f'{label}不能包含 < > : " / \\ | ? * 等字符'
        )
    return text


def _argument_tokens(value) -> list[str]:
    """将命令行参数说明整理为参数列表，保留 Windows 路径中的反斜杠。"""
    if value is None:
        tokens: list[str] = []
    elif isinstance(value, (list, tuple)):
        tokens = [str(item) for item in value]
    else:
        text = str(value).strip()
        if not text:
            tokens = []
        else:
            try:
                tokens = shlex.split(text, posix=False)
            except ValueError as exc:
                raise SoftwareLibraryError(
                    f"命令行参数无法解析（引号不成对?）: {exc}"
                ) from exc
            tokens = [
                item[1:-1]
                if len(item) >= 2 and item[0] == item[-1] and item[0] in "\"'"
                else item
                for item in tokens
            ]
    clean: list[str] = []
    for token in tokens:
        item = str(token)
        if not item.strip():
            continue
        if len(item) > 260:
            raise SoftwareLibraryError("单个命令行参数不能超过 260 个字符")
        for name in _PLACEHOLDER.findall(item):
            if not is_valid_placeholder(name):
                raise SoftwareLibraryError(
                    "命令行参数包含不支持的占位符 {%s}；可用占位符: "
                    "{input} {input1}..{input%d} {inputs} {output} {exe}"
                    % (name, MAX_CUSTOM_INPUTS)
                )
        clean.append(item)
    if not clean:
        clean = ["{input}"]
    if len(clean) > MAX_CUSTOM_ARGUMENTS:
        raise SoftwareLibraryError(
            f"命令行参数不能超过 {MAX_CUSTOM_ARGUMENTS} 项"
        )
    return clean


def is_valid_placeholder(name: str) -> bool:
    """判断命令行模板中的占位符是否受支持。"""
    text = str(name or "").strip()
    if text in CUSTOM_ARGUMENT_PLACEHOLDERS:
        return True
    match = _INDEXED_INPUT.fullmatch(text)
    if not match:
        return False
    return 1 <= int(match.group(1)) <= MAX_CUSTOM_INPUTS


def _input_name_list(value, default: str = "input.txt") -> list[str]:
    """把输入卡配置整理为有序文件名列表（第一个是主输入卡）。

    既支持列表，也支持 "a.txt, b.txt" / 每行一个 的文本写法。
    """
    if value is None:
        raw: list = []
    elif isinstance(value, (list, tuple)):
        raw = list(value)
    else:
        raw = _INPUT_NAME_SPLIT.split(str(value))

    names: list[str] = []
    seen: set[str] = set()
    for item in raw:
        text = str(item or "").strip()
        if not text:
            continue
        name = _file_name_field(text, "输入文件名", default)
        marker = name.casefold()
        if marker in seen:
            continue
        seen.add(marker)
        names.append(name)
    if not names:
        names = [default]
    if len(names) > MAX_CUSTOM_INPUTS:
        raise SoftwareLibraryError(
            f"输入卡最多登记 {MAX_CUSTOM_INPUTS} 个，当前 {len(names)} 个"
        )
    return names


def normalize_support_files(value) -> list[str]:
    """校验并整理运行依赖的相对路径或 glob 模式。

    依赖只能从软件库 ``bin/``、程序所在目录或项目 ``inputs/`` 下解析，
    因此这里拒绝绝对路径、盘符和父目录跳转。
    """
    if value is None:
        raw: list = []
    elif isinstance(value, (list, tuple)):
        raw = list(value)
    else:
        raw = _SUPPORT_FILE_SPLIT.split(str(value))

    patterns: list[str] = []
    seen: set[str] = set()
    for item in raw:
        text = str(item or "").strip()
        if not text:
            continue
        if len(text) > 260:
            raise SoftwareLibraryError("单个运行依赖路径不能超过 260 个字符")
        normalized = text.replace("\\", "/")
        if (
            normalized.startswith("/")
            or re.match(r"^[A-Za-z]:", normalized)
            or ":" in normalized
        ):
            raise SoftwareLibraryError(
                "运行依赖必须是相对路径，不能使用绝对路径或盘符"
            )
        parts = [
            part for part in normalized.split("/")
            if part not in ("", ".")
        ]
        if not parts:
            raise SoftwareLibraryError("运行依赖路径不能为空")
        if ".." in parts or "\x00" in normalized:
            raise SoftwareLibraryError(
                "运行依赖不能包含 .. 或非法字符"
            )
        clean = "/".join(parts)
        marker = clean.casefold()
        if marker in seen:
            continue
        seen.add(marker)
        patterns.append(clean)
    if len(patterns) > MAX_CUSTOM_SUPPORT_FILES:
        raise SoftwareLibraryError(
            "运行依赖最多登记 %d 个，当前 %d 个"
            % (MAX_CUSTOM_SUPPORT_FILES, len(patterns))
        )
    return patterns


def clean_custom_definition(payload) -> dict:
    """校验并整理用户提交的新增软件定义。"""
    if not isinstance(payload, dict):
        raise SoftwareLibraryError("软件定义格式无效")
    key = str(payload.get("software_key") or payload.get("key") or "").strip().lower()
    if not _SAFE_KEY.fullmatch(key):
        raise SoftwareLibraryError(
            "软件标识只能使用小写字母、数字、下划线和连字符，且必须以字母或数字开头"
        )
    if key in BUILTIN_SOFTWARE_KEYS:
        raise SoftwareLibraryError(f"软件标识 {key} 已被内置软件占用，请更换")
    display_name = _text_field(
        payload.get("display_name"), "显示名称", 60, required=True
    )
    timeout_raw = payload.get("timeout_sec")
    timeout_text = "" if timeout_raw is None else str(timeout_raw).strip()
    try:
        timeout_sec = int(timeout_text) if timeout_text else 1800
    except (TypeError, ValueError) as exc:
        raise SoftwareLibraryError("超时时间必须是整数秒") from exc
    if timeout_sec <= 0 or timeout_sec > 86400:
        raise SoftwareLibraryError("超时时间必须在 1 到 86400 秒之间")
    input_filenames = _input_name_list(
        payload.get("input_filenames", payload.get("input_filename")),
        "input.txt",
    )
    support_files = normalize_support_files(payload.get("support_files"))
    output_filename = _file_name_field(
        payload.get("output_filename"), "输出文件名", "output.txt"
    )
    for name in input_filenames:
        if name.casefold() == output_filename.casefold():
            raise SoftwareLibraryError("输出文件名不能与输入卡同名")
    return {
        "software_key": key,
        "display_name": display_name,
        "description": _text_field(
            payload.get("description"), "软件说明", 200
        ),
        "version": _text_field(payload.get("version"), "版本", 80),
        "arguments": _argument_tokens(payload.get("arguments")),
        "input_filenames": input_filenames,
        "support_files": support_files,
        # 兼容旧清单：input_filename 始终是主输入卡（列表第一项）。
        "input_filename": input_filenames[0],
        "output_filename": output_filename,
        "timeout_sec": timeout_sec,
    }


def adapter_input_names(adapter) -> list[str]:
    """返回适配器声明的输入卡列表（第一个为主输入卡）。

    内置适配器通常只声明一个 ``input_filename``；用户新增软件可以声明多个。
    """
    names = getattr(adapter, "input_filenames", None)
    if isinstance(names, (list, tuple)):
        cleaned = [str(item).strip() for item in names if str(item).strip()]
        if cleaned:
            return cleaned
    single = str(getattr(adapter, "input_filename", "") or "").strip()
    return [single] if single else []


def is_windows_executable(path) -> bool:
    """Return True only for a non-empty Windows PE executable."""
    try:
        candidate = Path(path).expanduser().resolve()
        if (
            not candidate.is_file()
            or candidate.suffix.casefold() != ".exe"
            or candidate.stat().st_size == 0
        ):
            return False
        with candidate.open("rb") as stream:
            return stream.read(2) == b"MZ"
    except (OSError, TypeError, ValueError):
        return False


def is_linux_executable(path) -> bool:
    """Return True only for a non-empty, executable Linux ELF file."""
    try:
        candidate = Path(path).expanduser().resolve()
        if not candidate.is_file() or candidate.stat().st_size == 0:
            return False
        if not os.access(candidate, os.X_OK):
            return False
        with candidate.open("rb") as stream:
            return stream.read(4) == b"\x7fELF"
    except (OSError, TypeError, ValueError):
        return False


def is_supported_executable(path) -> bool:
    """Return whether a file is runnable on the current operating system."""
    return is_windows_executable(path) if os.name == "nt" else is_linux_executable(path)


def executable_name_variants(*values: str) -> list[str]:
    """Return direct filename candidates, including .exe-less Linux names."""
    names: list[str] = []
    seen: set[str] = set()
    for value in values:
        name = Path(str(value or "")).name
        if not name:
            continue
        candidates = [name]
        if name.casefold().endswith(".exe") and len(name) > 4:
            candidates.append(name[:-4])
        for candidate in candidates:
            marker = candidate.casefold()
            if marker in seen:
                continue
            seen.add(marker)
            names.append(candidate)
    return names


def _ignore_bundle_entry(directory: str, names: list[str]) -> set[str]:
    current = Path(directory)
    ignored: set[str] = set()
    for name in names:
        candidate = current / name
        lowered = name.casefold()
        if candidate.is_dir():
            if (
                lowered in _IGNORED_BUNDLE_DIRECTORIES
                or lowered.startswith(".")
            ):
                ignored.add(name)
            continue
        if (
            candidate.suffix.casefold() in _IGNORED_BUNDLE_SUFFIXES
            or lowered in {"project.json", "software.json"}
            or lowered.startswith(".env")
        ):
            ignored.add(name)
    return ignored


def iter_runtime_bundle_files(source_root: Path):
    """Yield non-generated files suitable for a software runtime bundle."""
    source_root = Path(source_root).resolve()
    for current, directories, filenames in os.walk(source_root):
        current_path = Path(current)
        ignored = _ignore_bundle_entry(current, [*directories, *filenames])
        directories[:] = [
            name for name in directories if name not in ignored
        ]
        for filename in sorted(filenames, key=str.casefold):
            if filename in ignored:
                continue
            candidate = current_path / filename
            if candidate.is_file():
                yield candidate


class SoftwareLibrary:
    """Store software programs outside individual test projects."""

    def __init__(self, root: Path | None = None):
        self.root = Path(root or (ROOT / LIBRARY_DIR_NAME)).resolve()

    def ensure_root(self) -> Path:
        """Create the library root so it can be opened before first import."""
        self.root.mkdir(parents=True, exist_ok=True)
        return self.root

    def _safe_key(self, value: str) -> str:
        key = str(value or "").strip().lower()
        if not _SAFE_KEY.fullmatch(key):
            raise SoftwareLibraryError("软件标识格式无效")
        return key

    def entry_root(self, software_key: str) -> Path:
        candidate = (self.root / self._safe_key(software_key)).resolve()
        if not _inside(self.root, candidate):
            raise SoftwareLibraryError("软件库路径越界")
        return candidate

    def manifest_path(self, software_key: str) -> Path:
        return self.entry_root(software_key) / MANIFEST_NAME

    def _read_manifest(self, software_key: str) -> dict:
        path = self.manifest_path(software_key)
        if not path.is_file():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SoftwareLibraryError(
                f"软件清单无法读取: {self.entry_root(software_key).name}"
            ) from exc
        if not isinstance(payload, dict):
            raise SoftwareLibraryError("软件清单格式无效")
        return payload

    @staticmethod
    def _manifest_relative(entry_root: Path, value) -> Path | None:
        raw = str(value or "").strip()
        if not raw:
            return None
        candidate = Path(raw)
        if candidate.is_absolute():
            return None
        resolved = (entry_root / candidate).resolve()
        if not _inside(entry_root, resolved):
            return None
        return resolved

    def resolve_executable(
        self,
        software_key: str,
        preferred_name: str = "",
        expected_name: str = "",
    ) -> Path | None:
        """Find a valid executable in the library without trusting manifest paths."""
        entry_root = self.entry_root(software_key)
        if not entry_root.is_dir():
            return None
        manifest = self._read_manifest(software_key)
        candidates: list[Path] = []
        for value in (manifest.get("executable"), preferred_name):
            candidate = self._manifest_relative(entry_root, value)
            if candidate:
                candidates.append(candidate)
        for raw in executable_name_variants(preferred_name, expected_name):
            candidates.extend((entry_root / "bin" / raw, entry_root / raw))
        if entry_root.is_dir():
            pattern = "*.exe" if os.name == "nt" else "*"
            candidates.extend(
                sorted(
                    (path for path in entry_root.rglob(pattern) if path.is_file()),
                    key=lambda path: str(path).casefold(),
                )
            )
        seen: set[str] = set()
        for candidate in candidates:
            key = str(candidate).casefold()
            if key in seen:
                continue
            seen.add(key)
            if _inside(entry_root, candidate) and is_supported_executable(candidate):
                return candidate
        return None

    def template_files(self, software_key: str) -> list[Path]:
        entry_root = self.entry_root(software_key)
        templates_root = entry_root / "templates"
        if not templates_root.is_dir():
            return []
        manifest = self._read_manifest(software_key)
        listed = manifest.get("templates")
        files: list[Path] = []
        if isinstance(listed, list):
            for value in listed:
                candidate = self._manifest_relative(entry_root, value)
                if (
                    candidate
                    and candidate.is_file()
                    and (candidate == templates_root or templates_root in candidate.parents)
                ):
                    files.append(candidate)
        if not files:
            files = sorted(
                (path for path in templates_root.rglob("*") if path.is_file()),
                key=lambda path: str(path).casefold(),
            )
        return list(dict.fromkeys(files))

    @staticmethod
    def _file_count(entry_root: Path) -> tuple[int, int]:
        if not entry_root.is_dir():
            return 0, 0
        count = 0
        size = 0
        for path in entry_root.rglob("*"):
            if not path.is_file() or path.name == MANIFEST_NAME:
                continue
            count += 1
            try:
                size += path.stat().st_size
            except OSError:
                pass
        return count, size

    def describe(self, adapter) -> dict:
        key = self._safe_key(adapter.key)
        entry_root = self.entry_root(key)
        manifest: dict = {}
        manifest_error = ""
        if entry_root.is_dir():
            try:
                manifest = self._read_manifest(key)
            except SoftwareLibraryError as exc:
                manifest_error = str(exc)
        expected_name = str(getattr(adapter, "executable_name", "") or "")
        executable = self.resolve_executable(key, expected_name, expected_name)
        templates = self.template_files(key) if entry_root.is_dir() else []
        file_count, total_size = self._file_count(entry_root)
        # 用户只登记了定义、尚未导入程序时，不应显示成“缺少程序”。
        declared_executable = str(manifest.get("executable") or "").strip()
        installed = bool(declared_executable or executable or file_count)
        if getattr(adapter, "status", "") != "available":
            state = "reserved"
        elif executable:
            state = "ready"
        elif installed or manifest_error:
            state = "missing_program"
        else:
            state = "not_installed"
        return {
            "library_directory": str(entry_root),
            "installed": installed,
            "program_ready": executable is not None,
            "state": state,
            "version": str(manifest.get("version") or ""),
            "executable_path": str(executable) if executable else "",
            "executable_name": executable.name if executable else expected_name,
            "template_count": len(templates),
            "file_count": file_count,
            "total_size": total_size,
            "imported_at": manifest.get("imported_at"),
            "manifest_error": manifest_error,
        }

    def _find_source_executable(
        self, source_root: Path, adapter, preferred_name: str = ""
    ) -> Path:
        expected = str(getattr(adapter, "executable_name", "") or "")
        names = executable_name_variants(preferred_name, expected)
        for name in names:
            direct = source_root / name
            if direct.is_file() and is_supported_executable(direct):
                return direct
        for name in names:
            matches = sorted(
                (
                    path
                    for path in source_root.rglob(name)
                    if path.is_file() and is_supported_executable(path)
                ),
                key=lambda path: (len(path.parts), str(path).casefold()),
            )
            if matches:
                return matches[0]
        pattern = "*.exe" if os.name == "nt" else "*"
        executables = sorted(
            (
                path
                for path in source_root.rglob(pattern)
                if path.is_file() and is_supported_executable(path)
            ),
            key=lambda path: (len(path.parts), str(path).casefold()),
        )
        if len(executables) == 1:
            return executables[0]
        if not executables:
            kind = "Windows PE" if os.name == "nt" else "Linux ELF"
            raise SoftwareLibraryError(
                f"所选文件夹中没有有效的 {kind} 可执行程序"
            )
        kind = "exe" if os.name == "nt" else "可执行"
        raise SoftwareLibraryError(
            f"所选文件夹包含多个{kind}程序，请直接选择要导入的程序文件"
        )

    # ---------- user-defined software definitions ----------

    def custom_definitions(self) -> list[dict]:
        """返回软件库中由用户新增的软件定义（只读清单副本）。"""
        if not self.root.is_dir():
            return []
        definitions: list[dict] = []
        for entry in sorted(self.root.iterdir(), key=lambda p: p.name.casefold()):
            if not entry.is_dir() or entry.name.startswith("."):
                continue
            if entry.name in BUILTIN_SOFTWARE_KEYS:
                continue
            if not _SAFE_KEY.fullmatch(entry.name):
                continue
            try:
                manifest = self._read_manifest(entry.name)
            except SoftwareLibraryError:
                continue
            if str(manifest.get("adapter") or "") != CUSTOM_ADAPTER_NAME:
                continue
            manifest.setdefault("software_key", entry.name)
            definitions.append(manifest)
        return definitions

    def custom_adapters(self) -> list:
        """把用户软件定义实例化为通用命令行适配器。"""
        from software.custom import GenericCommandAdapter

        adapters = []
        for manifest in self.custom_definitions():
            adapters.append(
                GenericCommandAdapter.from_manifest(manifest, library=self)
            )
        return adapters

    def read_manifest(self, software_key: str) -> dict:
        """读取指定软件清单（不存在时返回空字典）。"""
        return dict(self._read_manifest(self._safe_key(software_key)))

    def create_custom_definition(self, payload) -> dict:
        """登记或更新一个用户新增的命令行软件定义。"""
        definition = clean_custom_definition(payload)
        key = definition["software_key"]
        entry_root = self.entry_root(key)
        existing_path = entry_root / MANIFEST_NAME
        existing: dict = {}
        if existing_path.is_file():
            existing = self._read_manifest(key)
            if str(existing.get("adapter") or "") != CUSTOM_ADAPTER_NAME:
                raise SoftwareLibraryError("该软件标识已被其他类型的软件占用")
        if "support_files" not in payload and existing:
            definition["support_files"] = normalize_support_files(
                existing.get("support_files")
            )

        now = _utc_now()
        manifest = {
            **existing,
            "schema_version": MANIFEST_VERSION,
            "software_key": key,
            "display_name": definition["display_name"],
            "description": definition["description"],
            "adapter": CUSTOM_ADAPTER_NAME,
            "arguments": definition["arguments"],
            "input_filenames": definition["input_filenames"],
            "support_files": definition["support_files"],
            "input_filename": definition["input_filename"],
            "output_filename": definition["output_filename"],
            "timeout_sec": definition["timeout_sec"],
            "version": definition["version"] or str(existing.get("version") or ""),
            "executable": str(existing.get("executable") or ""),
            "templates": existing.get("templates") or [],
            "created_at": str(existing.get("created_at") or now),
            "updated_at": now,
        }

        self.ensure_root()
        entry_root.mkdir(parents=True, exist_ok=True)
        temporary = entry_root / f"{MANIFEST_NAME}.{uuid4().hex}.tmp"
        try:
            temporary.write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            temporary.replace(existing_path)
        finally:
            if temporary.exists():
                temporary.unlink(missing_ok=True)
        return manifest

    def remove_custom_definition(self, software_key: str) -> str:
        """删除一个用户新增的软件条目（含已导入的程序）。"""
        key = self._safe_key(software_key)
        if key in BUILTIN_SOFTWARE_KEYS:
            raise SoftwareLibraryError("内置软件不能删除")
        entry_root = self.entry_root(key)
        manifest = self._read_manifest(key) if entry_root.is_dir() else {}
        if str(manifest.get("adapter") or "") != CUSTOM_ADAPTER_NAME:
            raise SoftwareLibraryError("只能删除用户新增的软件")
        shutil.rmtree(entry_root)
        return key

    def import_source(
        self,
        adapter,
        source_path: str | Path,
        version: str = "",
    ) -> dict:
        """Copy a program and its sibling dependencies into the library.

        When a file is selected, its containing folder is treated as the
        program directory so DLLs and property tables move together with it.
        Existing templates are preserved when a program is updated.
        """
        if not str(source_path or "").strip():
            raise SoftwareLibraryError("请选择要导入的程序或专用程序文件夹")
        source = Path(source_path).expanduser().resolve()
        if not source.exists():
            raise SoftwareLibraryError(f"所选软件路径不存在: {source}")
        if source.is_file():
            if not is_supported_executable(source):
                if os.name == "nt":
                    raise SoftwareLibraryError(
                        "请选择有效的、非空的 Windows PE .exe 程序"
                    )
                raise SoftwareLibraryError(
                    "请选择有效的、带执行权限的 Linux ELF 程序"
                )
            source_root = source.parent
            preferred = source.name
        elif source.is_dir():
            source_root = source
            preferred = ""
        else:
            raise SoftwareLibraryError("所选软件路径不是文件或文件夹")

        selected = self._find_source_executable(source_root, adapter, preferred)
        relative_executable = selected.relative_to(source_root)
        key = self._safe_key(adapter.key)
        entry_root = self.entry_root(key)
        old_manifest = self._read_manifest(key) if entry_root.is_dir() else {}

        self.ensure_root()
        staging = (self.root / f".import-{uuid4().hex}").resolve()
        backup = (self.root / f".backup-{uuid4().hex}").resolve()
        if not _inside(self.root, staging) or not _inside(self.root, backup):
            raise SoftwareLibraryError("软件库临时路径越界")

        try:
            staging.mkdir()
            bundle_files = list(iter_runtime_bundle_files(source_root))
            bundle_bytes = sum(path.stat().st_size for path in bundle_files)
            if len(bundle_files) > _MAX_BUNDLE_FILES:
                raise SoftwareLibraryError(
                    f"软件目录文件过多（{len(bundle_files)}），请选择专用的程序文件夹"
                )
            if bundle_bytes > _MAX_BUNDLE_BYTES:
                raise SoftwareLibraryError(
                    "软件目录超过 4 GB，请选择只包含程序和运行依赖的文件夹"
                )
            shutil.copytree(
                source_root,
                staging / "bin",
                ignore=_ignore_bundle_entry,
            )
            if (entry_root / "templates").is_dir():
                shutil.copytree(entry_root / "templates", staging / "templates")
            else:
                (staging / "templates").mkdir()

            clean_version = str(version or "").strip()
            if len(clean_version) > 80:
                raise SoftwareLibraryError("软件版本不能超过 80 个字符")
            manifest = {
                **old_manifest,
                "schema_version": MANIFEST_VERSION,
                "software_key": key,
                "display_name": str(
                    getattr(adapter, "display_name", key) or key
                ),
                "version": clean_version
                or str(old_manifest.get("version") or ""),
                "executable": f"bin/{relative_executable.as_posix()}",
                "templates": [
                    str(path.relative_to(staging)).replace("\\", "/")
                    for path in sorted((staging / "templates").rglob("*"))
                    if path.is_file()
                ],
                "imported_at": _utc_now(),
                "source_name": source_root.name,
            }
            (staging / MANIFEST_NAME).write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

            if entry_root.exists():
                entry_root.replace(backup)
            try:
                staging.replace(entry_root)
            except Exception:
                if backup.exists() and not entry_root.exists():
                    backup.replace(entry_root)
                raise
            if backup.is_dir():
                shutil.rmtree(backup, ignore_errors=True)
        finally:
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)
        return self.describe(adapter)


def default_software_library() -> SoftwareLibrary:
    return SoftwareLibrary()
