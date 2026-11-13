"""用户新增软件的通用命令行适配器。

用户只需给出显示名称、输入/输出文件名和命令行参数模板，框架即可在
不新增 Python 代码的前提下运行任意支持命令行调用的被测软件。
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
import time
from pathlib import Path

from common.results import (
    environment_summary,
    file_record,
    screen_excerpt,
    utc_now,
)
from common.util_prisma import ROOT, read_config, resolve_path
from software.base import (
    SoftwareAdapterError,
    SoftwareRunRequest,
    SoftwareRunResult,
    resolve_output_path,
)
from software.executables import destination_executable, resolve_executable
from software.library import (
    CUSTOM_ADAPTER_NAME,
    MAX_CUSTOM_INPUTS,
    SoftwareLibrary,
    normalize_support_files,
)
from software.locust_output import extract_value as extract_locust_value


_SAFE_OUTPUT = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_KEY_VALUE = re.compile(r"^\s*([A-Za-z0-9_.\-\[\]#/]+)\s*[:=]\s*(\S.*?)\s*$")


def _leaf_name(value, label: str, default: str) -> str:
    name = str(value or "").strip() or default
    if name in (".", "..") or Path(name).name != name or _SAFE_OUTPUT.search(name):
        raise SoftwareAdapterError(f"{label}必须是文件名，不能包含目录")
    return name


class GenericCommandAdapter:
    """按用户登记的命令行模板运行被测软件的适配器。"""

    status = "available"
    user_defined = True
    requires_input_ok = False
    timeout_config_key = "softwareTimeoutSec"
    adapter_kind = CUSTOM_ADAPTER_NAME

    def __init__(
        self,
        key: str,
        display_name: str,
        *,
        description: str = "",
        version: str = "",
        executable_name: str = "",
        arguments=None,
        input_filename: str = "",
        input_filenames=None,
        support_files=None,
        output_filename: str = "output.txt",
        output_parser: str = "",
        timeout_sec: int = 1800,
        library: SoftwareLibrary | None = None,
    ):
        self.key = key
        self.display_name = display_name
        self.description = description
        self.version = version
        self.executable_name = executable_name
        self.arguments = tuple(str(item) for item in (arguments or ("{input}",)))
        names = [
            str(item).strip() for item in (input_filenames or []) if str(item).strip()
        ]
        if not names:
            primary = str(input_filename or "").strip()
            names = [primary] if primary else ["input.txt"]
        # 去重（不区分大小写），保持登记顺序。
        unique: list[str] = []
        seen: set[str] = set()
        for name in names:
            marker = name.casefold()
            if marker in seen:
                continue
            seen.add(marker)
            unique.append(name)
        #: 输入卡顺序列表，第一项为主输入卡。
        self.input_filenames = tuple(unique)
        #: 主输入卡（兼容单输入卡用法）。
        self.input_filename = self.input_filenames[0]
        #: 默认随 folder_prep 复制的运行依赖，可被剧本第 6 列覆盖。
        self.support_files = tuple(normalize_support_files(support_files))
        self.output_filename = output_filename
        self.output_parser = str(output_parser or "").strip().lower()
        self.timeout_sec = int(timeout_sec or 1800)
        self.library = library or SoftwareLibrary()

    @classmethod
    def from_manifest(
        cls, manifest: dict, library: SoftwareLibrary | None = None
    ) -> "GenericCommandAdapter":
        key = str(manifest.get("software_key") or "").strip().lower()
        arguments = manifest.get("arguments")
        if not isinstance(arguments, (list, tuple)) or not arguments:
            arguments = ("{input}",)
        return cls(
            key=key,
            display_name=str(manifest.get("display_name") or key),
            description=str(manifest.get("description") or ""),
            version=str(manifest.get("version") or ""),
            executable_name=Path(str(manifest.get("executable") or "")).name,
            arguments=arguments,
            input_filename=str(manifest.get("input_filename") or ""),
            input_filenames=manifest.get("input_filenames"),
            support_files=manifest.get("support_files"),
            output_filename=str(manifest.get("output_filename") or "output.txt"),
            output_parser=str(manifest.get("output_parser") or ""),
            timeout_sec=int(manifest.get("timeout_sec") or 1800),
            library=library,
        )

    # ---------- helpers ----------

    @staticmethod
    def _project_root(request: SoftwareRunRequest) -> Path:
        return Path(request.project_root).resolve() if request.project_root else ROOT

    @staticmethod
    def _safe_run_dir(request: SoftwareRunRequest) -> Path:
        root = GenericCommandAdapter._project_root(request)
        run_dir = resolve_path(request.run_dir, root / "test_cases")
        if request.project_root is not None:
            try:
                run_dir.relative_to(root)
            except ValueError as exc:
                raise SoftwareAdapterError("运行目录必须位于当前项目内") from exc
        return run_dir

    def build_command(
        self,
        request: SoftwareRunRequest,
        executable: Path | str | None = None,
    ) -> list[str]:
        program = Path(executable) if executable else Path(self.executable_name or "solver.exe")
        inputs = self.input_names(request)
        values = {
            "input": inputs[0],
            "inputs": " ".join(inputs),
            "output": request.output_name or self.output_filename,
            "exe": program.name,
        }
        for index, name in enumerate(inputs, start=1):
            values[f"input{index}"] = name
        tokens = []
        for token in self.arguments:
            expanded = token
            for name, value in values.items():
                expanded = expanded.replace("{" + name + "}", value)
            if "{" in token:
                # 未登记的第 N 张输入卡占位符按空值处理，整项为空时跳过。
                expanded = re.sub(r"\{input\d{1,2}\}", "", expanded)
                if not expanded.strip():
                    continue
            tokens.append(expanded)
        return [str(program), *tokens]

    def input_names(self, request: SoftwareRunRequest | None = None) -> list[str]:
        """本次运行使用的输入卡顺序；剧本显式指定的输入卡优先作为主输入卡。"""
        names = list(self.input_filenames)
        requested = str(getattr(request, "input_name", "") or "").strip()
        if not requested:
            return names
        marker = requested.casefold()
        for index, name in enumerate(names):
            if name.casefold() != marker:
                continue
            if index == 0:
                return names
            return [name, *names[:index], *names[index + 1:]][:MAX_CUSTOM_INPUTS]
        # 显式输入卡覆盖主输入卡；辅助输入卡仍由软件登记自动带入。
        return [requested, *names[1:]][:MAX_CUSTOM_INPUTS]

    def _input_candidates(
        self, name: str, run_dir: Path, root: Path
    ) -> list[Path]:
        candidates = [
            run_dir / name,
            root / "inputs" / name,
            root / "inputs" / self.key / name,
        ]
        try:
            candidates.extend(
                template
                for template in self.library.template_files(self.key)
                if template.name.casefold() == name.casefold()
            )
        except Exception:  # 软件库缺失时不影响运行现场已存在的输入
            pass
        return list(dict.fromkeys(candidates))

    def missing_inputs(self, request: SoftwareRunRequest) -> list[str]:
        run_dir = self._safe_run_dir(request)
        root = self._project_root(request)
        return [
            name
            for name in self.input_names(request)
            if not any(
                path.is_file()
                for path in self._input_candidates(name, run_dir, root)
            )
        ]

    def stage_inputs(self, request: SoftwareRunRequest) -> list[str]:
        """把输入卡按原文件名放到运行目录，供被测程序直接读取。"""
        run_dir = self._safe_run_dir(request)
        root = self._project_root(request)
        run_dir.mkdir(parents=True, exist_ok=True)
        staged: list[str] = []
        for name in self.input_names(request):
            source = next(
                (
                    path
                    for path in self._input_candidates(name, run_dir, root)
                    if path.is_file()
                ),
                None,
            )
            if source is None:
                continue
            destination = run_dir / name
            if source.resolve() != destination.resolve():
                shutil.copy2(source, destination)
            staged.append(name)
        return staged

    def prepare(self, request: SoftwareRunRequest) -> None:
        missing = self.missing_inputs(request)
        if missing:
            raise SoftwareAdapterError(
                f"未找到输入文件 {'、'.join(missing)}；"
                "请放入项目 inputs 目录或运行目录"
            )

    def _resolve_program(
        self, request: SoftwareRunRequest
    ) -> tuple[Path, Path]:
        root = self._project_root(request)
        name = str(request.executable or self.executable_name or "").strip()
        executable = (
            resolve_executable(
                name,
                request.run_dir,
                self.key,
                project_root=root,
                library=self.library,
            )
            if name
            else None
        )
        if executable is None:
            executable = self.library.resolve_executable(self.key)
        if executable is None:
            raise SoftwareAdapterError(
                f"未找到 {self.display_name} 的可运行程序；"
                "请先在软件库中导入可执行程序"
                "（Windows 为 .exe，Linux 为 ELF）"
            )
        destination = destination_executable(
            executable,
            request.run_dir,
            self.key,
            project_root=root,
            library=self.library,
        )
        return executable, destination

    def analyze_output(self, output_path) -> dict:
        path = Path(output_path)
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise SoftwareAdapterError(f"输出文件无法读取: {path}") from exc
        values: dict[str, str] = {}
        for line in text.splitlines():
            match = _KEY_VALUE.match(line)
            if match:
                values.setdefault(match.group(1), match.group(2))
        return {"pairs": len(values), "keys": sorted(values)[:200]}

    def extract_value(self, text: str, key: str):
        if self.output_parser:
            if self.output_parser == "locust":
                return extract_locust_value(text, key)
            raise SoftwareAdapterError(
                f"未支持的输出解析器: {self.output_parser}"
            )
        wanted = str(key or "").strip()
        if not wanted:
            return None
        pattern = re.compile(
            rf"^\s*{re.escape(wanted)}\s*[:=]\s*(\S.*?)\s*$", re.I
        )
        for line in str(text or "").splitlines():
            match = pattern.match(line)
            if match:
                return match.group(1)
        return None

    def _result_evidence(
        self,
        *,
        request: SoftwareRunRequest,
        output_path: Path,
        mode: str,
        started_at: str,
        started_clock: float,
        returncode: int | None = None,
        analysis: dict | None = None,
        executable_source: Path | None = None,
        executable_path: Path | None = None,
        input_path: Path | None = None,
        command: tuple[str, ...] = (),
    ) -> SoftwareRunResult:
        run_dir = self._safe_run_dir(request)
        screen_path = run_dir / "screen.txt"
        output_files = []
        seen: set[str] = set()
        for path, role in (
            (output_path, "output"),
            (screen_path, "screen"),
        ):
            record = file_record(path, base=run_dir, role=role)
            if not record or record["absolute_path"] in seen:
                continue
            seen.add(record["absolute_path"])
            output_files.append(record)
        return SoftwareRunResult(
            output_path=output_path,
            returncode=returncode,
            analysis=analysis,
            software_type=self.key,
            software_version=self.version,
            mode=mode,
            executable_source=executable_source,
            executable_path=executable_path,
            input_path=input_path,
            command=command,
            started_at=started_at,
            finished_at=utc_now(),
            duration_seconds=max(0.0, time.monotonic() - started_clock),
            environment=environment_summary(),
            output_files=tuple(output_files),
            screen_path=screen_path if screen_path.is_file() else None,
            screen_excerpt=screen_excerpt(screen_path),
        )

    def execute(self, request: SoftwareRunRequest) -> SoftwareRunResult:
        logger = logging.getLogger("prisma")
        root = self._project_root(request)
        run_dir = self._safe_run_dir(request)
        run_dir.mkdir(parents=True, exist_ok=True)
        started_at = utc_now()
        started_clock = time.monotonic()
        output_path = resolve_output_path(
            run_dir,
            request.output_name,
            self.output_filename,
            label="输出文件名",
        )
        output_name = output_path.relative_to(run_dir.resolve()).as_posix()
        input_name = _leaf_name(
            request.input_name or self.input_filename,
            "输入文件名",
            self.input_filename,
        )
        request = SoftwareRunRequest(
            case_no=request.case_no,
            run_dir=request.run_dir,
            executable=request.executable,
            software_type=request.software_type,
            input_name=input_name,
            output_name=output_name,
            timeout=request.timeout,
            params=request.params,
            project_root=request.project_root,
            config_path=request.config_path,
        )
        config = read_config(request.config_path)
        mode = str(config.get("runMode", "simulate")).lower()
        if mode != "real":
            output_path.write_text(
                f"# simulated {self.display_name} run\nstatus: simulate\n",
                encoding="utf-8",
            )
            logger.info(
                "[%s] %s(simulate) -> %s", request.case_no, self.key, output_path
            )
            input_path = run_dir / input_name
            return self._result_evidence(
                request=request,
                output_path=output_path,
                mode="simulate",
                started_at=started_at,
                started_clock=started_clock,
                analysis={"pairs": 0, "keys": []},
                input_path=input_path if input_path.is_file() else None,
            )

        self.prepare(request)
        self.stage_inputs(request)
        executable, destination = self._resolve_program(request)
        command = self.build_command(request, destination)
        logger.info(
            "[%s] %s executable=%s command=%s",
            request.case_no, self.key, executable, command,
        )
        if output_path.is_file():
            output_path.unlink()
        timeout_sec = int(
            request.timeout or config.get(self.timeout_config_key, self.timeout_sec)
            or self.timeout_sec
        )
        try:
            with (run_dir / "screen.txt").open(
                "w", encoding="utf-8", errors="replace"
            ) as screen:
                process = subprocess.run(
                    command,
                    cwd=run_dir,
                    stdout=screen,
                    stderr=subprocess.STDOUT,
                    timeout=timeout_sec,
                    check=False,
                )
        except subprocess.TimeoutExpired as exc:
            raise SoftwareAdapterError(
                f"{self.display_name} 超时（{timeout_sec} 秒）"
            ) from exc
        except OSError as exc:
            raise SoftwareAdapterError(
                f"{self.display_name} 无法启动: {exc}"
            ) from exc

        if not output_path.is_file():
            hint = "" if process.returncode == 0 else f"，返回码 {process.returncode}"
            raise SoftwareAdapterError(
                f"{self.display_name} 未生成输出文件 {output_name}{hint}；"
                "请查看运行目录中的 screen.txt"
            )
        analysis = self.analyze_output(output_path)
        logger.info(
            "[%s] %s returncode=%s -> %s",
            request.case_no, self.key, process.returncode, output_path,
        )
        return self._result_evidence(
            request=request,
            output_path=output_path,
            mode="real",
            started_at=started_at,
            started_clock=started_clock,
            returncode=process.returncode,
            analysis=analysis,
            executable_source=executable,
            executable_path=destination,
            input_path=run_dir / input_name,
            command=tuple(str(item) for item in command),
        )
